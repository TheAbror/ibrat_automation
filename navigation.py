import time

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
from question_handler import OPTION_IGNORE, parse_screen, xpath_literal

# Labels a close (X) icon may carry on offer/promo popups
CLOSE_ICON_DESCS = ("X", "x", "✕", "×", "Close", "close")
# Buttons never worth tapping when pushing through an unknown screen:
# backwards navigation, popup closers, and the report icon
SKIP_BUTTONS = ("Go back", "Back", "null") + CLOSE_ICON_DESCS


def tap(driver, waiter, locator, label):
    el = waiter.until(EC.presence_of_element_located(locator))
    el.click()
    print(f"Tapped: {label}")
    time.sleep(1)


def last_lesson_desc(nodes):
    """The last 'Dars ...' / 'Test ...' item visible in a lessons list."""
    items = [d for _, d in nodes if d.startswith(("Dars ", "Test "))]
    return items[-1] if items else None


def find_close_icon(nodes):
    """The label of a popup's close (X) icon, or None."""
    return next((d for _, d in nodes if d in CLOSE_ICON_DESCS), None)


def candidate_buttons(nodes):
    """Buttons worth tapping when pushing through an unknown screen."""
    return [
        d for cls, d in nodes
        if cls == "android.widget.Button" and d and d not in SKIP_BUTTONS
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

    Test-finish screens offer "Retry" and "Next ..." — always the Next one.
    A quiz Start page offers "Start". Returns the button label, or None.
    (The feedback sheet's exact "Next" is excluded — poll_once handles it.)
    """
    for _, d in nodes:
        if d.lower().startswith("next") and d != "Next":
            return d
    if any(d == "Start" for _, d in nodes):
        return "Start"
    return None


def tap_forward_button(driver):
    """On a finish/start screen, tap the button that moves forward."""
    label = find_forward_button(parse_screen(driver.page_source))
    if not label:
        return False
    try:
        xpath = f"//*[@content-desc={xpath_literal(label)}]"
        driver.find_element(AppiumBy.XPATH, xpath).click()
        print(f"Tapped: {label}")
        time.sleep(1.5)
        return True
    except (NoSuchElementException, StaleElementReferenceException):
        return False


def dismiss_popup(driver):
    """If a popup with a close (X) icon is on screen, tap the X."""
    icon = find_close_icon(parse_screen(driver.page_source))
    if not icon:
        return False
    try:
        xpath = f"//*[@content-desc={xpath_literal(icon)}]"
        driver.find_element(AppiumBy.XPATH, xpath).click()
        print(f"Dismissed popup via '{icon}'")
        time.sleep(1)
        return True
    except (NoSuchElementException, StaleElementReferenceException):
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


def wait_for_lessons_list(driver, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        last = last_lesson_desc(parse_screen(driver.page_source))
        if last:
            return last
        time.sleep(0.5)
    return None


def open_next_in_sequence(driver):
    """Open whatever lesson/test the course sequence says is next.

    Tapping a locked item triggers the "You must study the lessons in
    sequence!" reminder; its "Next lesson" button opens the right item.
    The last visible item is used because it is (almost) always locked.
    """
    last = wait_for_lessons_list(driver)
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


def navigate_to_test(driver, wait, wait_long):
    """Returns True when the question screen is reached, False otherwise."""
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

    if not push_through_to_start(driver):
        print(
            "Could not reach the question screen — the next item in sequence\n"
            "may need to be completed manually in the app. Then run main.py again."
        )
        return False

    print("Reached the question screen!")
    return True


def push_through_to_start(driver, attempts=3):
    """Get from wherever the sequence landed to the questions.

    Each attempt: close any popup, tap Start if it's there, and otherwise
    tap through the screen's buttons to move forward. Also succeeds when
    questions have already started (no Start screen in between).
    """
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

        print("No Start button — tapping through this screen...")
        tap_through_buttons(driver)
    return False
