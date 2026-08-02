import re
import time
import xml.etree.ElementTree as ET

from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)

import locators as loc
import config
from question_handler import (
    OPTION_IGNORE,
    looks_like_promo,
    parse_screen,
    xpath_literal,
)

# Labels a close (X) icon may carry on offer/promo popups
CLOSE_ICON_DESCS = ("X", "x", "✕", "×", "Close", "close")
# Buttons never worth tapping when pushing through an unknown screen:
# backwards navigation, popup closers, the report icon, and Retry — which
# restarts an already-finished test instead of moving forward
SKIP_BUTTONS = ("Go back", "Back", "null", "Retry") + CLOSE_ICON_DESCS
# Paywall/promo call-to-action buttons ("IELTSGA GOO!", "Subscribe ·
# 318 000 soums", the Yillik/Oylik plan cards): tapping one leads deeper
# into the subscription flow, never forward through the course.
PROMO_CTA_MARKERS = ("IELTSGA", "VAQT TOPILAVERADI", "Subscribe", "soums")

BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
# The home screen's bottom navigation — its top-left gear icon is also an
# unlabeled clickable, so blind-closing there would open settings instead.
HOME_NAV_DESCS = {"Main", "Learn", "Profile"}

# While a stuck screen is waited out (e.g. a lesson video still loading):
STUCK_POLL = 2          # seconds between screen checks
STUCK_RETAP_EVERY = 10  # seconds between retries of the forward button
STUCK_REMIND_EVERY = 60 # seconds between reminders to tap manually
# Texts identifying the task-reward chest screens, which have no buttons
# and no X at all. Matched as substrings: the real screens' labels have
# never been captured in a dump, and the 2026-08-02 stranding proved they
# do not equal the expected strings exactly — merged descs and
# text-attribute rendering are both still possible.
CHEST_TAP_MARKER = "Tap on the chest"
CHEST_SCREEN_MARKERS = (CHEST_TAP_MARKER, "Get your reward", "Open chest")
# A screen with no forward button that also isn't moving is dead — give
# up on it (an app restart recovers) instead of waiting forever.
DEAD_SCREEN_LIMIT = 90


class StuckScreenError(Exception):
    """The app sat on a screen the runner can't move past (a chest/ad
    variant with unreadable labels, a fake question whose answers change
    nothing, or a dead screen with nothing to tap). Restarting the app
    skips it."""


def tap(driver, waiter, locator, label):
    el = waiter.until(EC.presence_of_element_located(locator))
    el.click()
    print(f"Tapped: {label}")
    time.sleep(0.5)


def last_lesson_desc(nodes):
    """The last 'Dars ...' / 'Test ...' item visible in a lessons list."""
    items = [d for _, d in nodes if d.startswith(("Dars ", "Test "))]
    return items[-1] if items else None


def find_close_icon(nodes):
    """The label of a popup's close (X) icon, or None."""
    return next((d for _, d in nodes if d in CLOSE_ICON_DESCS), None)


def on_chest_screen(nodes):
    """True when any chest-reward marker appears, even inside a merged desc."""
    return any(m in d for _, d in nodes for m in CHEST_SCREEN_MARKERS)


def chest_tap_caption(nodes):
    """True on the tap-the-chest screen itself (not the tasks screen)."""
    return any(
        CHEST_TAP_MARKER in d or "Get your reward" in d for _, d in nodes
    )


def blind_close_unsafe(nodes):
    """True on screens where tapping an unlabeled top-left icon would
    navigate away rather than close a popup: the home screen (its gear
    icon is an unlabeled top-left clickable too), finish/start pages
    (their back arrow sits where a popup's X would), and the chest-reward
    screens, which have no X at all — only their forward button/chest."""
    descs = [d for _, d in nodes if d]
    if HOME_NAV_DESCS.issubset(descs):
        return True
    if on_chest_screen(nodes):
        return True
    return any(d == "Start" or d.lower().startswith("next") for d in descs)


def find_unlabeled_close_center(xml):
    """Center (x, y) of a popup's unlabeled close (X) icon, or None.

    The streak and Pro-offer popups' X carries no accessibility label, so
    it can only be recognized geometrically: an icon-sized clickable with
    no label in the top-left corner of the screen. Question screens are
    immune — their unlabeled top bar spans the full width.
    """
    nodes = []
    for el in ET.fromstring(xml).iter():
        m = BOUNDS_RE.fullmatch(el.get("bounds") or "")
        if m:
            nodes.append((el, *map(int, m.groups())))
    if not nodes:
        return None
    width = max(n[3] for n in nodes)
    height = max(n[4] for n in nodes)
    for el, x1, y1, x2, y2 in nodes:
        if el.get("clickable") != "true" or el.get("content-desc"):
            continue
        icon_sized = 0 < x2 - x1 <= width * 0.25 and 0 < y2 - y1 <= height * 0.12
        top_left = x2 <= width * 0.4 and y2 <= height * 0.15
        if icon_sized and top_left:
            return (x1 + x2) // 2, (y1 + y2) // 2
    return None


