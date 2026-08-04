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

Usage: python3 main.py — supervised, self-healing run (via supervisor.py).
       python3 main.py --worker — this bare runner (needs your own Appium).
"""
import math
import re
import subprocess
import sys
import time
from datetime import datetime

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.common.actions import interaction
from selenium.webdriver.common.actions.action_builder import ActionBuilder
from selenium.webdriver.common.actions.pointer_input import PointerInput
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
    looks_like_quiz_start,
    navigate_to_test,
    rejoin_lesson_sequence,
    reveal_forward_button,
    tap_forward_button,
)
from question_handler import (
    build_answer_map,
    build_pair_map,
    chip_sequence,
    choose_mc_option,
    classify_sheet,
    detect_question_type,
    judge_pair_attempt,
    looks_like_finish,
    pair_attempt_order,
    parse_cards,
    parse_screen,
    split_matching_columns,
    xpath_literal,
)

IDLE_LIMIT = 10      # seconds on an unrecognized screen -> restart the app
# The quiz start card, stripped of its button, is what a quiz looks like
# while it opens. That is a healthy transition, not a stranding, so it
# gets its own far longer allowance.
LOADING_IDLE_LIMIT = 90
MAX_QUESTIONS = 5000  # a whole course in one run (~88 quizzes + retakes)
APP_RELAUNCHES = 50  # app restarts per run before giving up
# Accuracy governor: secure the floor first, then steer into the 93–96%
# band. No known answer is deliberately missed until enough correct
# answers are banked that the final score stays >= SCORE_FLOOR of the
# whole course even if every remaining question went wrong. Once that
# floor is secured, a known answer is missed ONLY when answering it
# correctly would push the run's accuracy above ACCURACY_HIGH; at or
# below it, every known answer is played straight (so the score can
# climb back whenever natural misses on unseen questions drag it down).
# Equilibrium sits at ~95%, safely under the 96% cap. Set ACCURACY_HIGH
# = 1.0 to always answer as well as possible.
ACCURACY_HIGH = 0.95
TOTAL_QUESTIONS = 880  # course size (~88 quizzes x ~10 questions)
SCORE_FLOOR = 0.88     # worst-case final score that must stay secured

# This run's sheet results, across app restarts (module-level so every
# auto_answer_loop session adds to the same tally).
RUN_STATS = {"correct": 0, "incorrect": 0}


def should_miss(correct, incorrect):
    """True when a correct answer NOW would lift accuracy past the cap."""
    if correct < math.ceil(SCORE_FLOOR * TOTAL_QUESTIONS):
        return False
    return (correct + 1) / (correct + incorrect + 1) > ACCURACY_HIGH
# Where the tree of an unrecognized screen is saved before restarting
STUCK_SCREEN_FILE = "stuck_screen.xml"
# Every restart and give-up, one block each. Small enough to email, and
# the only record that survives a console window being closed — which is
# how a run's history reaches whoever has to explain it.
PROBLEM_LOG = "problems.log"
# Where the run currently is, so a problem can say more than what broke.
CONTEXT = {"phase": "starting up", "question": None, "answered": 0}
# The tree file the most recent stranding wrote, so the incident that
# follows can point at it. One slot, in a list so save_stuck_screen can
# set it from anywhere.
LAST_STUCK_SCREEN = [None]


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
        # No settle between chips: each find_element round-trips to the
        # device anyway, and UiAutomator2 waits for the UI to idle
        # before locating — that spacing is what keeps taps reliable.
        try:
            tap_text(driver, word)
        except (NoSuchElementException, StaleElementReferenceException):
            continue
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
# Selection pause between the two taps of an attempt (runs device-side
# inside the batched request) and the settle before re-reading the board.
# The re-read tolerates a too-early snapshot — a lock that hasn't
# rendered yet just reads as "not a pair" and the attempt is retried —
# so trimming these risks a wasted attempt, never a stranding.
PAIR_TAP_PAUSE = 0.2
MATCH_SETTLE = 0.4
# Judging one attempt: poll the board until it reaches a state the
# attempt explains (reset or the tapped pair locked), give up into
# "moved" after VERDICT_TIMEOUT. A lock only counts once it has held for
# MATCHED_HOLD — a wrong-pair flash also takes the tapped cards out of
# play for a few frames, but a flash always ends in a reset while a real
# lock persists. The hold costs ~2 extra dumps per REAL match only;
# wrong attempts (the majority) return on their first settled read.
VERDICT_TIMEOUT = 2.5
VERDICT_POLL = 0.25
MATCHED_HOLD = 0.5


def tap_pair(driver, first, second):
    """Both taps of a matching attempt in ONE actions request.

    One Wi-Fi round trip instead of two, and the selection pause runs on
    the device instead of the host. Falls back to two plain taps when
    the batched call is unavailable (fake drivers) or rejected.
    """
    try:
        actions = ActionBuilder(
            driver, mouse=PointerInput(interaction.POINTER_TOUCH, "touch")
        )
        pointer = actions.pointer_action
        pointer.move_to_location(*first)
        pointer.pointer_down()
        pointer.pointer_up()
        pointer.pause(PAIR_TAP_PAUSE)
        pointer.move_to_location(*second)
        pointer.pointer_down()
        pointer.pointer_up()
        actions.perform()
    except (AttributeError, WebDriverException):
        driver.tap([first])
        time.sleep(PAIR_TAP_PAUSE)
        driver.tap([second])


def pair_verdict(driver, before_cards, left_label, right_label):
    """Judge one matching attempt by polling the board until it settles.

    'sheet' — the feedback sheet is up (this attempt completed the board),
    'matched' — the tapped pair locked, 'reset' — not a pair, 'moved' —
    the board changed in a way this attempt can't explain (a lock
    rendering late, an animation that never settled): coordinates are
    stale, re-read the board and start a fresh round.

    A reset is accepted at once. A lock only counts after holding for
    MATCHED_HOLD: the wrong-pair flash also takes the tapped cards out
    of play for a few frames — judging on a single frame is what
    recorded 16 phantom pairs on one board (results.json n=986,
    2026-08-03) and re-tried the same wrong cards until the attempt
    budget died.
    """
    deadline = time.time() + VERDICT_TIMEOUT
    matched_since = None
    while True:
        xml = driver.page_source
        if classify_sheet([d for _, d in parse_screen(xml) if d]) is not None:
            return "sheet"
        verdict = judge_pair_attempt(
            before_cards, parse_cards(xml), left_label, right_label
        )
        now = time.time()
        if verdict == "reset":
            return "reset"
        if verdict == "matched":
            if matched_since is None:
                matched_since = now
            elif now - matched_since >= MATCHED_HOLD:
                return "matched"
        else:
            matched_since = None
        if now >= deadline:
            # even a matched frame is untrusted without its hold: a
            # phantom pair recorded here would poison the answer book,
            # while "moved" merely re-reads the board — a real lock
            # shows up in that fresh read.
            return "moved"
        time.sleep(VERDICT_POLL)


def answer_matching(driver, state, known_pairs):
    """Match pairs one attempt at a time, tapping cards by position.

    Labels repeat on the category boards ("Singular"/"Plural" twice
    each), so text taps are ambiguous — they land on the first tree
    instance with that label, including an already-locked card, which
    swallows the tap (the 2026-08-02 category-board stranding). Cards
    are therefore tapped by their coordinates and the columns told apart
    by geometry. Each attempt is judged by pair_verdict against the
    board read at the start of the round — safe because the round only
    continues through verified resets, and abandoned for a fresh read on
    anything else. Pairs judged wrong go to the back of the try order
    for the rest of the board. Discovered pairs are left in
    state["pending_pairs"] so the sheet logger saves them for next time.
    """
    found = []
    state["pending_pairs"] = found
    budget = MATCH_ATTEMPTS
    skip = 0
    announced = False
    failed = set()

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
        matched = False
        for right in pair_attempt_order(left["label"], rights, known_pairs, failed):
            budget -= 1
            tap_pair(driver, (left["x"], left["y"]), (right["x"], right["y"]))
            time.sleep(MATCH_SETTLE)
            verdict = pair_verdict(driver, cards, left["label"], right["label"])
            if verdict == "sheet":
                found.append([left["label"], right["label"]])
                return True
            if verdict == "matched":
                print(f"  matched: {left['label']} + {right['label']}")
                found.append([left["label"], right["label"]])
                matched = True
                break
            if verdict == "moved":
                print("  board moved mid-attempt — re-reading")
                break
            print(f"  not a pair: {left['label']} + {right['label']}")
            failed.add((left["label"], right["label"]))
            if budget <= 0:
                break
        # a left whose rights all failed is skipped next round — the board
        # may need another pair locked first
        skip = 0 if matched else skip + 1
    return True


def log_problem(kind, reason, screen=None):
    """Append one incident — what broke, and where the run had got to.

    Best-effort by design: a diagnostic must never be the thing that
    ends a run, so every failure to write is swallowed.
    """
    try:
        with open(PROBLEM_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n[{datetime.now():%Y-%m-%d %H:%M:%S}] {kind}\n")
            f.write(f"  reason:   {reason}\n")
            f.write(f"  phase:    {CONTEXT['phase']}\n")
            f.write(f"  answered: {CONTEXT['answered']} question(s) this run\n")
            if CONTEXT["question"]:
                f.write(f"  on:       {CONTEXT['question']}\n")
            f.write(f"  device:   {config.DEVICE_NAME}\n")
            if screen:
                f.write(f"  screen:   {screen}\n")
    except OSError as e:
        print(f"(could not write {PROBLEM_LOG}: {e})")


def save_stuck_screen(driver, observed=None):
    """Save the tree of a screen the runner can't handle; return its path.

    The saved file is how a new stranding screen gets explained and then
    supported (it identified the 2026-08-02 launcher crash). No taps are
    tried here: blind taps on an unknown ad screen could open the ad.

    `observed` is the tree the runner actually stalled on. Re-reading the
    device here instead would capture whatever is on screen by now, and
    the app often resolves during the seconds the stall takes to detect —
    twice on 2026-08-03 the "stuck" file held an ordinary question screen,
    which is worse than useless: it hides the screen under investigation.

    Each stranding keeps its own timestamped file: a run that strands
    several times used to leave only the last, so the screen that started
    the trouble was already gone by the time anyone looked. The newest is
    copied to STUCK_SCREEN_FILE too, the path the docs point at.
    """
    tree = observed if observed is not None else driver.page_source
    stamped = f"stuck_screen_{datetime.now():%Y%m%d_%H%M%S_%f}.xml"
    for path in (stamped, STUCK_SCREEN_FILE):
        with open(path, "w", encoding="utf-8") as f:
            f.write(tree)
    print(f"Unrecognized screen — tree saved to {stamped}")
    LAST_STUCK_SCREEN[0] = stamped
    return stamped


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
    # The idle_since value the below-the-fold button hunt last ran for,
    # so one stuck episode costs one hunt.
    scroll_hunted_at = None
    # Same guard for backing out of a dead-end finish screen.
    backed_out_at = None
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
            CONTEXT.update(question=f"[{answered}] {qtype}: {question}",
                           answered=answered)
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
                    save_stuck_screen(driver, state.get("source"))
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

        # The pass-stats screen's "Lessons" (the tap above) lands on
        # the lessons list, which this loop otherwise has no move for.
        if rejoin_lesson_sequence(driver):
            idle_since = time.time()
            continue

        descs = [d for _, d in parse_screen(state.get("source") or "<hierarchy/>")]

        # The Retry-only pass-stats variant (2026-08-04 17:51) offers no
        # forward button at all — Retry would redo the finished quiz.
        # Back out to the lessons list; the rejoin above picks up the
        # sequence next cycle. Once per stuck episode, and WITHOUT
        # resetting the idle timer: a back press that changes nothing
        # must still end in the recovering restart.
        if backed_out_at != idle_since and looks_like_finish(descs):
            backed_out_at = idle_since
            driver.back()
            print("Finish screen with nothing forward — backing out to the lessons list")

        # Finishing a quiz lands on the next one's Start page, whose
        # button is below the fold on a tall phone and so missing from
        # the tree entirely. Scrolling it into view beats paying a whole
        # app restart between every pair of quizzes.
        #
        # Hunted at most once per stuck episode (idle_since changes only
        # when something moved): every swipe costs a second, so hunting
        # on each poll of a dead screen would burn the idle budget that
        # triggers the recovering restart.
        if scroll_hunted_at != idle_since:
            scroll_hunted_at = idle_since
            if reveal_forward_button(driver) and tap_forward_button(driver):
                # The hunt itself takes seconds, so by now the timer has
                # run down. Tapping IS progress — without this the next
                # poll restarts the app right after the successful tap.
                idle_since = time.time()
                continue

        # A quiz takes a while to open after Start, and while it does the
        # start card sits there stripped of its button — unrecognizable,
        # and healthy. Restarting the app at 10s killed it mid-transition
        # at every quiz boundary; left alone it opened in well under a
        # minute (watched on the phone, 2026-08-04).
        limit = LOADING_IDLE_LIMIT if looks_like_quiz_start(descs) else IDLE_LIMIT
        if time.time() - idle_since > limit:
            # A slow transition can eat the whole budget before the
            # settled screen gets a single look: the stats screen's
            # entry animation plus the idle-wait tax on every dump
            # left tap_forward_button zero attempts on the final
            # screen (2026-08-04, twice). One last chance on a fresh
            # dump before the restart hammer.
            if tap_forward_button(driver) or rejoin_lesson_sequence(driver):
                idle_since = time.time()
                continue
            save_stuck_screen(driver, state.get("source"))
            raise StuckScreenError(f"unrecognized screen for over {limit}s")
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


def adb_capture(*args):
    """Same as adb_shell, but returns the command's output ('' on failure)."""
    for base in (["adb", "-s", config.DEVICE_NAME, "shell"], ["adb", "shell"]):
        try:
            done = subprocess.run(base + list(args), check=True, timeout=15,
                                  capture_output=True, text=True)
            return done.stdout
        except (OSError, subprocess.SubprocessError):
            continue
    return ""


