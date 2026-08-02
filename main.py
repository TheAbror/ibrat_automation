"""Automatic test runner.

Navigates to the test, then answers every question:
- known answers are looked up in results.json (collected by watcher.py and
  by this runner itself — every correct answer makes the next run smarter)
- unknown multiple_choice: option A first; the next untried option when the
  same question repeats
- word_translation (a few chips + a Continue button): chosen like
  multiple_choice, but submitted with Continue — the chip tap alone
  submits nothing
- unknown fill_the_blank: tap all chips first-to-last, then Continue
- matching: cards tapped by position (labels can repeat), the known
  partner first, then every other right-column card — wrong pairs reset
  harmlessly, correct pairs lock in, so the board always completes
- anything else that yields no feedback sheet: one desperate attempt of
  first option + Continue before the app is restarted

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
from navigation import (
    StuckScreenError,
    dismiss_popup,
    navigate_to_test,
    tap_forward_button,
)
from question_handler import (
    build_answer_map,
    build_pair_map,
    card_signature,
    chip_sequence,
    choose_mc_option,
    classify_sheet,
    detect_question_type,
    pair_attempt_order,
    parse_cards,
    parse_screen,
    split_matching_columns,
    xpath_literal,
)

IDLE_LIMIT = 10      # seconds on an unrecognized screen -> restart the app
MAX_QUESTIONS = 5000  # a whole course in one run (~88 quizzes + retakes)
APP_RELAUNCHES = 50  # app restarts per run before giving up
# Accuracy governor: keep this run's score in the 93–96% band. A known
# answer is deliberately missed ONLY when answering it correctly would
# push the run's accuracy above ACCURACY_HIGH; at or below it, every
# known answer is played straight (so the score can climb back whenever
# natural misses on unseen questions drag it down). Equilibrium sits at
# ~95%, safely under the 96% cap. Set ACCURACY_HIGH = 1.0 to always
# answer as well as possible.
ACCURACY_HIGH = 0.95
GOVERNOR_WARMUP = 20  # honest answers before steering starts

# This run's sheet results, across app restarts (module-level so every
# auto_answer_loop session adds to the same tally).
RUN_STATS = {"correct": 0, "incorrect": 0}


def should_miss(correct, incorrect):
    """True when a correct answer NOW would lift accuracy past the cap."""
    total = correct + incorrect
    if total < GOVERNOR_WARMUP:
        return False
    return (correct + 1) / (total + 1) > ACCURACY_HIGH
# Where the tree of an unrecognized screen is saved before restarting
STUCK_SCREEN_FILE = "stuck_screen.xml"


class AppLostError(Exception):
    """The app is no longer in the foreground (crashed or was closed)."""


def tap_text(driver, text):
    xpath = f"//android.widget.Button[@content-desc={xpath_literal(text)}]"
    driver.find_element(AppiumBy.XPATH, xpath).click()


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
        time.sleep(0.2)
    return False


def answer_multiple_choice(driver, question, options, known, attempted):
    choice = choose_mc_option(question, options, known, attempted)
    if choice is None:
        return False
    print(f"  tapping option: {choice}")
    tap_text(driver, choice)
    return True


def tap_continue(driver):
    try:
        btn = WebDriverWait(driver, 3).until(
            lambda d: d.find_element(*loc.CONTINUE_BUTTON)
        )
        btn.click()
        print("  tapped Continue")
    except (WebDriverException, NoSuchElementException):
        pass  # some screens submit automatically after the last chip


def answer_fill_the_blank(driver, question, options, known):
    sequence = chip_sequence(question, options, known)
    print(f"  tapping {len(sequence)} chips")
    for word in sequence:
        try:
            tap_text(driver, word)
        except (NoSuchElementException, StaleElementReferenceException):
            continue
        time.sleep(0.1)
    tap_continue(driver)
    return True


def answer_wrong(driver, qtype, question, options, known, state):
    """Deliberately miss a question the runner knows (accuracy throttle).

    Taps an option that is NOT the known answer; chip screens still get
    their Continue. The feedback sheet reveals the right answer anyway,
    so the answer book loses nothing.
    """
    answer = (known.get(question) or "").strip().lower()
    wrong = next((o for o in options if o.strip().lower() != answer), None)
    if wrong is None:
        return False
    print(f"  missing on purpose: {wrong}")
    tap_text(driver, wrong)
    if qtype in ("word_translation", "fill_the_blank"):
        time.sleep(0.1)
        state["descs"] = [d for _, d in parse_screen(driver.page_source) if d]
        tap_continue(driver)
    return True


def answer_unknown(driver, options, state):
    """Last resort for a question shape the runner isn't prepared for:
    tap the first option, then Continue if the screen has one. The goal
    is not to be right — it is to get SOME feedback sheet up so the run
    moves on and the reveal gets recorded for next time."""
    if not options:
        return False
    print("  unprepared question shape — tapping first option + Continue")
    tap_text(driver, options[0])
    time.sleep(0.1)
    state["descs"] = [d for _, d in parse_screen(driver.page_source) if d]
    tap_continue(driver)
    return True


def answer_word_translation(driver, question, options, known, attempted, state):
    """A multiple choice built from chips: tap the chip, then submit.

    The Continue button starts disabled and the chip tap alone changes
    nothing — without the Continue press no feedback sheet ever comes and
    the runner restarts in a loop (the 2026-08-02 "Clever -" stranding).

    The pre-sheet snapshot is refreshed between the chip tap and Continue:
    the tapped chip moved into the answer area, and with a stale baseline
    the sheet diff echoes it next to the revealed word — an ambiguous
    entry that teaches nothing (watcher mode gets the fresh baseline for
    free from its continuous polling).
    """
    if not answer_multiple_choice(driver, question, options, known, attempted):
        return False
    time.sleep(0.1)  # let the chip land so Continue enables
    state["descs"] = [d for _, d in parse_screen(driver.page_source) if d]
    tap_continue(driver)
    return True


# Wrong-pair taps a matching board tolerates before giving up on it; the
# honest worst case for a 5-pair board is ~15 misses plus 5 matches.
MATCH_ATTEMPTS = 40


def answer_matching(driver, state, known_pairs):
    """Match pairs one attempt at a time, tapping cards by position.

    Labels repeat on the category boards ("Singular"/"Plural" twice
    each), so text taps are ambiguous — they land on the first tree
    instance with that label, including an already-locked card, which
    swallows the tap (the 2026-08-02 category-board stranding). Cards
    are therefore tapped by their coordinates, the columns are told
    apart by geometry, and progress is judged by the full (label,
    position, clickable) board signature — a locked pair can keep its
    labels and position, so the label list alone can miss a match. The
    board is re-read after every attempt because locking rearranges it.
    Discovered pairs are left in state["pending_pairs"] so the sheet
    logger saves them for next time.
    """
    found = []
    state["pending_pairs"] = found
    budget = MATCH_ATTEMPTS
    skip = 0
    announced = False

    while budget > 0:
        xml = driver.page_source
        if classify_sheet([d for _, d in parse_screen(xml) if d]) is not None:
            return True
        cards = parse_cards(xml)
        lefts, rights = split_matching_columns(cards)
        if not lefts or not rights:
            budget -= 1
            time.sleep(0.4)  # board settling, or the sheet on its way
            continue
        if not announced:
            announced = True
            print(f"  matching {len(lefts)} pairs: "
                  f"{[c['label'] for c in lefts]} x {[c['label'] for c in rights]}")

        left = lefts[skip % len(lefts)]
        before = card_signature(cards)
        matched = False
        for right in pair_attempt_order(left["label"], rights, known_pairs):
            budget -= 1
            driver.tap([(left["x"], left["y"])])
            time.sleep(0.25)
            driver.tap([(right["x"], right["y"])])
            time.sleep(0.6)
            xml = driver.page_source
            if classify_sheet([d for _, d in parse_screen(xml) if d]) is not None:
                found.append([left["label"], right["label"]])
                return True
            if card_signature(parse_cards(xml)) != before:
                print(f"  matched: {left['label']} + {right['label']}")
                found.append([left["label"], right["label"]])
                matched = True
                break
            print(f"  not a pair: {left['label']} + {right['label']}")
            if budget <= 0:
                break
        # a left whose rights all failed is skipped next round — the board
        # may need another pair locked first
        skip = 0 if matched else skip + 1
    return True


def save_stuck_screen(driver):
    """Save the tree of a screen the runner can't handle.

    The saved file is how a new stranding screen gets explained and then
    supported (it identified the 2026-08-02 launcher crash). No taps are
    tried here: blind taps on an unknown ad screen could open the ad.
    """
    with open(STUCK_SCREEN_FILE, "w", encoding="utf-8") as f:
        f.write(driver.page_source)
    print(f"Unrecognized screen — tree saved to {STUCK_SCREEN_FILE}")


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
    # An ad styled like a question (a text plus 2+ buttons) dodges the
    # idle timer: answering it looks like progress but no feedback sheet
    # ever comes. Two sheetless attempts on the same question = restart.
    sheetless_question, sheetless_count = None, 0

    while answered < MAX_QUESTIONS:
        try:
            status = watcher.poll_once(driver, state, results)
        except StaleElementReferenceException:
            continue

        if status in ("correct", "incorrect", "other"):
            # poll_once logged the result and tapped Next; new correct
            # answers become known immediately.
            if status in RUN_STATS:
                RUN_STATS[status] += 1
            known = build_answer_map(results)
            known_pairs = build_pair_map(results)
            idle_since = time.time()
            continue

        if status == "question":
            question, options = state["question"], state["options"]
            qtype = detect_question_type(question, options, state["descs"])
            answered += 1
            print(f"\n[{answered}] {qtype}: {question}")
            throttle = (
                qtype != "matching" and question in known
                and should_miss(RUN_STATS["correct"], RUN_STATS["incorrect"])
            )
            try:
                if question == sheetless_question:
                    # the typed handler already got no sheet out of this
                    # question — try the desperate generic move before the
                    # restart hammer falls
                    answer_unknown(driver, options, state)
                elif throttle:
                    answer_wrong(driver, qtype, question, options, known, state)
                elif qtype == "multiple_choice":
                    answer_multiple_choice(driver, question, options, known, attempted)
                elif qtype == "word_translation":
                    answer_word_translation(driver, question, options, known, attempted, state)
                elif qtype == "fill_the_blank":
                    answer_fill_the_blank(driver, question, options, known)
                else:
                    answer_matching(driver, state, known_pairs)
            except (NoSuchElementException, StaleElementReferenceException) as e:
                print(f"  tap failed ({type(e).__name__}), retrying next cycle")
                continue
            if wait_for_sheet(driver, state):
                sheetless_question, sheetless_count = None, 0
            else:
                print("  no feedback sheet appeared within 12s")
                if question == sheetless_question:
                    sheetless_count += 1
                else:
                    sheetless_question, sheetless_count = question, 1
                if sheetless_count >= 2:
                    save_stuck_screen(driver)
                    raise StuckScreenError(f"answers change nothing on: {question!r}")
            idle_since = time.time()
            continue

        # Unrecognized screen: first make sure it is still the app at all.
        # A crash drops the phone to the launcher, where blind popup taps
        # or the chest-position rescue could open unrelated apps (found
        # the hard way — see stuck_screen.xml from 2026-08-02).
        if driver.current_package != config.APP_PACKAGE:
            raise AppLostError(driver.current_package)

        # An offer popup may be in the way, or it's a finish screen
        # (Retry / Next ...) or the next quiz's Start page.
        if dismiss_popup(driver):
            idle_since = time.time()
            continue
        if tap_forward_button(driver):
            idle_since = time.time()
            continue

        if time.time() - idle_since > IDLE_LIMIT:
            save_stuck_screen(driver)
            raise StuckScreenError(f"unrecognized screen for over {IDLE_LIMIT}s")
        time.sleep(0.5)

    print(f"\nDone. {state['correct']} correct, {state['incorrect']} incorrect this run.")
    print(f"Results saved in {watcher.RESULTS_FILE}")


def adb_shell(*args):
    """Run an adb shell command, trying the pinned device first. Best-effort."""
    for base in (["adb", "-s", config.DEVICE_NAME, "shell"], ["adb", "shell"]):
        try:
            subprocess.run(base + list(args), check=True, timeout=15, capture_output=True)
            return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


def wake_device():
    """Wake the phone and clear its swipe lock so the app can show.

    An unattended phone sleeps between runs and a locked screen makes
    every launch time out on the first home-screen element. The lock is
    swipe-only (no PIN): wake, collapse the notification shade (an open
    shade would swallow the swipe), then swipe up. All best-effort — a
    phone that is already awake and unlocked just ignores all of it.
    """
    if not adb_shell("input", "keyevent", "KEYCODE_WAKEUP"):
        print("adb not reachable — cannot wake the device")
        return False
    time.sleep(1)
    adb_shell("cmd", "statusbar", "collapse")
    adb_shell("wm", "dismiss-keyguard")
    adb_shell("input", "swipe", "360", "1300", "360", "300", "200")
    time.sleep(1)
    print("Device woken and unlocked (best effort)")
    return True


def clear_stale_instrumentation():
    """Kill the device-side UiAutomator2 server processes.

    A dead or leftover instrumentation (another session restarted it, an
    attach session dumped a screen, ...) fails commands with 'the
    instrumentation process is not running' and new sessions with 'The
    instrumentation process cannot be initialized'.
    """
    adb_shell("am", "force-stop", "io.appium.uiautomator2.server")
    adb_shell("am", "force-stop", "io.appium.uiautomator2.server.test")
    time.sleep(2)


def connect_fresh_session():
    """Start the app session, recovering from a stale device-side server."""
    try:
        return watcher.connect(attach=False)
    except watcher.CONNECTION_ERRORS:
        print("Session failed to start — clearing stale instrumentation and retrying...")
        clear_stale_instrumentation()
        return watcher.connect(attach=False)


def force_stop_app():
    """Kill the app via adb so every run starts from the app's home screen.

    Works even when the app was left open mid-test on the phone. Falls back
    to the session's forceAppLaunch capability when adb isn't reachable.
    """
    if adb_shell("am", "force-stop", config.APP_PACKAGE):
        print("App closed (adb force-stop)")
        time.sleep(1)
        return True
    print("adb force-stop not available — relying on forceAppLaunch")
    return False


def main():
    started = time.time()
    relaunches = APP_RELAUNCHES
    try:
        while True:
            driver = None

            # Always close the session: an orphaned session wedges the
            # Appium server and breaks the next script that talks to the
            # same device.
            try:
                wake_device()
                force_stop_app()
                driver = connect_fresh_session()
                time.sleep(3)

                wait = WebDriverWait(driver, 20)
                wait_long = WebDriverWait(driver, 30)
                if navigate_to_test(driver, wait, wait_long):
                    answer_until_done(driver)
                return
            except (AppLostError, StuckScreenError) + watcher.CONNECTION_ERRORS as e:
                if relaunches == 0:
                    print("Still stuck after several app restarts — giving up.")
                    return
                relaunches -= 1
                if isinstance(e, AppLostError):
                    reason = "left the foreground"
                elif isinstance(e, StuckScreenError):
                    reason = "is stuck"
                else:
                    # e.g. the device-side instrumentation died
                    # mid-navigation (another runner on the same phone
                    # restarts it too).
                    reason = f"lost the device connection ({type(e).__name__})"
                    clear_stale_instrumentation()
                print(f"The app {reason} ({e}) — restarting it...")
            except KeyboardInterrupt:
                print("\nStopped by user.")
                return
            finally:
                if driver is not None:
                    watcher.safe_quit(driver)
    finally:
        elapsed = time.time() - started
        print(f"Total wall time: {elapsed / 60:.1f} minutes "
              f"({elapsed / 3600:.2f} hours)")


if __name__ == "__main__":
    main()