def is_promo_cta(desc):
    return any(m in desc for m in PROMO_CTA_MARKERS)


def looks_like_known_popup(descs):
    """True only on the popups whose unlabeled X may be blind-tapped.

    An icon-sized top-left unlabeled clickable is ambiguous: on the
    streak and promo popups it is the X, on ordinary screens it is a
    back arrow — blind-tapping one of those walked the runner out of the
    course to the Assigned-courses list (2026-08-02). So the geometric X
    is only trusted when the screen's texts identify a known popup.
    """
    if looks_like_promo(descs):
        return True
    if any(d == "Day" for d in descs):  # the streak popup's day counter
        return True
    return any("Parvoz" in d or "Last week" in d for d in descs)


def candidate_buttons(nodes):
    """Buttons worth tapping when pushing through an unknown screen."""
    return [
        d for cls, d in nodes
        if cls == "android.widget.Button" and d
        and d not in SKIP_BUTTONS and not is_promo_cta(d)
    ]


def looks_like_question(nodes):
    """True when the screen resembles a question: a text plus 2+ options."""
    question = next((d for c, d in nodes if c == "android.view.View" and d), None)
    options = [
        d for c, d in nodes
        if c == "android.widget.Button" and d and d not in OPTION_IGNORE
    ]
    return bool(question) and len(options) >= 2


def find_forward_button(nodes):
    """The button that moves forward on between-screens.

    Test-finish screens offer "Retry" and "Next ..." — always the Next
    one. The failed-test screen ("Sorry, your score is a little low!")
    offers ONLY "Try again" — retaking with the freshly learned answers
    IS the way forward there, unlike "Retry" next to a Next button. A
    quiz Start page offers "Start". Popups whose X icon has no label
    (e.g. the streak popup) offer "Continue", and the task-reward screen
    offers "Open chest". Returns the label, or None.
    (The feedback sheet's exact "Next" is excluded — poll_once handles it.)
    """
    for _, d in nodes:
        if d.lower().startswith("next") and d != "Next":
            return d
    for label in ("Start", "Continue", "Open chest", "Try again"):
        if any(d == label for _, d in nodes):
            return label
    # "Open chest" merged into a longer desc: return the full desc so the
    # content-desc xpath still finds the node to tap.
    return next((d for _, d in nodes if "Open chest" in d), None)


