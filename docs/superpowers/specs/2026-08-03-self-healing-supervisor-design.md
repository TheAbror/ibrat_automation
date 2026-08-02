# Self-healing runner: supervisor + worker

**Date:** 2026-08-03
**Goal:** `python3 main.py` alone runs the course unattended and recovers from
every failure a human babysitter handles today: a dead or wedged Appium
server, a dropped adb connection (Wi-Fi or USB), a crashed runner, and a
hard-hung runner process. It works whether the phone is on Wi-Fi adb or
plugged in through a USB cable.

## Architecture

Two modes in one entry point:

- `python3 main.py` — **supervisor**: resolves the device, ensures Appium is
  up, spawns the worker, babysits it, restarts pieces as needed. New logic
  lives in a new `supervisor.py` module; `main.py` only dispatches.
- `python3 main.py --worker` — **worker**: exactly today's runner
  (`main.main()`), unchanged except for explicit exit codes.

The supervisor is an outer layer. All existing in-worker recovery (app
restarts up to `APP_RELAUNCHES`, session reconnects, stale-instrumentation
clearing, stuck-screen dumps, device wake) stays as is.

Only the worker talks to the phone, so the "one runner at a time" rule is
preserved.

## Device resolution (Wi-Fi or cable)

Before each worker spawn the supervisor picks a device serial:

1. If the pinned Wi-Fi target (`config.DEVICE_NAME`, `192.168.1.16:5555`)
   is online in `adb devices` → use it.
2. Otherwise run `adb connect <target>` once and recheck.
3. Otherwise, if any other device is online (the phone on a USB cable) →
   use that serial. If several are online, prefer a USB serial (one
   without a `:` port suffix) and take the first listed.
4. Otherwise no device: enter the retry/backoff loop.

The chosen serial is passed to the worker in the `IBRAT_DEVICE` env var.
`config.py` reads it with the current pinned value as fallback:

```python
DEVICE_NAME = os.environ.get("IBRAT_DEVICE") or "192.168.1.16:5555"
```

so `watcher.py`, manual `--worker` runs, and every existing adb helper keep
working unchanged, and a cable-connected phone works with zero config edits.

## Appium lifecycle

- The supervisor probes `GET {config.APPIUM_SERVER}/status` with a short
  timeout.
- Down → start `appium` as a subprocess, output appended to `appium.log`,
  wait up to ~30 s for `/status` to answer.
- Already running (e.g. the user's own terminal) → reuse it. The first time
  a restart is required, kill whatever holds the port (port parsed from
  `config.APPIUM_SERVER`) and own the server from then on.
- If the supervisor started Appium, it shuts it down when the supervisor
  exits.

## Watchdog, restarts, backoff

The worker is spawned with unbuffered stdout (`PYTHONUNBUFFERED=1`); the
supervisor echoes its output live — the console looks exactly like today —
and records the time of the last line.

Recovery triggers:

- worker exits non-zero (give-up paths now `sys.exit(1)`);
- worker crashes;
- worker prints nothing for **5 minutes** (hard hang; the longest healthy
  quiet stretch is under a minute). The worker gets SIGTERM, a 10 s grace,
  then SIGKILL.

Any trigger → restart Appium, re-resolve the device, respawn the worker.

**Retry forever.** A worker that died within 60 s escalates the respawn
delay: 5 s → 15 s → 30 s → 60 s → 120 s → 300 s cap. A worker that lived
60 s or longer resets the ladder. A phone that falls off Wi-Fi at night is
re-probed every few minutes and the run resumes when it returns.

Clean endings:

- worker exit 0 (course done) → supervisor exits;
- Ctrl+C → SIGINT reaches both processes (same process group); the worker
  cleans up its session as today and exits 130, the supervisor stops
  Appium if it owns it and exits without respawning.

## Worker changes (minimal)

- `--worker` flag dispatch in `main.py`.
- Give-up paths ("Still stuck after several app restarts", "Connection to
  the device keeps failing") exit 1 instead of returning.
- KeyboardInterrupt exits 130 instead of returning (so a manual stop is
  never mistaken for course completion).
- Normal completion keeps exit 0.

## Testing

Supervisor decisions are pure functions tested in `test_watcher.py` in the
existing fake/FakeTime style:

- device pick from an `adb devices` output listing (Wi-Fi online, cable
  only, both, none);
- backoff ladder (escalation, cap, reset on a long-lived worker);
- restart-vs-stop decision from exit codes;
- silence detection against a fake clock.

The subprocess/socket plumbing stays thin and untested, like today's adb
helpers.

## Docs

`HOW_TO_RUN.md` is rewritten: running becomes "connect the phone (Wi-Fi or
cable), then `python3 main.py`". Manual Appium/adb steps move to a
fallback/advanced section; the recovery behavior section documents what the
supervisor handles by itself.
