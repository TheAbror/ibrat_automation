"""Keep the runner alive: device, Appium server, and worker process.

python3 main.py            -> this supervisor
python3 main.py --worker   -> the actual runner (main.main)

The supervisor resolves the device (the pinned Wi-Fi target when online,
a USB-cable serial otherwise), makes sure an Appium server is answering,
then spawns the worker and babysits it: a worker that dies or goes
silent is killed, Appium is restarted, and the worker is respawned —
forever, with a growing delay while nothing is making progress.
"""
import http.client
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import urlsplit

import config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DELAYS = (5, 15, 30, 60, 120, 300)  # respawn delay ladder, seconds
RESET_AFTER = 60      # a worker alive this long resets the ladder
SILENCE_LIMIT = 300   # no worker output for this long = hung
KILL_GRACE = 10       # seconds between SIGTERM and SIGKILL
APPIUM_LOG = "appium.log"
APPIUM_LOG_PATH = os.path.join(BASE_DIR, APPIUM_LOG)
APPIUM_WAIT = 30      # seconds for a fresh server to answer /status


def pick_device(adb_output, pinned):
    """Serial to use, from `adb devices` output.

    The pinned Wi-Fi target when online, else the first USB serial (no
    ':' port suffix — the phone on a cable), else the first other online
    device. None when nothing usable is attached.
    """
    online = []
    for line in adb_output.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == "device":
            online.append(parts[0])
    if pinned in online:
        return pinned
    for serial in online:
        if ":" not in serial:
            return serial
    return next((s for s in online if s != pinned), None)


def update_streak(streak, lived_seconds):
    return 1 if lived_seconds >= RESET_AFTER else streak + 1


def respawn_delay(streak):
    if streak <= 0:
        return 0
    return DELAYS[min(streak, len(DELAYS)) - 1]


def should_respawn(exit_code):
    # 0 = course done; 130 / -SIGINT = the user stopped it.
    return exit_code not in (0, 130, -signal.SIGINT)


def worker_is_hung(last_output, now):
    return now - last_output > SILENCE_LIMIT


def _adb(*args, timeout=15):
    try:
        return subprocess.run(["adb", *args], capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def ensure_device():
    """Serial of an online device, reconnecting the Wi-Fi target once."""
    pinned = config.DEVICE_NAME
    out = _adb("devices")
    device = pick_device(out.stdout if out else "", pinned)
    if device:
        return device
    _adb("connect", pinned)
    out = _adb("devices")
    return pick_device(out.stdout if out else "", pinned)


def appium_alive():
    try:
        with urllib.request.urlopen(config.APPIUM_SERVER + "/status",
                                    timeout=3) as resp:
            return resp.status == 200
    except (OSError, http.client.HTTPException):
        return False


def _kill_port_owner():
    port = str(urlsplit(config.APPIUM_SERVER).port or 4723)
    listed = None
    try:
        listed = subprocess.run(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return
    for pid in (listed.stdout or "").split():
        subprocess.run(["kill", "-9", pid], capture_output=True)


def restart_appium(proc=None):
    """Stop whatever Appium is around (ours or not) and start a fresh one.

    Returns the new process handle, or None when the server never came
    up — the caller's backoff loop retries.
    """
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=KILL_GRACE)
        except subprocess.TimeoutExpired:
            proc.kill()
    _kill_port_owner()
    time.sleep(1)
    print(f"[supervisor] starting appium (output -> {APPIUM_LOG})")
    try:
        log = open(APPIUM_LOG_PATH, "a")
        fresh = subprocess.Popen(["appium"], stdout=log,
                                 stderr=subprocess.STDOUT)
    except OSError as e:
        print(f"[supervisor] could not start appium: {e}")
        return None
    end = time.time() + APPIUM_WAIT
    while time.time() < end:
        if appium_alive():
            return fresh
        if fresh.poll() is not None:
            break
        time.sleep(1)
    print(f"[supervisor] appium did not come up — see {APPIUM_LOG}")
    return fresh


def spawn_worker(device, worker_cmd=None):
    cmd = worker_cmd or [sys.executable, "-u", "main.py", "--worker"]
    env = dict(os.environ, IBRAT_DEVICE=device)
    return subprocess.Popen(cmd, env=env, cwd=BASE_DIR, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, encoding="utf-8",
                            errors="replace")


def _pump(child, last_output):
    for line in child.stdout:
        print(line, end="")
        last_output[0] = time.time()


def babysit(child):
    """Echo the worker's output until it exits; kill it when it hangs."""
    last_output = [time.time()]
    threading.Thread(target=_pump, args=(child, last_output),
                     daemon=True).start()
    while child.poll() is None:
        if worker_is_hung(last_output[0], time.time()):
            print(f"[supervisor] no worker output for {SILENCE_LIMIT}s "
                  "— killing it")
            child.terminate()
            try:
                child.wait(timeout=KILL_GRACE)
            except subprocess.TimeoutExpired:
                child.kill()
            break
        time.sleep(1)
    return child.wait()


def _raise_keyboard_interrupt(signum, frame):
    raise KeyboardInterrupt


def run(worker_cmd=None):
    # A bare `kill <pid>` (SIGTERM) would otherwise skip straight past
    # this function, orphaning the worker and any Appium we own — route
    # it through the same graceful-stop path as Ctrl+C.
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    appium_proc, owned, streak = None, False, 0
    child = None
    run_started = time.time()
    try:
        while True:
            device = ensure_device()
            if device is None:
                streak = update_streak(streak, 0)
                delay = respawn_delay(streak)
                print("[supervisor] no device online (Wi-Fi or cable) — "
                      f"retrying in {delay}s")
                time.sleep(delay)
                continue
            if not appium_alive():
                appium_proc = restart_appium(appium_proc if owned else None)
                owned = True
                if not appium_alive():
                    streak = update_streak(streak, 0)
                    delay = respawn_delay(streak)
                    print(f"[supervisor] appium is not answering — "
                          f"retrying in {delay}s")
                    time.sleep(delay)
                    continue
            print(f"[supervisor] device {device}, appium up — "
                  "starting the runner")
            started = time.time()
            child = spawn_worker(device, worker_cmd)
            code = babysit(child)
            child = None
            lived = time.time() - started
            if not should_respawn(code):
                print(f"[supervisor] runner finished (exit {code})")
                return code
            streak = update_streak(streak, lived)
            delay = respawn_delay(streak)
            print(f"[supervisor] runner died (exit {code}) after "
                  f"{lived:.0f}s — restarting appium, relaunching in {delay}s")
            appium_proc = restart_appium(appium_proc if owned else None)
            owned = True
            time.sleep(delay)
    except KeyboardInterrupt:
        # Ctrl+C reaches the worker too (same process group), but a
        # SIGTERM to just this process does not — terminate it directly
        # (harmless if it is already on its way out) and give it a
        # moment to save its session before making sure it is gone.
        print("\n[supervisor] stopped by user")
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=KILL_GRACE)
            except subprocess.TimeoutExpired:
                child.kill()
        return 130
    finally:
        # The workers' own wall-time lines reset on every respawn; this
        # one covers the whole supervised run.
        elapsed = time.time() - run_started
        print(f"[supervisor] total run time: {elapsed / 60:.1f} minutes "
              f"({elapsed / 3600:.2f} hours)")
        # Only a server the supervisor itself started is shut down.
        if owned and appium_proc is not None and appium_proc.poll() is None:
            appium_proc.terminate()
