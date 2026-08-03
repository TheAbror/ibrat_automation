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

## On a tall phone, buttons can start below the fold

UiAutomator2 leaves off-screen nodes out of the element tree entirely, so
a control below the fold is not merely out of reach — it does not exist
as far as the runner is concerned, and the lookup fails instantly instead
of settling. On a 1080x2340 phone this hit two screens:

- the home screen, whose "My collection" grid pushes the
  **2+6 Program Certificate** card off the bottom;
- the quiz **Start** page, whose button sits under the quiz-info card.

Both are handled by scrolling the screen until the target enters the tree
(`reveal_card` / `reveal_forward_button` in [navigation.py](navigation.py)).
A target already in view costs no swipe. If a new screen ever strands the
runner with "nothing to tap" on a phone where a human can plainly see a
button, this is the first thing to suspect — check `stuck_screen.xml` for
whether the button is in the tree at all.

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

If the phone fell off Wi-Fi but a USB cable is plugged in, nothing needs
restoring — `python3 main.py` finds and uses the cable connection by
itself.

To get back on Wi-Fi adb: the listener stops when the phone reboots. To
restore it:

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