def keyguard_is_up():
    """True unless the phone is known to be unlocked already.

    Unknown counts as locked: a stray swipe on an unlocked phone is a
    smaller problem than a phone that stays locked all run.
    """
    return "mDreamingLockscreen=false" not in adb_capture("dumpsys", "window")


def screen_size():
    match = re.search(r"(\d+)x(\d+)", adb_capture("wm", "size"))
    return (int(match.group(1)), int(match.group(2))) if match else (720, 1600)


def wake_device():
    """Wake the phone and clear its swipe lock so the app can show.

    An unattended phone sleeps between runs and a locked screen makes
    every launch time out on the first home-screen element. The lock is
    swipe-only (no PIN): wake, collapse the notification shade (an open
    shade would swallow the swipe), then swipe up.

    The swipe only happens while the keyguard is actually up. It is a
    blind gesture: on an unlocked phone sitting on the launcher it drags
    the app drawer open instead, and since every app restart wakes the
    device again, a run that cannot start the app turns into a phone
    scrolling endlessly through its own app list.
    """
    if not adb_shell("input", "keyevent", "KEYCODE_WAKEUP"):
        print("adb not reachable — cannot wake the device")
        return False
    time.sleep(1)
    adb_shell("cmd", "statusbar", "collapse")
    if not keyguard_is_up():
        print("Device is awake and already unlocked")
        return True
    adb_shell("wm", "dismiss-keyguard")
    if keyguard_is_up():
        width, height = screen_size()
        adb_shell("input", "swipe", str(width // 2), str(int(height * 0.8)),
                  str(width // 2), str(int(height * 0.2)), "200")
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

                CONTEXT.update(phase="navigating into the course", question=None,
                               answered=0)
                wait = WebDriverWait(driver, 20)
                wait_long = WebDriverWait(driver, 30)
                if navigate_to_test(driver, wait, wait_long):
                    CONTEXT["phase"] = "answering questions"
                    answer_until_done(driver)
                    return 0
                # Navigation dead ends are retried like stuck screens —
                # under the supervisor exit 0 means "course done".
                raise StuckScreenError("navigation never reached the question screen")
            except (AppLostError, StuckScreenError) + watcher.CONNECTION_ERRORS as e:
                if isinstance(e, AppLostError):
                    reason = "left the foreground"
                elif isinstance(e, StuckScreenError):
                    reason = "is stuck"
                else:
                    # e.g. the device-side instrumentation died
                    # mid-navigation (another runner on the same phone
                    # restarts it too).
                    reason = f"lost the device connection ({type(e).__name__})"
                if relaunches == 0:
                    print("Still stuck after several app restarts — giving up.")
                    log_problem("gave up", f"app {reason} ({e}); "
                                f"no restarts left after {APP_RELAUNCHES}")
                    return 1
                relaunches -= 1
                if not isinstance(e, (AppLostError, StuckScreenError)):
                    clear_stale_instrumentation()
                log_problem("app restarted", f"app {reason} ({e})",
                            screen=LAST_STUCK_SCREEN[0])
                print(f"The app {reason} ({e}) — restarting it...")
            except KeyboardInterrupt:
                print("\nStopped by user.")
                return 130
            finally:
                if driver is not None:
                    watcher.safe_quit(driver)
    finally:
        elapsed = time.time() - started
        print(f"Total wall time: {elapsed / 60:.1f} minutes "
              f"({elapsed / 3600:.2f} hours)")


if __name__ == "__main__":
    if "--worker" in sys.argv:
        sys.exit(main())
    import supervisor
    sys.exit(supervisor.run())
