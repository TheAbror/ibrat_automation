# How to run the automation

The phone is used over Wi-Fi adb — no cable needed, it does not charge from the Mac.
The device is pinned in `config.py` (`DEVICE_NAME = "192.168.1.16:5555"`), so there is
nothing to select manually.

## 1. Make sure the phone is connected

Only needed after a Mac restart or if the connection drops:

```bash
adb connect 192.168.1.16:5555
adb devices        # should show:  192.168.1.16:5555  device
```

## 2. Start the Appium server

In one terminal, run it and leave it open:

```bash
appium
```

## 3. Run the automation

In a second terminal:

```bash
cd ~/Desktop/ibrat-automation
python3 main.py
```

Stop it anytime with `Ctrl+C` — it cleans up its session on exit.

**Only one runner at a time.** Two `main.py` (or `watcher.py`) processes on
the same phone kill each other: every new session restarts the device-side
automation server, which drops the other runner's connection. If runs keep
dying with "instrumentation process is not running", check for a second
runner still going (another terminal, a background task) and stop it.

## If a lesson video loads slowly

Lesson pages with a video swallow the **Next** tap until the video has loaded.
The runner does not give up there: it prints

```
Screen is not moving forward (video still loading?) — waiting.
```

and keeps re-tapping Next every few seconds. You can also just tap **Next**
on the phone yourself — as soon as the screen changes, the automation
continues on its own.

## If a full-screen promo appears (IELTSGA GOO! / Pro offer)

The app sometimes shows full-screen promo/upsell screens mid-run. The ones
with an X are closed via the X; the IELTS interstitial ("O'ychi o'yini...")
has no X at all, so the runner presses the Android **back** button, which
lands on the screen the promo covered (e.g. a quiz Start page) and
continues. Promo buttons ("IELTSGA GOO!", "Subscribe", plan cards) are
never tapped.

## If the app gets stuck on a screen (ads, rewards, crashes)

The runner restarts the app and navigates back into the course whenever
it can't make progress — up to 5 restarts per run:

- an unrecognized screen (a new ad, an unreadable reward screen) sits
  there for more than 10 seconds;
- a "question" is answered twice but no feedback sheet ever appears
  (an ad styled like a question);
- the app crashes to the Android launcher or otherwise leaves the
  foreground.

Before each restart the offending screen's UI tree is saved to
`stuck_screen.xml` — if some screen keeps forcing restarts, keep that
file: it shows exactly what the runner saw and is how the screen gets
supported. The runner never taps blindly on screens it doesn't know,
so ad buttons and other apps are never opened.

## Manual adb commands

Plain `adb shell ...` works since there is just one device.
Explicit form: `adb -s 192.168.1.16:5555 shell ...`

## If the phone is offline (after a phone reboot or Wi-Fi change)

The Wi-Fi adb listener stops when the phone reboots. To restore it:

1. Plug the phone in via USB cable once
2. Run `./wifi_adb.sh`
3. Unplug the cable

Alternative without a cable (Android 11+): on the phone open
Developer options → Wireless debugging → "Pair device with pairing code", then:

```bash
adb pair <ip>:<pairing-port> <code>
adb connect 192.168.1.16:5555
```

If `192.168.1.16` stops working, the phone's IP may have changed (DHCP) —
check the current IP in the phone's Wi-Fi settings and update `config.py`.
