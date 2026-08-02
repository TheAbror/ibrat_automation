# Self-Healing Supervisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `python3 main.py` alone runs the course unattended, starting/restarting Appium, resolving the device over Wi-Fi or USB cable, and reviving a crashed or hung runner forever.

**Architecture:** `main.py` becomes a dispatcher: no args → a new `supervisor.py` module (device resolution, Appium lifecycle, worker babysitting with a silence watchdog and a backoff ladder); `--worker` → today's runner unchanged except explicit exit codes. The supervisor is an outer layer; all in-worker recovery stays as is.

**Tech Stack:** Python 3 stdlib only (`subprocess`, `threading`, `urllib.request`); unittest in the existing `test_watcher.py` style.

**Spec:** `docs/superpowers/specs/2026-08-03-self-healing-supervisor-design.md`

## Global Constraints

- No new dependencies; stdlib only for the supervisor.
- Pinned Wi-Fi device default stays `"192.168.1.16:5555"`; Appium URL comes from `config.APPIUM_SERVER` (`http://127.0.0.1:4723`).
- Supervisor constants (exact values): `DELAYS = (5, 15, 30, 60, 120, 300)`, `RESET_AFTER = 60`, `SILENCE_LIMIT = 300`, `KILL_GRACE = 10`, `APPIUM_WAIT = 30`, `APPIUM_LOG = "appium.log"`.
- Worker exit codes: `0` course done, `1` gave up, `130` Ctrl+C.
- **A real automation run may be live on the phone right now.** Tests and smoke checks must NEVER spawn the real worker, kill the real Appium server, or send commands to the device. Smokes patch `ensure_device`, `appium_alive`, and `restart_appium` and use fake worker subprocesses only.
- Tests run with `python3 -m unittest test_watcher -v` from the repo root; all new tests live in `test_watcher.py` (single test file convention).
- Commit after each task; never `git push`.

---

### Task 1: Device override in config.py

**Files:**
- Modify: `config.py:1-7`
- Test: `test_watcher.py` (append)

**Interfaces:**
- Produces: `config.DEVICE_NAME` — now `os.environ.get("IBRAT_DEVICE") or "192.168.1.16:5555"`. Everything that imports `config` (watcher.py, main.py adb helpers) picks the override up automatically.

- [ ] **Step 1: Write the failing test** (append to `test_watcher.py`)

```python
class TestConfigDeviceOverride(unittest.TestCase):
    def test_env_var_overrides_pinned_device(self):
        import importlib
        import config
        os.environ["IBRAT_DEVICE"] = "ZY22GTXB9R"
        try:
            importlib.reload(config)
            self.assertEqual(config.DEVICE_NAME, "ZY22GTXB9R")
        finally:
            del os.environ["IBRAT_DEVICE"]
            importlib.reload(config)
        self.assertEqual(config.DEVICE_NAME, "192.168.1.16:5555")
```

