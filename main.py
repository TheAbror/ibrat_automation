"""Automatic test runner.

Navigates to the test, then answers every question:
- known answers are looked up in results.json (collected by watcher.py and
  by this runner itself — every correct answer makes the next run smarter)
- unknown multiple_choice: option A first; the next untried option when the
  same question repeats
- unknown fill_the_blank: tap all chips first-to-last, then Continue
- matching: direct neighbour first, then every other combination — wrong
  pairs reset harmlessly, correct pairs lock in, so the screen always
  completes

Usage: python3 main.py   (Ctrl+C to stop early)
"""
import time

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)

import config
import locators as loc
import watcher
from navigation import navigate_to_test
from question_handler import (
    build_answer_map,
    chip_sequence,
    choose_mc_option,
    classify_sheet,
    detect_question_type,
    matching_attempt_pairs,
    parse_screen,
    xpath_literal,
)

IDLE_LIMIT = 20      # seconds with no question and no sheet -> assume finished
MAX_QUESTIONS = 500


def tap_text(driver, text):
    xpath = f"//android.widget.Button[@content-desc={xpath_literal(text)}]"
    driver.find_element(AppiumBy.XPATH, xpath).click()


def sheet_is_up(driver):
    return classify_sheet([d for _, d in parse_screen(driver.page_source) if d]) is not None


def wait_for_sheet(driver, timeout=12):
    end = time.time() + timeout
    while time.time() < end:
        if sheet_is_up(driver):
            return True
        time.sleep(0.4)
    return False


def answer_multiple_choice(driver, question, options, known, attempted):
    choice = choose_mc_option(question, options, known, attempted)
    if choice is None:
        return False
    print(f"  tapping option: {choice}")
    tap_text(driver, choice)
    return True


def answer_fill_the_blank(driver, question, options, known):
    sequence = chip_sequence(question, options, known)
    print(f"  tapping {len(sequence)} chips")
    for word in sequence:
        try:
            tap_text(driver, word)
        except (NoSuchElementException, StaleElementReferenceException):
            continue
        time.sleep(0.3)
    try:
        btn = WebDriverWait(driver, 3).until(
            lambda d: d.find_element(*loc.CONTINUE_BUTTON)
        )
        btn.click()
        print("  tapped Continue")
    except (WebDriverException, NoSuchElementException):
        pass  # some screens submit automatically after the last chip
    return True


def answer_matching(driver, cards):
    attempts = matching_attempt_pairs(cards)
    print(f"  matching {len(cards)} cards ({len(attempts)} attempts max)")
    for left, right in attempts:
        if sheet_is_up(driver):
            return True
        try:
            tap_text(driver, left)
            time.sleep(0.3)
            tap_text(driver, right)
            time.sleep(0.3)
        except (NoSuchElementException, StaleElementReferenceException):
            continue
    return True


def auto_answer_loop(driver):
    results = watcher.load_results()
    known = build_answer_map(results)
    attempted = {}
    state = watcher.fresh_state()
    answered = 0
    idle_since = time.time()

    while answered < MAX_QUESTIONS:
        try:
            status = watcher.poll_once(driver, state, results)
        except StaleElementReferenceException:
            continue

        if status in ("correct", "incorrect", "other"):
            # poll_once logged the result and tapped Next; new correct
            # answers become known immediately.
            known = build_answer_map(results)
            idle_since = time.time()
            continue

        if status == "question":
            question, options = state["question"], state["options"]
            qtype = detect_question_type(question, options)
            answered += 1
            print(f"\n[{answered}] {qtype}: {question}")
            try:
                if qtype == "multiple_choice":
                    answer_multiple_choice(driver, question, options, known, attempted)
                elif qtype == "fill_the_blank":
                    answer_fill_the_blank(driver, question, options, known)
                else:
                    answer_matching(driver, options)
            except (NoSuchElementException, StaleElementReferenceException) as e:
                print(f"  tap failed ({type(e).__name__}), retrying next cycle")
                continue
            if not wait_for_sheet(driver):
                print("  no feedback sheet appeared within 12s")
            idle_since = time.time()
            continue

        if time.time() - idle_since > IDLE_LIMIT:
            print(f"\nNo questions or feedback for {IDLE_LIMIT}s — test finished (or stuck).")
            break
        time.sleep(0.5)

    print(f"\nDone. {state['correct']} correct, {state['incorrect']} incorrect this run.")
    print(f"Results saved in {watcher.RESULTS_FILE}")


def main():
    driver = watcher.connect(attach=False)

    # Always close the session: an orphaned session wedges the Appium server
    # and breaks the next script that talks to the same device.
    try:
        try:
            driver.terminate_app(config.APP_PACKAGE)
            time.sleep(1)
        except WebDriverException as e:
            print(f"terminate_app skipped: {e}")

        driver.activate_app(config.APP_PACKAGE)
        time.sleep(3)

        wait = WebDriverWait(driver, 20)
        wait_long = WebDriverWait(driver, 30)
        navigate_to_test(driver, wait, wait_long)

        auto_answer_loop(driver)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        watcher.safe_quit(driver)


if __name__ == "__main__":
    main()
