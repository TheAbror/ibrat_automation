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
