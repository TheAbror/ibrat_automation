"""Watch the screen while you answer questions manually.

Detects each correct/incorrect feedback bottom sheet, records the result to
results.json with the question type (multiple_choice / fill_the_blank /
matching), and auto-taps Next. When the answer was correct, the answer the
sheet reveals is saved as "correct_answer".

Usage: navigate to the question screen on the phone yourself, then run
    python3 watcher.py
and start answering. Ctrl+C to stop.
"""
import json
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime

from appium import webdriver
from appium.options.android import UiAutomator2Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException,
    WebDriverException,
)

import config
import locators as loc
from question_handler import detect_question_type

RESULTS_FILE = "results.json"
POLL_INTERVAL = 0.4
RECONNECT_DELAY = 2
MAX_RECONNECTS = 3

CORRECT_MARKERS = ("Nicely done",)
INCORRECT_MARKERS = ("Incorrect Answer",)
NEXT_LABELS = ("Next",)
# "null" is the report (!) icon-button, which exposes the literal string
# "null" as its label on every question screen — not a real answer option.
OPTION_IGNORE = NEXT_LABELS + ("Continue", "null")


def load_results():
    try:
        with open(RESULTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_results(results):
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def parse_screen(xml):
    """One atomic snapshot of the UI tree as [(class, content-desc), ...]."""
    return [
        (el.get("class") or "", el.get("content-desc") or "")
        for el in ET.fromstring(xml).iter()
    ]


def classify_sheet(descs):
    """Return 'correct' / 'incorrect' / 'other' if a feedback sheet is up, else None."""
    for d in descs:
        if any(m in d for m in CORRECT_MARKERS):
            return "correct"
    for d in descs:
        if any(m in d for m in INCORRECT_MARKERS):
            return "incorrect"
    if any(d in NEXT_LABELS for d in descs):
        return "other"
    return None


def tap_next(driver):
    for _ in range(2):
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable(loc.NEXT_BUTTON)
            )
            btn.click()
            return True
        except StaleElementReferenceException:
            continue
        except TimeoutException:
            return False
    return False


def connect():
    # No app_package/app_activity: attach to whatever screen is open
    # instead of relaunching the app and losing your place in the test.
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.device_name = config.DEVICE_NAME
    options.no_reset = True
    return webdriver.Remote(config.APPIUM_SERVER, options=options)


def safe_quit(driver):
    try:
        driver.quit()
    except WebDriverException:
        pass


def poll_once(driver, state, results):
    """One watch cycle: remember the question, or log + skip a feedback sheet."""
    nodes = parse_screen(driver.page_source)
    descs = [d for _, d in nodes if d]
    sheet = classify_sheet(descs)

    if sheet is None:
        question = next(
            (d for cls, d in nodes if cls == "android.view.View" and d), None
        )
        if question:
            state["question"] = question
            state["options"] = [
                d for cls, d in nodes
                if cls == "android.widget.Button" and d and d not in OPTION_IGNORE
            ]
            state["descs"] = descs
        return

    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "type": detect_question_type(state["question"], state["options"]),
        "result": sheet,
        "question": state["question"],
        "options": state["options"],
    }
    if sheet == "correct":
        # The sheet reveals the correct answer: it's whatever text appeared
        # on screen that wasn't there before the sheet (multiset diff, so a
        # repeated text like the chosen option counts too).
        entry["correct_answer"] = [
            d for d in (Counter(descs) - Counter(state.get("descs", []))).elements()
            if d not in NEXT_LABELS
            and not any(m in d for m in CORRECT_MARKERS)
        ]
    results.append(entry)
    save_results(results)
    state[sheet] += 1
    print(f"[{entry['time']}] {sheet.upper()} ({entry['type']}) | {state['question']}")

    if not tap_next(driver):
        print("Next button not found — tap it manually, still watching...")

    # Wait until the sheet is gone so the same one isn't logged twice
    while classify_sheet([d for _, d in parse_screen(driver.page_source) if d]) is not None:
        time.sleep(0.5)


def run(driver_factory):
    driver = driver_factory()
    results = load_results()
    state = {
        "question": None, "options": [], "descs": [],
        "correct": 0, "incorrect": 0, "other": 0,
    }
    reconnects_left = MAX_RECONNECTS

    print("Watching... answer questions on the phone. Ctrl+C to stop.")
    print(f"Results will be saved to {RESULTS_FILE}\n")

    try:
        while True:
            try:
                poll_once(driver, state, results)
                reconnects_left = MAX_RECONNECTS
            except WebDriverException as e:
                # e.g. "socket hang up": another session restarted the
                # device-side automation server. Reconnect and keep watching.
                if reconnects_left == 0:
                    print("Connection to the device keeps failing — giving up.")
                    raise
                reconnects_left -= 1
                print(f"Lost connection to the device ({type(e).__name__}) — reconnecting...")
                safe_quit(driver)
                time.sleep(RECONNECT_DELAY)
                driver = driver_factory()
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print(f"\nStopped. {state['correct']} correct, {state['incorrect']} incorrect this session.")
        print(f"All results saved in {RESULTS_FILE}")
    finally:
        safe_quit(driver)


def main():
    run(connect)


if __name__ == "__main__":
    main()
