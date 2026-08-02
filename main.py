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
import subprocess
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
from navigation import dismiss_popup, navigate_to_test, tap_chest, tap_forward_button
from question_handler import (
    OPTION_IGNORE,
    build_answer_map,
    build_pair_map,
    chip_sequence,
    choose_mc_option,
    classify_sheet,
    detect_question_type,
    pair_attempt_order,
    parse_screen,
    split_matching_cards,
    xpath_literal,
)

IDLE_LIMIT = 20      # seconds with no question and no sheet -> assume finished
MAX_QUESTIONS = 500
# Where the tree of an unrecognized screen is saved before giving up on it
STUCK_SCREEN_FILE = "stuck_screen.xml"


def tap_text(driver, text):
    xpath = f"//android.widget.Button[@content-desc={xpath_literal(text)}]"
    driver.find_element(AppiumBy.XPATH, xpath).click()


def sheet_is_up(driver):
    return classify_sheet([d for _, d in parse_screen(driver.page_source) if d]) is not None


def wait_for_sheet(driver, state, timeout=12):
    """Wait for the feedback sheet, refreshing state's pre-sheet snapshot.

    Keeping state["descs"] fresh while our own taps rearrange the screen
    means the answer-reveal diff won't include chips we placed ourselves.
    """
    end = time.time() + timeout
    while time.time() < end:
        descs = [d for _, d in parse_screen(driver.page_source) if d]
        if classify_sheet(descs) is not None:
            return True
        state["descs"] = descs
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


def card_state(driver):
    """Ordered card texts — changes when a correct pair locks and moves."""
    return [
        d for cls, d in parse_screen(driver.page_source)
        if cls == "android.widget.Button" and d and d not in OPTION_IGNORE
    ]


def answer_matching(driver, cards, state, known_pairs):
    """Match pairs one at a time, checking after every attempt.

    For each left card, try the remaining right cards — the known partner
    from earlier runs first. A correct pair rearranges the cards (they
    move up/lock), so a changed screen means matched: remove that right
    card from the pool and move on. Discovered pairs are left in
    state["pending_pairs"] so the sheet logger saves them for next time.
    """
    lefts, rights = split_matching_cards(cards)
    remaining = list(rights)
    found = []
    state["pending_pairs"] = found
    print(f"  matching {len(lefts)} pairs: {lefts} x {rights}")

    for left in lefts:
        if sheet_is_up(driver):
            return True
        for right in pair_attempt_order(left, remaining, known_pairs):
            before = card_state(driver)
            try:
                tap_text(driver, left)
                time.sleep(0.4)
                tap_text(driver, right)
            except (NoSuchElementException, StaleElementReferenceException):
                continue
            time.sleep(0.8)
            if sheet_is_up(driver):
                found.append([left, right])
                return True
            if card_state(driver) != before:
                print(f"  matched: {left} + {right}")
                found.append([left, right])
                remaining.remove(right)
                break
            print(f"  not a pair: {left} + {right}")
    return True


def rescue_stuck_screen(driver):
    """Last resort before giving up on an unrecognized screen.

    Saves the tree for diagnosis (the chest-reward screens have never
    been captured in a real dump — the saved file is how the next
    stranding gets explained), then tries the chest-style center-column
    taps: the known stranding screen is the reward chest, whose only tap
    target is mid-screen. Rescued only if the screen actually changed.
    """
    with open(STUCK_SCREEN_FILE, "w", encoding="utf-8") as f:
        f.write(driver.page_source)
    print(f"Unrecognized screen — tree saved to {STUCK_SCREEN_FILE}, trying center taps...")
    return tap_chest(driver)


def answer_until_done(driver):
    """auto_answer_loop plus reconnect when a command stalls or drops.

    COMMAND_TIMEOUT bounds every request, so a command swallowed by Wi-Fi
    adb raises instead of blocking forever. The app stays on its current
    screen, so reattaching resumes right where the run stalled. Owns
    quitting whichever session is currently live.
    """
    reconnects_left = watcher.MAX_RECONNECTS
    try:
        while True:
            try:
                auto_answer_loop(driver)
                return
            except watcher.CONNECTION_ERRORS as e:
                if reconnects_left == 0:
                    print("Connection to the device keeps failing — giving up.")
                    raise
                reconnects_left -= 1
                print(f"Lost connection to the device ({type(e).__name__}) — reconnecting...")
                watcher.safe_quit(driver)
                time.sleep(watcher.RECONNECT_DELAY)
                driver = watcher.connect(attach=True)
    finally:
        watcher.safe_quit(driver)


def auto_answer_loop(driver):
    results = watcher.load_results()
    known = build_answer_map(results)
    known_pairs = build_pair_map(results)
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
            known_pairs = build_pair_map(results)
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
                    answer_matching(driver, options, state, known_pairs)
            except (NoSuchElementException, StaleElementReferenceException) as e:
                print(f"  tap failed ({type(e).__name__}), retrying next cycle")
                continue
            if not wait_for_sheet(driver, state):
                print("  no feedback sheet appeared within 12s")
            idle_since = time.time()
            continue

        # Unrecognized screen: an offer popup may be in the way, or it's a
        # finish screen (Retry / Next ...) or the next quiz's Start page.
        if dismiss_popup(driver):
            idle_since = time.time()
            continue
        if tap_forward_button(driver):
            idle_since = time.time()
            continue

        if time.time() - idle_since > IDLE_LIMIT:
            if rescue_stuck_screen(driver):
                idle_since = time.time()
                continue
            print(f"\nNo questions or feedback for {IDLE_LIMIT}s — test finished (or stuck).")
            break
        time.sleep(0.5)

    print(f"\nDone. {state['correct']} correct, {state['incorrect']} incorrect this run.")
    print(f"Results saved in {watcher.RESULTS_FILE}")


def force_stop_app():
    """Kill the app via adb so every run starts from the app's home screen.

    Works even when the app was left open mid-test on the phone. Falls back
    to the session's forceAppLaunch capability when adb isn't reachable.
    """
    commands = (
        ["adb", "-s", config.DEVICE_NAME, "shell", "am", "force-stop", config.APP_PACKAGE],
        ["adb", "shell", "am", "force-stop", config.APP_PACKAGE],
    )
    for cmd in commands:
        try:
            subprocess.run(cmd, check=True, timeout=15, capture_output=True)
            print("App closed (adb force-stop)")
            time.sleep(1)
            return True
        except (OSError, subprocess.SubprocessError):
            continue
    print("adb force-stop not available — relying on forceAppLaunch")
    return False


def main():
    force_stop_app()
    driver = watcher.connect(attach=False)

    # Always close the session: an orphaned session wedges the Appium server
    # and breaks the next script that talks to the same device.
    try:
        time.sleep(3)

        wait = WebDriverWait(driver, 20)
        wait_long = WebDriverWait(driver, 30)
        if navigate_to_test(driver, wait, wait_long):
            answer_until_done(driver)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        watcher.safe_quit(driver)


if __name__ == "__main__":
    main()
