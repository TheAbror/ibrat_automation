import os

# Wi-Fi adb target (USB serial was "ZY22GTXB9R"). After a phone reboot,
# plug in the cable once and run ./wifi_adb.sh to restore this connection.
# The supervisor overrides this per-run (IBRAT_DEVICE) when the phone is
# reachable over a USB cable instead of the Wi-Fi target.
DEVICE_NAME = os.environ.get("IBRAT_DEVICE") or "192.168.1.16:5555"
APP_PACKAGE = "uz.ibrat.farzandlari"
APP_ACTIVITY = ".MainActivity"

APPIUM_SERVER = "http://127.0.0.1:4723"

# Which course to open. The test itself is no longer configured: the runner
# always opens whatever the course sequence says is next.
COURSE_DESCRIPTION = "Ingliz tili B2\nRustam Qoriyev"
# The module card to open when the app falls back to the "Modules" list
# (matched as a desc prefix — the full desc carries live progress counts).
MODULE_PREFIX = "B2 |"
