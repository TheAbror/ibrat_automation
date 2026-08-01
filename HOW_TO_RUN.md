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