def tap_chest(driver):
    """Open the chest on the "Tap on the chest!" reward screen.

    That screen has no buttons and no X — the chest image mid-screen is
    the only way forward. The chest spans roughly the 47–62% band of the
    screen height, centered horizontally, so tap the center column at
    those heights until the screen changes. (Compared as raw trees: the
    caption desc can't be trusted to exist on the real screen.)
    """
    before = driver.page_source
    width = height = 0
    for el in ET.fromstring(before).iter():
        m = BOUNDS_RE.fullmatch(el.get("bounds") or "")
        if m:
            width = max(width, int(m.group(3)))
            height = max(height, int(m.group(4)))
    if not width or not height:
        return False
    for frac in (0.55, 0.47, 0.62):
        center = (width // 2, int(height * frac))
        driver.tap([center])
        print(f"Tapped the reward chest at {center}")
        time.sleep(1.5)
        if driver.page_source != before:
            return True
    return False


def tap_forward_button(driver):
    """On a finish/start/reward screen, tap whatever moves forward."""
    nodes = parse_screen(driver.page_source)
    label = find_forward_button(nodes)
    if not label:
        if chest_tap_caption(nodes):
            return tap_chest(driver)
        return False
    try:
        xpath = f"//*[@content-desc={xpath_literal(label)}]"
        driver.find_element(AppiumBy.XPATH, xpath).click()
        print(f"Tapped: {label}")
        time.sleep(1)
        return True
    except (NoSuchElementException, StaleElementReferenceException):
        return False


def dismiss_popup(driver):
    """If a popup with a close (X) icon is on screen, tap the X.

    A labeled icon is found by name. The streak and Pro-offer popups' X
    has no label, so it is tapped by position instead — except on screens
    where a blind top-left tap would navigate away (home, finish/start).
    The full-screen IELTS promo interstitial has no X at all (in any
    form), so a promo screen with no X left to try is dismissed with the
    Android back button — verified to land back on the covered screen.
    """
    xml = driver.page_source
    nodes = parse_screen(xml)
    icon = find_close_icon(nodes)
    if icon:
        try:
            xpath = f"//*[@content-desc={xpath_literal(icon)}]"
            driver.find_element(AppiumBy.XPATH, xpath).click()
            print(f"Dismissed popup via '{icon}'")
            time.sleep(1)
            return True
        except (NoSuchElementException, StaleElementReferenceException):
            return False
    descs = [d for _, d in nodes if d]
    if blind_close_unsafe(nodes) or not looks_like_known_popup(descs):
        return False
    center = find_unlabeled_close_center(xml)
    if center:
        driver.tap([center])
        print(f"Dismissed popup via unlabeled X at {center}")
        time.sleep(1)
        return True
    if looks_like_promo(descs):
        driver.back()
        print("Promo screen with no X — pressed the Android back button")
        time.sleep(1)
        return True
    return False


def tap_through_buttons(driver):
    """Unknown screen: tap its buttons one by one until the screen changes.

    Used to move forward when the sequence lands somewhere unexpected
    (e.g. a video lesson page). Returns True as soon as a tap changed
    the screen.
    """
    nodes = parse_screen(driver.page_source)
    before = [d for _, d in nodes if d]
    buttons = candidate_buttons(nodes)
    if not buttons:
        print("No buttons to tap on this screen")
        return False

    for text in buttons:
        try:
            xpath = f"//android.widget.Button[@content-desc={xpath_literal(text)}]"
            driver.find_element(AppiumBy.XPATH, xpath).click()
            print(f"Tapped: {text}")
        except (NoSuchElementException, StaleElementReferenceException):
            continue
        time.sleep(1.5)
        now = [d for _, d in parse_screen(driver.page_source) if d]
        if now != before:
            print("Screen changed")
            return True
    return False


def forward_tap_label(nodes):
    """The label to re-tap while waiting out a stuck screen.

    Like find_forward_button, but a plain "Next" counts too: the lesson
    pages' button is exactly "Next", and no feedback sheet is involved
    when a screen is being waited out.
    """
    for _, d in nodes:
        if d.lower().startswith("next"):
            return d
    for label in ("Start", "Continue", "Open chest", "Try again"):
        if any(d == label for _, d in nodes):
            return label
    return next((d for _, d in nodes if "Open chest" in d), None)


def retap_forward(driver, nodes):
    """Best-effort re-tap of a stuck screen's forward button."""
    label = forward_tap_label(nodes)
    if not label:
        return
    try:
        xpath = f"//*[@content-desc={xpath_literal(label)}]"
        driver.find_element(AppiumBy.XPATH, xpath).click()
        print(f"Re-tapped: {label}")
    except (NoSuchElementException, StaleElementReferenceException):
        pass


def wait_for_manual_advance(driver):
    """Wait out a screen that refuses to move forward.

    Happens on lesson pages whose video is still loading — the Next tap
    is swallowed until the page is ready. The forward button is re-tapped
    every few seconds in case the page finished loading, and a manual tap
    on the phone works too. Returns True once the screen has changed.

    A screen with NO forward button at all that also isn't moving (e.g.
    the Assigned-courses list after the 2026-08-02 back-arrow escape) is
    dead — waiting can't help, so give up after DEAD_SCREEN_LIMIT and
    let the app restart recover.
    """
    before = [d for _, d in parse_screen(driver.page_source) if d]
    print("Screen is not moving forward (video still loading?) — waiting.")
    print("Re-tapping it periodically; you can also tap the button on the phone...")
    started = last_tap = last_remind = time.time()
    while True:
        time.sleep(STUCK_POLL)
        nodes = parse_screen(driver.page_source)
        if [d for _, d in nodes if d] != before:
            print("Screen changed — continuing")
            return True
        now = time.time()
        if forward_tap_label(nodes) is None:
            if now - started >= DEAD_SCREEN_LIMIT:
                print("Nothing to tap and nothing moving — giving up on this screen")
                return False
            continue
        if now - last_tap >= STUCK_RETAP_EVERY:
            last_tap = now
            retap_forward(driver, nodes)
        if now - last_remind >= STUCK_REMIND_EVERY:
            last_remind = now
            print("Still waiting — tap the forward button on the phone to continue...")


def wait_for_lessons_list(driver, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        last = last_lesson_desc(parse_screen(driver.page_source))
        if last:
            return last
        time.sleep(0.5)
    return None


def fling_lessons_to_end(driver):
    """Fling the lessons list to its very bottom.

    A fresh course entry always opens the list at the top of the module,
    so once progress moves past the first screenful every visible item
    is already completed — tapping one reopens an old lesson with no
    sequence reminder. Descs carry no locked/completed marker, so the
    only safe tap target is the true last item, and it stays locked
    until the course is finished. (flingToEnd, unlike scrollToEnd,
    covers the whole list in a few seconds.)
    """
    try:
        driver.find_element(
            AppiumBy.ANDROID_UIAUTOMATOR,
            "new UiScrollable(new UiSelector().scrollable(true)).flingToEnd(40)",
        )
        time.sleep(0.5)
        return True
    except (NoSuchElementException, StaleElementReferenceException):
        return False


def open_next_in_sequence(driver):
    """Open whatever lesson/test the course sequence says is next.

    The list is flung to its very end, and the last item tapped. Tapping
    a locked item triggers the "You must study the lessons in sequence!"
    reminder; its "Next lesson" button opens the right item.
    """
    if not wait_for_lessons_list(driver):
        print("No lessons found in the list")
        return False

    if not fling_lessons_to_end(driver):
        print("Could not fling the lessons list — using the visible items")
    last = last_lesson_desc(parse_screen(driver.page_source))
    if not last:
        print("No lessons found in the list")
        return False

    xpath = f"//*[@content-desc={xpath_literal(last)}]"
    driver.find_element(AppiumBy.XPATH, xpath).click()
    print(f"Tapped last item: {last.splitlines()[0]}")

    try:
        btn = WebDriverWait(driver, 8).until(
            EC.element_to_be_clickable(loc.NEXT_LESSON)
        )
        btn.click()
        print("Tapped: Next lesson (sequence reminder)")
        time.sleep(1)
    except TimeoutException:
        # No reminder: the tapped item was unlocked and opened directly.
        print("No sequence reminder — item opened directly")
    return True


def clear_launch_popups(driver, rounds=5):
    """Close popups that cover the home screen right after app launch
    (streak screen, Pro offer, the chest-reward flow) — X icon first,
    forward button as fallback. The chest flow alone takes three rounds
    (Open chest → tap the chest → Continue); unused rounds cost nothing
    because the loop returns as soon as nothing was closed or tapped."""
    for _ in range(rounds):
        if dismiss_popup(driver) or tap_forward_button(driver):
            time.sleep(1)
        else:
            return


def navigate_to_test(driver, wait, wait_long):
    """Returns True when the question screen is reached, False otherwise."""
    clear_launch_popups(driver)
    tap(driver, wait, loc.PROGRAM_CERTIFICATE, "Program Certificate")
    tap(driver, wait, loc.GET_CERTIFICATE, "Get certificate")

    try:
        WebDriverWait(driver, 15).until(
            EC.invisibility_of_element_located(loc.PROGRESS_BAR)
        )
        print("Spinner gone, list loaded")
    except TimeoutException:
        print("Spinner still visible after 15s, continuing anyway")

    time.sleep(1)

    course_selector = (
        AppiumBy.ANDROID_UIAUTOMATOR,
        f'new UiSelector().description("{config.COURSE_DESCRIPTION}")'
    )
    tap(driver, wait_long, course_selector, "Course selected")

    if not open_next_in_sequence(driver):
        return False

    push_through_to_start(driver)
    print("Reached the question screen!")
    return True


def push_through_to_start(driver, attempts=5):
    """Get from wherever the sequence landed to the questions.

    Each attempt: close any popup, tap Start if it's there, and otherwise
    tap through the screen's buttons to move forward. Also succeeds when
    questions have already started (no Start screen in between). Five
    attempts leave room for the three-screen chest-reward flow plus a
    Start page on the way in.

    When a whole round of attempts moves nothing (e.g. a lesson page whose
    video is still loading and swallows every Next tap), the screen is
    waited out — re-tapped periodically, and a manual tap on the phone
    works too — then pushing resumes. Only a dead screen (nothing to tap,
    nothing moving) raises StuckScreenError so the app restart recovers.
    """
    while True:
        for _ in range(attempts):
            dismiss_popup(driver)

            try:
                tap(driver, WebDriverWait(driver, 5), loc.START_TEST, "Start test")
                return True
            except TimeoutException:
                pass

            if looks_like_question(parse_screen(driver.page_source)):
                print("Questions already started")
                return True

            # A finish/start/streak screen: its forward button (Next ... /
            # Start / Continue) beats blind-tapping in tree order.
            if tap_forward_button(driver):
                continue

            print("No Start button — tapping through this screen...")
            tap_through_buttons(driver)

        if not wait_for_manual_advance(driver):
            raise StuckScreenError("navigation stranded on a screen with nothing to tap")