(`os` is already imported at the top of `test_watcher.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest test_watcher.TestConfigDeviceOverride -v`
Expected: FAIL — `config.DEVICE_NAME` stays `"192.168.1.16:5555"` under the env var.

- [ ] **Step 3: Implement** — in `config.py`, add `import os` at the top and replace the `DEVICE_NAME` line:

```python
import os

# Wi-Fi adb target (USB serial was "ZY22GTXB9R"). After a phone reboot,
# plug in the cable once and run ./wifi_adb.sh to restore this connection.
# The supervisor overrides this per-run (IBRAT_DEVICE) when the phone is
# reachable over a USB cable instead of the Wi-Fi target.
DEVICE_NAME = os.environ.get("IBRAT_DEVICE") or "192.168.1.16:5555"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest test_watcher.TestConfigDeviceOverride -v` — PASS.

- [ ] **Step 5: Commit**

```bash
git add config.py test_watcher.py
git commit -m "Let IBRAT_DEVICE override the pinned adb target"
```

---

### Task 2: Worker mode and exit codes in main.py

**Files:**
- Modify: `main.py:20` (imports), `main.py:505-555` (`main()` and the `__main__` guard)
- Test: `test_watcher.py` (append)

**Interfaces:**
- Consumes: `navigation.StuckScreenError` (already imported in main.py).
- Produces: `main.main() -> int` (0 done / 1 gave up / 130 Ctrl+C); `python3 main.py --worker` runs `main()`; `python3 main.py` (no flag) runs `supervisor.run()` — `supervisor` is created in Task 3/4, so the no-flag path will not be exercised until then (import happens inside the guard, so nothing else breaks).

- [ ] **Step 1: Write the failing tests** (append to `test_watcher.py`; `FakeTime` is defined near the top of the file)

```python
class QuietDriver:
    def quit(self):
        pass


class TestWorkerExitCodes(unittest.TestCase):
    def setUp(self):
        import main as main_mod
        self.m = main_mod
        self._saved = (main_mod.wake_device, main_mod.force_stop_app,
                       main_mod.connect_fresh_session, main_mod.navigate_to_test,
                       main_mod.answer_until_done, main_mod.APP_RELAUNCHES,
                       main_mod.time)
        main_mod.wake_device = lambda: True
        main_mod.force_stop_app = lambda: True
        main_mod.connect_fresh_session = lambda: QuietDriver()
        main_mod.APP_RELAUNCHES = 0
        main_mod.time = FakeTime()

    def tearDown(self):
        (self.m.wake_device, self.m.force_stop_app, self.m.connect_fresh_session,
         self.m.navigate_to_test, self.m.answer_until_done, self.m.APP_RELAUNCHES,
         self.m.time) = self._saved

    def test_course_completion_exits_zero(self):
        self.m.navigate_to_test = lambda *a: True
        self.m.answer_until_done = lambda d: None
        self.assertEqual(self.m.main(), 0)

    def test_giving_up_exits_one(self):
        from navigation import StuckScreenError

        def stuck(*a):
            raise StuckScreenError("stranded")
        self.m.navigate_to_test = stuck
        self.assertEqual(self.m.main(), 1)

    def test_navigation_failure_is_a_stuck_screen_not_success(self):
        # navigate_to_test returning False used to end the run with a
        # quiet success; under the supervisor exit 0 means "course done,
        # stop everything", so a failed navigation must be a give-up.
        self.m.navigate_to_test = lambda *a: False
        self.assertEqual(self.m.main(), 1)

    def test_ctrl_c_exits_130(self):
        def interrupted(*a):
            raise KeyboardInterrupt
        self.m.navigate_to_test = interrupted
        self.assertEqual(self.m.main(), 130)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_watcher.TestWorkerExitCodes -v`
Expected: FAIL — `main()` currently returns `None` on every path.

- [ ] **Step 3: Implement.** In `main.py` add `import sys` next to `import subprocess`. Replace `main()` and the `__main__` guard (currently lines 505-555) with:

```python
def main():
    started = time.time()
    relaunches = APP_RELAUNCHES
    try:
        while True:
            driver = None

            # Always close the session: an orphaned session wedges the
            # Appium server and breaks the next script that talks to the
            # same device.
            try:
                wake_device()
                force_stop_app()
                driver = connect_fresh_session()
                time.sleep(3)

                wait = WebDriverWait(driver, 20)
                wait_long = WebDriverWait(driver, 30)
                if navigate_to_test(driver, wait, wait_long):
                    answer_until_done(driver)
                    return 0
                # Navigation dead ends are retried like stuck screens —
                # under the supervisor exit 0 means "course done".
                raise StuckScreenError("navigation never reached the question screen")
            except (AppLostError, StuckScreenError) + watcher.CONNECTION_ERRORS as e:
                if relaunches == 0:
                    print("Still stuck after several app restarts — giving up.")
                    return 1
                relaunches -= 1
                if isinstance(e, AppLostError):
                    reason = "left the foreground"
                elif isinstance(e, StuckScreenError):
                    reason = "is stuck"
                else:
                    # e.g. the device-side instrumentation died
                    # mid-navigation (another runner on the same phone
                    # restarts it too).
                    reason = f"lost the device connection ({type(e).__name__})"
                    clear_stale_instrumentation()
                print(f"The app {reason} ({e}) — restarting it...")
            except KeyboardInterrupt:
                print("\nStopped by user.")
                return 130
            finally:
                if driver is not None:
                    watcher.safe_quit(driver)
    finally:
        elapsed = time.time() - started
        print(f"Total wall time: {elapsed / 60:.1f} minutes "
              f"({elapsed / 3600:.2f} hours)")


if __name__ == "__main__":
    if "--worker" in sys.argv:
        sys.exit(main())
    import supervisor
    sys.exit(supervisor.run())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_watcher.TestWorkerExitCodes -v` — PASS.
Also run the whole suite (`python3 -m unittest test_watcher -v`) — the existing `TestTapPair`/`TestAnswerMatchingFlow` tests import `main` and must still pass.

- [ ] **Step 5: Commit**

```bash
git add main.py test_watcher.py
git commit -m "Give the runner a worker mode with explicit exit codes"
```

---

### Task 3: Supervisor policy functions

**Files:**
- Create: `supervisor.py`
- Test: `test_watcher.py` (append)

**Interfaces:**
- Consumes: `config.APPIUM_SERVER`, `config.DEVICE_NAME`.
- Produces (used by Task 4's loop and its tests):
  - `pick_device(adb_output: str, pinned: str) -> str | None`
  - `update_streak(streak: int, lived_seconds: float) -> int`
  - `respawn_delay(streak: int) -> int`
  - `should_respawn(exit_code: int) -> bool`
  - `worker_is_hung(last_output: float, now: float) -> bool`
  - constants `DELAYS`, `RESET_AFTER`, `SILENCE_LIMIT`, `KILL_GRACE`, `APPIUM_WAIT`, `APPIUM_LOG`

- [ ] **Step 1: Write the failing tests** (append to `test_watcher.py`)

```python
ADB_HEADER = "List of devices attached\n"


class TestSupervisorPolicy(unittest.TestCase):
    PINNED = "192.168.1.16:5555"

    def test_pinned_wifi_target_wins_when_online(self):
        import supervisor
        out = ADB_HEADER + "ZY22GTXB9R\tdevice\n192.168.1.16:5555\tdevice\n\n"
        self.assertEqual(supervisor.pick_device(out, self.PINNED), self.PINNED)

    def test_usb_serial_used_when_wifi_is_gone(self):
        import supervisor
        out = ADB_HEADER + "ZY22GTXB9R\tdevice\n\n"
        self.assertEqual(supervisor.pick_device(out, self.PINNED), "ZY22GTXB9R")

    def test_usb_preferred_over_another_network_serial(self):
        import supervisor
        out = ADB_HEADER + "192.168.1.99:5555\tdevice\nZY22GTXB9R\tdevice\n\n"
        self.assertEqual(supervisor.pick_device(out, self.PINNED), "ZY22GTXB9R")

    def test_other_network_serial_used_as_last_resort(self):
        import supervisor
        out = ADB_HEADER + "192.168.1.99:5555\tdevice\n\n"
        self.assertEqual(supervisor.pick_device(out, self.PINNED),
                         "192.168.1.99:5555")

    def test_offline_and_unauthorized_devices_are_ignored(self):
        import supervisor
        out = ADB_HEADER + "192.168.1.16:5555\toffline\nZY22GTXB9R\tunauthorized\n\n"
        self.assertIsNone(supervisor.pick_device(out, self.PINNED))

    def test_no_devices_returns_none(self):
        import supervisor
        self.assertIsNone(supervisor.pick_device(ADB_HEADER + "\n", self.PINNED))

    def test_respawn_delay_ladder_caps_at_five_minutes(self):
        import supervisor
        delays = [supervisor.respawn_delay(s) for s in range(1, 9)]
        self.assertEqual(delays, [5, 15, 30, 60, 120, 300, 300, 300])
        self.assertEqual(supervisor.respawn_delay(0), 0)

    def test_streak_resets_after_a_long_lived_worker(self):
        import supervisor
        self.assertEqual(supervisor.update_streak(4, lived_seconds=3600), 1)
        self.assertEqual(supervisor.update_streak(1, lived_seconds=5), 2)

    def test_exit_codes_decide_respawn(self):
        import signal
        import supervisor
        self.assertFalse(supervisor.should_respawn(0))    # course done
        self.assertFalse(supervisor.should_respawn(130))  # worker saw Ctrl+C
        self.assertFalse(supervisor.should_respawn(-signal.SIGINT))
        self.assertTrue(supervisor.should_respawn(1))     # worker gave up
        self.assertTrue(supervisor.should_respawn(-9))    # killed while hung

    def test_silence_detection_uses_the_limit(self):
        import supervisor
        limit = supervisor.SILENCE_LIMIT
        self.assertFalse(supervisor.worker_is_hung(1000.0, 1000.0 + limit))
        self.assertTrue(supervisor.worker_is_hung(1000.0, 1000.0 + limit + 1))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest test_watcher.TestSupervisorPolicy -v`
Expected: ERROR — `ModuleNotFoundError: No module named 'supervisor'`.

- [ ] **Step 3: Create `supervisor.py`** with the module docstring, constants, and the pure policy half (the plumbing half lands in Task 4):

```python
"""Keep the runner alive: device, Appium server, and worker process.

python3 main.py            -> this supervisor
python3 main.py --worker   -> the actual runner (main.main)

The supervisor resolves the device (the pinned Wi-Fi target when online,
a USB-cable serial otherwise), makes sure an Appium server is answering,
then spawns the worker and babysits it: a worker that dies or goes
silent is killed, Appium is restarted, and the worker is respawned —
forever, with a growing delay while nothing is making progress.
"""
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from urllib.parse import urlsplit

import config

DELAYS = (5, 15, 30, 60, 120, 300)  # respawn delay ladder, seconds
RESET_AFTER = 60      # a worker alive this long resets the ladder
SILENCE_LIMIT = 300   # no worker output for this long = hung
KILL_GRACE = 10       # seconds between SIGTERM and SIGKILL
APPIUM_LOG = "appium.log"
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest test_watcher.TestSupervisorPolicy -v` — PASS.

- [ ] **Step 5: Commit**

```bash
git add supervisor.py test_watcher.py
git commit -m "Add the supervisor's device, backoff, and restart policy"
```

---

### Task 4: Supervisor plumbing and the run loop

**Files:**
- Modify: `supervisor.py` (append below the policy functions)

**Interfaces:**
- Consumes: everything from Task 3; `config.DEVICE_NAME`, `config.APPIUM_SERVER`.
- Produces: `run(worker_cmd=None) -> int` — called by `main.py`'s no-flag path (already wired in Task 2). `worker_cmd` overrides the spawned command (used by smoke checks only).

- [ ] **Step 1: Append the plumbing to `supervisor.py`:**

```python
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
    except OSError:
        return False


def _kill_port_owner():
    port = str(urlsplit(config.APPIUM_SERVER).port or 4723)
    listed = None
    try:
        listed = subprocess.run(["lsof", "-ti", f":{port}"],
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
    log = open(APPIUM_LOG, "a")
    try:
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
    return subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)


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


def run(worker_cmd=None):
    appium_proc, owned, streak = None, False, 0
    child = None
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
        # Ctrl+C reached the worker too (same process group); give it a
        # moment to save its session, then make sure it is gone.
        print("\n[supervisor] stopped by user")
        if child is not None and child.poll() is None:
            try:
                child.wait(timeout=KILL_GRACE)
            except subprocess.TimeoutExpired:
                child.kill()
        return 130
    finally:
        # Only a server the supervisor itself started is shut down.
        if owned and appium_proc is not None and appium_proc.poll() is None:
            appium_proc.terminate()
```

- [ ] **Step 2: Compile check and full suite**

Run: `python3 -m py_compile supervisor.py main.py config.py && python3 -m unittest test_watcher -v`
Expected: everything passes.

- [ ] **Step 3: Hermetic smoke — clean finish.** No device, Appium, or real worker is touched: `ensure_device`, `appium_alive`, and `restart_appium` are patched. Run from the repo root:

```bash
python3 - <<'EOF'
import supervisor
supervisor.ensure_device = lambda: "smoke-device"
supervisor.appium_alive = lambda: True
supervisor.restart_appium = lambda proc=None: None
code = supervisor.run(worker_cmd=[
    "python3", "-u", "-c", "print('fake worker ran'); raise SystemExit(0)"])
assert code == 0, code
print("clean-finish smoke OK")
EOF
```

Expected output includes `fake worker ran`, `runner finished (exit 0)`, `clean-finish smoke OK`.

- [ ] **Step 4: Hermetic smoke — die once, then respawn and finish.** Verifies the respawn decision, the backoff call, and that `restart_appium` is invoked on failure:

```bash
python3 - <<'EOF'
import os, supervisor
supervisor.DELAYS = (0, 0, 0, 0, 0, 0)
supervisor.ensure_device = lambda: "smoke-device"
supervisor.appium_alive = lambda: True
restarts = []
supervisor.restart_appium = lambda proc=None: restarts.append(1)
flag = "smoke-worker-flag"
if os.path.exists(flag):
    os.remove(flag)
script = (
    "import os, sys\n"
    f"flag = {flag!r}\n"
    "if os.path.exists(flag):\n"
    "    print('fake worker: second run, finishing'); sys.exit(0)\n"
    "open(flag, 'w').close()\n"
    "print('fake worker: first run, dying'); sys.exit(1)\n"
)
code = supervisor.run(worker_cmd=["python3", "-u", "-c", script])
os.remove(flag)
assert code == 0 and len(restarts) == 1, (code, restarts)
print("respawn smoke OK")
EOF
```

Expected: both fake-worker lines, one `runner died (exit 1)` line, `respawn smoke OK`.

- [ ] **Step 5: Hermetic smoke — hung worker is killed and replaced.** Same patches plus a tiny silence limit:

```bash
python3 - <<'EOF'
import os, supervisor
supervisor.DELAYS = (0, 0, 0, 0, 0, 0)
supervisor.SILENCE_LIMIT = 2
supervisor.KILL_GRACE = 2
supervisor.ensure_device = lambda: "smoke-device"
supervisor.appium_alive = lambda: True
supervisor.restart_appium = lambda proc=None: None
flag = "smoke-hang-flag"
if os.path.exists(flag):
    os.remove(flag)
script = (
    "import os, sys, time\n"
    f"flag = {flag!r}\n"
    "if os.path.exists(flag):\n"
    "    print('fake worker: revived, finishing'); sys.exit(0)\n"
    "open(flag, 'w').close()\n"
    "print('fake worker: hanging now'); time.sleep(600)\n"
)
code = supervisor.run(worker_cmd=["python3", "-u", "-c", script])
os.remove(flag)
assert code == 0, code
print("hang smoke OK")
EOF
```

Expected: `hanging now`, then the `no worker output ... killing it` line within ~3-5 s, then `revived, finishing`, `hang smoke OK`.

- [ ] **Step 6: Commit**

```bash
git add supervisor.py
git commit -m "Babysit the runner: appium lifecycle, watchdog, respawns"
```

---

### Task 5: Documentation and final verification

**Files:**
- Modify: `HOW_TO_RUN.md` (sections 1-3 and the offline section; the video/promo/stuck sections stay)

**Interfaces:** none — docs only.

- [ ] **Step 1: Rewrite the top of `HOW_TO_RUN.md`.** Replace everything from the title through the end of section "## 3. Run the automation" (keeping the "Only one runner at a time" warning) with:

```markdown
# How to run the automation

One command runs everything:

```bash
cd ~/Desktop/ibrat-automation
python3 main.py
```

That command supervises the whole stack by itself:

- **finds the phone** — the Wi-Fi adb target (`192.168.1.16:5555`) when it
  is online (running `adb connect` itself), otherwise a phone plugged in
  through a USB cable;
- **runs the Appium server** — starts one if none is answering (output
  goes to `appium.log`) and restarts it whenever the runner loses it;
- **keeps the runner alive** — if the runner crashes, gives up, or prints
  nothing for 5 minutes, it is killed, Appium is restarted, and the run
  resumes. It never gives up: while the phone is unreachable it retries
  with a growing pause (5 s up to 5 min) and picks the run back up the
  moment the phone returns.

Stop everything with `Ctrl+C`.

The bare runner is still available the old way — start `appium` in one
terminal yourself, then `python3 main.py --worker` in another.

**Only one runner at a time.** Two runners on the same phone kill each
other: every new session restarts the device-side automation server,
which drops the other runner's connection. The supervisor spawns exactly
one worker, but a second `python3 main.py` (or `watcher.py`) started by
hand has the same effect. If runs keep dying with "instrumentation
process is not running", check for a second runner still going.
```

- [ ] **Step 2: Update the offline section.** In "## If the phone is offline", add one sentence at the top:

```markdown
If the phone fell off Wi-Fi but a USB cable is plugged in, nothing needs
restoring — `python3 main.py` finds and uses the cable connection by
itself. To get back on Wi-Fi adb:
```

(the existing numbered steps stay).

- [ ] **Step 3: Full verification**

Run: `python3 -m unittest test_watcher -v && python3 -m py_compile supervisor.py main.py config.py watcher.py`
Expected: whole suite green.

- [ ] **Step 4: Commit**

```bash
git add HOW_TO_RUN.md
git commit -m "Document the one-command self-healing run"
```
