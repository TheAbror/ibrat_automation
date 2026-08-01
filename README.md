# ibrat_automation

Automates tests in the **Ibrat Farzandlari** Android app (`uz.ibrat.farzandlari`) using [Appium](https://appium.io/).

- `main.py` — auto-runner: opens the course, navigates to the next test, and answers every question by itself. Answers it has seen before are looked up in `results.json`; unknown ones are solved by strategy (try options in order, tap chips, match pairs).
- `watcher.py` — watcher: you answer questions on the phone yourself while it records every correct/incorrect result into `results.json` and auto-taps **Next**. Every correct answer it records makes future `main.py` runs smarter.
- `test_watcher.py` — unit tests for the shared screen-parsing logic (no phone needed).

## What you need

| Tool | Why |
|------|-----|
| Python 3.9+ | runs the scripts |
| Node.js 18+ (LTS) | runs the Appium server |
| Appium 2 + UiAutomator2 driver | drives the Android UI |
| Android platform-tools (`adb`) | talks to the phone |
| An Android phone with USB debugging on | the Ibrat Farzandlari app must be installed and logged in |

## Install — macOS

Using [Homebrew](https://brew.sh/):

```bash
# 1. Core tools
brew install python node android-platform-tools

# 2. Appium server + Android driver
npm install -g appium
appium driver install uiautomator2

# 3. Python dependencies (from the project folder)
cd ibrat-automation
pip3 install -r requirements.txt
```

## Install — Windows

1. **Python** — install from [python.org/downloads](https://www.python.org/downloads/). During setup, check **"Add python.exe to PATH"**.
2. **Node.js LTS** — install from [nodejs.org](https://nodejs.org/).
3. **Android platform-tools (adb)** — download from [developer.android.com/tools/releases/platform-tools](https://developer.android.com/tools/releases/platform-tools), unzip somewhere permanent (e.g. `C:\platform-tools`), and add that folder to your `Path` environment variable.
4. **Appium + driver + Python packages** — in PowerShell or Command Prompt:

```powershell
npm install -g appium
appium driver install uiautomator2

cd ibrat-automation
pip install -r requirements.txt
```

> If your phone is not detected by `adb devices`, install the [Google USB driver](https://developer.android.com/studio/run/win-usb) (or your phone vendor's USB driver) — this is a Windows-only step.

## Prepare the phone

1. Enable **Developer options** (Settings → About phone → tap "Build number" 7 times).
2. Enable **USB debugging** in Developer options.
3. Connect the phone by USB and accept the "Allow USB debugging?" prompt.
4. Verify the connection and get your device serial:

```bash
adb devices
# List of devices attached
# ZY22GTXB9R    device
```

## Configure

Edit [config.py](config.py):

```python
DEVICE_NAME = "ZY22GTXB9R"          # your serial from `adb devices`
COURSE_DESCRIPTION = "Ingliz tili B2\nRustam Qoriyev"   # course card to open
```

`APPIUM_SERVER` stays `http://127.0.0.1:4723` when Appium runs on the same machine.

## Run

**Terminal 1** — start the Appium server and leave it running:

```bash
appium
```

**Terminal 2** — from the project folder, pick one:

```bash
# Auto-runner: answers the test by itself (Ctrl+C to stop early)
python3 main.py

# Watcher: you answer on the phone, it records results and taps Next
python3 watcher.py
```

On Windows use `python` instead of `python3`.

- `main.py` force-stops the app first so every run starts from the app's home screen, then navigates to the next test on its own.
- For `watcher.py`, navigate to the question screen on the phone yourself before starting it.
- Both write what they learn to `results.json` — keep this file; it is the answer memory.

## Run the tests

No phone or Appium server needed:

```bash
python3 -m unittest test_watcher -v
```

## Troubleshooting

- **`adb devices` shows nothing** — reconnect USB, re-accept the debugging prompt; on Windows install the USB driver (see above).
- **Appium can't find adb** — set the `ANDROID_HOME` environment variable to the folder that contains `platform-tools`, or make sure `adb` is on your `PATH`, then restart the Appium server.
- **Session errors / device wedged** — stop the script, restart the `appium` server, and run again. `main.py` always closes its session on exit, but a killed process can leave an orphaned one behind.
