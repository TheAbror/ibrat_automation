"""Watch the screen while you answer questions manually.

Detects each correct/incorrect feedback bottom sheet, records the result
to results.json, and auto-taps Next so you never have to dismiss it.

Usage: navigate to the question screen on the phone yourself, then run
    python3 watcher.py
and start answering. Ctrl+C to stop.
"""
import json
import time
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
from question_handler import check_answer_feedback, get_question_text

RESULTS_FILE = "results.json"
POLL_INTERVAL = 0.4
RECONNECT_DELAY = 2
MAX_RECONNECTS = 3


def load_results():
    try:
        with open(RESULTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_results(results):
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def get_visible_options(driver):
    """Return visible option texts, or None if the UI changed mid-read."""
    options = []
    try:
        for b in driver.find_elements(*loc.ALL_BUTTONS):
            desc = (b.get_attribute("content-desc") or "").strip()
            if desc and desc not in ("Next", "Continue"):
                options.append(desc)
    except StaleElementReferenceException:
        return None
    return options


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
    feedback = check_answer_feedback(driver)

    if feedback == "unknown":
        question = get_question_text(driver)
        if question:
            options = get_visible_options(driver)
            if options is not None:
                state["question"] = question
                state["options"] = options
        return

    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "result": feedback,
        "question": state["question"],
        "options": state["options"],
    }
    results.append(entry)
    save_results(results)
    state[feedback] += 1
    print(f"[{entry['time']}] {feedback.upper()} | {state['question']}")

    if not tap_next(driver):
        print("Next button not found — tap it manually, still watching...")

    # Wait until the sheet is gone so the same one isn't logged twice
    while check_answer_feedback(driver) != "unknown":
        time.sleep(0.5)


def run(driver_factory):
    driver = driver_factory()
    results = load_results()
    state = {"question": None, "options": [], "correct": 0, "incorrect": 0}
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
