"""Watch the screen while you answer questions manually.

Detects each correct/incorrect feedback bottom sheet, records the result to
results.json with the question type (multiple_choice / word_translation /
fill_the_blank / matching), and auto-taps Next. When the answer was correct, the answer the
sheet reveals is saved as "correct_answer".

Usage: navigate to the question screen on the phone yourself, then run
    python3 watcher.py
and start answering. Ctrl+C to stop.
"""
import json
import time
from collections import Counter
from datetime import datetime

from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.client_config import AppiumClientConfig
from selenium.common.exceptions import WebDriverException
from urllib3.exceptions import HTTPError

import config
from question_handler import (
    CORRECT_MARKERS,
    INCORRECT_MARKERS,
    NEXT_LABELS,
    OPTION_IGNORE,
    classify_sheet,
    dedupe_results,
    detect_question_type,
    looks_like_finish,
    looks_like_promo,
    parse_screen,
    tap_next,
)

RESULTS_FILE = "results.json"
POLL_INTERVAL = 0.2
RECONNECT_DELAY = 2
MAX_RECONNECTS = 3
# Wi-Fi adb can silently swallow an in-flight command (the device never
# receives it); without a timeout the client then blocks forever.
COMMAND_TIMEOUT = 60
# selenium does not wrap transport failures: a timed-out or dropped request
# raises a raw urllib3 error (HTTPError subclass), not a WebDriverException.
CONNECTION_ERRORS = (WebDriverException, HTTPError)
# When a screen with a Next button refuses to move (a lesson page whose
# video is still loading swallows the tap), retry Next this often.
STUCK_RETAP_EVERY = 10


def load_results():
    try:
        with open(RESULTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_results(results):
    # The file is the answer book, not the attempt log: one entry per
    # question (per board for matching), repeats folded in without ever
    # replacing a learned answer with an empty reveal. Numbered ("n",
    # kept first) so the file shows how many questions it holds. Mutated
    # in place so the caller's list stays the saved list.
    results[:] = dedupe_results(results)
    for i, entry in enumerate(results):
        results[i] = {"n": i + 1, **{k: v for k, v in entry.items() if k != "n"}}
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


def connect(attach=True):
    # attach=True: no app_package/app_activity, so the session attaches to
    # whatever screen is open instead of relaunching the app.
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.automation_name = "UiAutomator2"
    options.device_name = config.DEVICE_NAME
    # Pin the session to the Wi-Fi adb target explicitly; deviceName alone
    # does not select the device when several are attached.
    options.udid = config.DEVICE_NAME
    options.no_reset = True
    if not attach:
        options.app_package = config.APP_PACKAGE
        options.app_activity = config.APP_ACTIVITY
        # Relaunch the app on session start even when it is already open,
        # so main.py always begins from the app's home screen.
        options.set_capability("appium:forceAppLaunch", True)
    client_config = AppiumClientConfig(
        remote_server_addr=config.APPIUM_SERVER, timeout=COMMAND_TIMEOUT
    )
    driver = webdriver.Remote(
        config.APPIUM_SERVER, options=options, client_config=client_config
    )
    # UiAutomator2 waits for the UI thread to go idle before every command.
    # That wait is load-bearing on question screens: dumps and chip taps
    # must land after the entry animation, or the runner reads half a chip
    # row and answers with it (observed 2026-08-03: 0 → ~33% wrong). But
    # screens that NEVER go idle (the chest reward's looping animation)
    # pay the timeout in full on every command, so the default 10s cap
    # turns the chest sequence into minutes. Screens that settle only pay
    # actual settle time (~0.3s), so the cap just bounds the pathological
    # case.
    driver.update_settings({"waitForIdleTimeout": 2000})
    return driver


def safe_quit(driver):
    try:
        driver.quit()
    except CONNECTION_ERRORS:
        pass


def poll_once(driver, state, results):
    """One watch cycle: remember the question, or log + skip a feedback sheet.

    Returns 'correct' / 'incorrect' / 'other' when a sheet was handled,
    'question' when a question screen was seen, else None.
    """
    nodes = parse_screen(driver.page_source)
    descs = [d for _, d in nodes if d]
    sheet = classify_sheet(descs)

    if sheet is None:
        question = next(
            (d for cls, d in nodes if cls == "android.view.View" and d), None
        )
        options = [
            d for cls, d in nodes
            if cls == "android.widget.Button" and d and d not in OPTION_IGNORE
        ]
        # A real question always offers 2+ option buttons; without this
        # floor the mid-run streak popup ("3" / "Day" / Continue) counts
        # as a question and the runner never tries to dismiss it. Promo
        # screens clear that floor with paywall CTAs — never a question.
        # Finish screens clear it with their stat Buttons (Lessons /
        # Quizzes / Accuracy) — never questions either.
        if (question and len(options) >= 2
                and not looks_like_promo(descs) and not looks_like_finish(descs)):
            state["question"] = question
            state["options"] = options
            state["descs"] = descs
            return "question"
        return None

    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "type": detect_question_type(state["question"], state["options"], state["descs"]),
        "result": sheet,
        "question": state["question"],
        "options": state["options"],
    }
    # Matching sheets reveal nothing, but the auto-runner discovers the
    # pairs itself and leaves them in state for us to attach.
    pairs = state.pop("pending_pairs", None)
    if entry["type"] == "matching" and pairs:
        entry["correct_answer"] = list(pairs)
    else:
        # Both sheets reveal the correct answer: it's whatever text appeared
        # on screen that wasn't there before the sheet (multiset diff, so a
        # repeated text like a re-shown option counts too). Saving it on
        # incorrect results too lets the runner learn from its own mistakes.
        shown = [
            d for d in (Counter(descs) - Counter(state.get("descs", []))).elements()
            if d not in NEXT_LABELS
            and not any(m in d for m in CORRECT_MARKERS + INCORRECT_MARKERS)
        ]
        if shown:
            entry["correct_answer"] = shown
    results.append(entry)
    save_results(results)
    state[sheet] += 1
    print(f"[{entry['time']}] {sheet.upper()} ({entry['type']}) | {state['question']}")

    if not tap_next(driver):
        print("Next button not found — tap it manually, still watching...")

    # Wait until the sheet is gone so the same one isn't logged twice.
    # A lesson page lands here too (its "Next" makes classify_sheet say
    # "other"); while its video is still loading the tap is swallowed, so
    # keep re-tapping Next — a manual tap on the phone also moves it on.
    stuck_since = time.time()
    while classify_sheet([d for _, d in parse_screen(driver.page_source) if d]) is not None:
        time.sleep(0.25)
        if time.time() - stuck_since >= STUCK_RETAP_EVERY:
            stuck_since = time.time()
            print("Screen not moving — re-tapping Next (a tap on the phone works too)...")
            tap_next(driver)
    return sheet


def fresh_state():
    return {
        "question": None, "options": [], "descs": [],
        "correct": 0, "incorrect": 0, "other": 0,
    }


def run(driver_factory):
    driver = driver_factory()
    results = load_results()
    state = fresh_state()
    reconnects_left = MAX_RECONNECTS

    print("Watching... answer questions on the phone. Ctrl+C to stop.")
    print(f"Results will be saved to {RESULTS_FILE}\n")

    try:
        while True:
            try:
                poll_once(driver, state, results)
                reconnects_left = MAX_RECONNECTS
            except CONNECTION_ERRORS as e:
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
