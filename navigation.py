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
    WebDriverException,
)

import locators as loc
import config
from question_handler import (
    NEXT_LABELS,
    OPTION_IGNORE,
    SURVEY_TV_LABELS,
    looks_like_finish,
    looks_like_promo,
    looks_like_survey,
    looks_like_update_sheet,
    parse_screen,
    xpath_literal,
)

# Labels a close (X) icon may carry on offer/promo popups. "Dismiss" is
# the 30%-discount countdown interstitial's own name for its full-screen
# close surface (2026-08-04).
CLOSE_ICON_DESCS = ("X", "x", "✕", "×", "Close", "close", "Dismiss")
# Buttons never worth tapping when pushing through an unknown screen:
# backwards navigation, popup closers, the report icon, Retry — which
# restarts an already-finished test instead of moving forward — the
# update sheet's Update and the Play Store nag's Update/Learn more,
# which walk out to the Play Store, the language cross-sell sheet's CTA,
# which walks out to a different course, the Daily Reward sheet's CLAIM
# REWARD, an unproven CTA, and the video player's controls toggle:
# tapping it only flips an overlay on and off, which reads as "screen
# changed" and loops the tap-through forever (2026-08-04).
SKIP_BUTTONS = ("Go back", "Back", "null", "Retry",
                "Update", "Yangilash", "Learn more", "Men til oʻrganmoqchiman",
                "CLAIM REWARD",
                "Show player controls", "Hide player controls") + CLOSE_ICON_DESCS
# Paywall/promo call-to-action buttons ("IELTSGA GOO!", "Subscribe ·
# 318 000 soums", the Yillik/Oylik plan cards) and the payment sheet's
# "Toʻlovni amalga oshirish" (= make the payment — 2026-08-04, both
# apostrophe renderings): tapping one leads deeper into the
# subscription/payment flow, never forward through the course.
# "Chegirmadan" fences the discount interstitial's own CTA — but this
# button is only ever reached if its "Dismiss" close surface somehow
# fails first (dismiss_popup tries that before any button gets tapped
# at all), so an English render's CTA text has no confirmed fence here
# yet. Not guessed at, unlike the entries above: a wrong guess could
# fence out an unrelated legitimate button. Add its real text once an
# English-language dump of this screen exists.
PROMO_CTA_MARKERS = ("IELTSGA", "VAQT TOPILAVERADI", "Subscribe", "soums",
                     "Toʻlovni", "To'lovni", "Chegirmadan")
# The payment bottom sheet: a Flutter sheet over a "Scrim" whose only
# Button is the payment CTA. Closed like any bottom sheet — Android back.
PAYMENT_SHEET_MARKERS = ("Toʻlovni", "To'lovni")
# The "want to learn a language too?" cross-sell bottom sheet (client
# report, 2026-08-04 — stuck_screen_20260804_184527, captured mid a
# matching quiz: the runner had no dismisser for it, sat idle past the
# 10s limit, and the app restart that followed is what read on the
# client's phone as the quiz being frozen). Same Scrim-backed sheet shape
# as the payment sheet; its one Button offers to start a different
# course and must never be tapped. Closed the same way — Android back.
LANGUAGE_CROSS_SELL_MARKERS = ("Men til oʻrganmoqchiman",)
# The Play Store "Update available" nag (client photo, 2026-08-06 — no
# XML dump, so recognized by its own title text; a native system dialog
# rather than a Flutter sheet, so no Scrim desc is assumed). Its "Update"
# and "Learn more" buttons both lead out of the app and are never
# tapped — closed with the Android back button like the sheets above.
PLAY_STORE_UPDATE_MARKERS = ("Update available",)
# The "Daily Reward" streak-claim bottom sheet (client photo, 2026-08-06
# — no XML dump either). Seen covering the Profile screen; its CLAIM
# REWARD button is an unproven CTA, so it is never tapped and the sheet
# is closed the same way as the others — Android back.
DAILY_REWARD_MARKERS = ("Daily Reward",)
# The "How did you hear about us?" survey page: the labels that might
# submit or skip it, tried in order. Guesses — the page's tree has never
# been captured in a dump — so a page none of them clears falls through
# to the usual restart-and-dump, which captures the real labels. (Which
# option to choose lives with the detector: SURVEY_TV_LABELS.)
SURVEY_FORWARD = ("Continue", "Submit", "Next", "Done", "Skip")
# Labels that decline the ask-to-update bottom sheet, compared stripped
# and case-blind. Guesses too (same never-captured caveat as the survey);
# a sheet with none of them is closed with the Android back button — how
# any bottom sheet closes.
UPDATE_DECLINE = ("later", "not now", "skip", "cancel", "keyinroq")

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
# Swipes allowed when scrolling something below the fold into view. The
# screens involved are a few rows tall, so a handful always suffices.
REVEAL_SWIPES = 5
# A driver that cannot swipe (an older server, a fake driver) must not
# kill the run — the caller falls back to what is already on screen.
SWIPE_ERRORS = (WebDriverException, AttributeError)
# The home-screen card the run enters the course through, as its
# accessibility label reads. Kept next to the locator it mirrors.
HOME_CARD_DESC = loc.PROGRAM_CERTIFICATE[1]


class StuckScreenError(Exception):
    """The app sat on a screen the runner can't move past (a chest/ad
    variant with unreadable labels, a fake question whose answers change
    nothing, or a dead screen with nothing to tap). Restarting the app
    skips it."""


class ChestOverlayDetected(Exception):
    """A chest-reward screen is covering the tap target. Raised from
    inside the target wait so the clear runs the moment the chest is
    seen — waiting out the full presence timeout under a chest cost
    minutes per encounter (2026-08-04)."""


class DailyRewardOverlayDetected(Exception):
    """The Daily Reward sheet is covering the tap target. Raised from
    inside the target wait for the same reason as ChestOverlayDetected
    (2026-08-06): the sheet's animate-in can land after
    clear_launch_popups already found nothing to close, right as a
    tap() wait starts polling the card underneath it. Left to the
    except-TimeoutException fallback, closing it paid the wait's full
    20-30s every time (confirmed in run.log — every Daily Reward close
    that run went through the 'Cleared an overlay ... retrying' path,
    never the fast one) — the sub-second confirm loop in
    _closed_via_back never got a chance to matter."""


def presence_or_chest(locator):
    """Wait condition: the target element, else ChestOverlayDetected /
    DailyRewardOverlayDetected.

    The chest and Daily Reward markers are both unambiguous — no
    legitimate navigation screen carries either — so a covering chest
    or Daily Reward sheet never needs the full-patience timeout other
    (X-dismissable) popups get.
    """
    def check(driver):
        try:
            return driver.find_element(*locator)
        except NoSuchElementException:
            pass
        nodes = parse_screen(driver.page_source)
        if on_chest_screen(nodes):
            raise ChestOverlayDetected()
        if any(m in d for _, d in nodes for m in DAILY_REWARD_MARKERS):
            raise DailyRewardOverlayDetected()
        return False
    return check


def tap(driver, waiter, locator, label, clear_rounds=3):
    """Tap the located element, clearing overlays that cover it.

    The chest-reward flow pops at ANY point since the 2026-08-02 app
    update, and it is a multi-screen sequence (chest → stars → Continue)
    — one clear is not enough, so clearing loops until the target
    appears or nothing clears anymore.

    A chest is recognized mid-wait and its whole flow walked at once:
    paying the full presence timeout per flow screen kept the phone on
    "Tap on the chest!" for minutes (2026-08-04). The Daily Reward sheet
    gets the same early recognition (2026-08-06), closed the moment it's
    seen rather than only after the wait times out. Other overlays still
    keep the full-patience wait — the missing target is the only
    evidence they are in the way at all.
    """
    while True:
        try:
            if clear_rounds > 0:
                el = waiter.until(presence_or_chest(locator))
            else:
                el = waiter.until(EC.presence_of_element_located(locator))
            break
        except ChestOverlayDetected:
            clear_rounds -= 1
            print(f"Chest-reward flow covering '{label}' — clearing it now")
            clear_launch_popups(driver)
        except DailyRewardOverlayDetected:
            clear_rounds -= 1
            print(f"Daily Reward sheet covering '{label}' — closing it now")
            dismiss_popup(driver)
        except TimeoutException:
            if clear_rounds <= 0 or not (dismiss_popup(driver) or tap_forward_button(driver)):
                raise
            clear_rounds -= 1
            print(f"Cleared an overlay covering '{label}' — retrying")
    el.click()
    print(f"Tapped: {label}")
    time.sleep(0.5)


# A real lessons-list item: "Dars 68 A vs The | Articles", "Test 69.1 The
# article", ... — the number matters, or "Test completed" (the pass-stats
# screen) would count as a list item.
LESSON_ITEM_RE = re.compile(r"^(Dars|Test) \d")


def last_lesson_desc(nodes):
    """The last 'Dars N ...' / 'Test N ...' item visible in a lessons list."""
    items = [d for _, d in nodes if LESSON_ITEM_RE.match(d)]
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
    (The feedback sheet's exact "Next" is excluded — poll_once handles
    it. During navigation, where no sheet can be up, tap_plain_next
    covers the lesson pages' bare "Next".)
    """
    # The survey's Continue belongs to dismiss_survey alone (a blind
    # forward tap is swallowed until an option is chosen), and under the
    # update sheet the covered screen's forward button is still in the
    # tree but blocked. Each "successful" swallowed tap would reset the
    # idle timer — stuck forever. Their dismissers own these screens.
    descs = [d for _, d in nodes if d]
    if looks_like_survey(descs) or looks_like_update_sheet(descs):
        return None
    for _, d in nodes:
        if d.lower().startswith("next") and d != "Next":
            return d
    for label in ("Start", "Continue", "Open chest", "Try again"):
        if any(d == label for _, d in nodes):
            return label
    # The Modules list (the app falls back to it after some reward
    # flows, 2026-08-02): forward = the course's module card. The full
    # desc is returned because it carries live progress counts.
    if any(d == "Modules" for _, d in nodes):
        module = next(
            (d for _, d in nodes if d.startswith(config.MODULE_PREFIX)), None
        )
        if module:
            return module
    # The pass-stats screen ("Test completed" + See your answers,
    # 2026-08-04): its next-lesson Button renders disabled and
    # unlabeled, so "Lessons" — back to the list, where the sequence
    # is rejoined — is the only way forward it offers.
    if looks_like_finish(descs) and any(d == "Lessons" for _, d in nodes):
        return "Lessons"
    # "Open chest" merged into a longer desc: return the full desc so the
    # content-desc xpath still finds the node to tap.
    return next((d for _, d in nodes if "Open chest" in d), None)


def tap_chest(driver):
    """Open the chest on the "Tap on the chest!" reward screen.

    That screen has no buttons and no X — the chest image mid-screen is
    the only way forward. Measured from the 2026-08-02 screenshot, the
    chest is centered at ~62% of the screen height (spanning ~55–70%),
    centered horizontally, so tap the center column at those heights
    until the screen changes. (Compared as raw trees: the caption desc
    can't be trusted to exist on the real screen.)
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
    for frac in (0.62, 0.55, 0.68):
        center = (width // 2, int(height * frac))
        driver.tap([center])
        print(f"Tapped the reward chest at {center}")
        time.sleep(1.5)
        if driver.page_source != before:
            return True
    return False


def desc_center(xml, label):
    """Center (x, y) of the first node carrying this content-desc, or None."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    for el in root.iter():
        if el.get("content-desc") == label:
            m = BOUNDS_RE.fullmatch(el.get("bounds") or "")
            if m:
                x1, y1, x2, y2 = map(int, m.groups())
                return (x1 + x2) // 2, (y1 + y2) // 2
    return None


def tap_forward_button(driver):
    """On a finish/start/reward screen, tap whatever moves forward."""
    xml = driver.page_source
    nodes = parse_screen(xml)
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
        # The pass-stats screen: its tree kept SHOWING "Lessons" while
        # find_element missed it for the whole 10s idle budget, twice
        # on 2026-08-04 (a looping entry animation keeps the screen
        # from ever settling). The tree already says where the button
        # is — tap the spot.
        center = desc_center(xml, label)
        if center is None:
            return False
        driver.tap([center])
        print(f"Tapped: {label} (by position)")
        time.sleep(1)
        return True


def tap_desc(driver, label):
    """Tap the element carrying this content-desc. False when it's gone."""
    try:
        xpath = f"//*[@content-desc={xpath_literal(label)}]"
        driver.find_element(AppiumBy.XPATH, xpath).click()
        return True
    except (NoSuchElementException, StaleElementReferenceException):
        return False


def tap_plain_next(driver):
    """Tap a lesson page's bare "Next" while navigating.

    find_forward_button hides exactly "Next" on purpose — on feedback
    sheets that button belongs to poll_once. But no sheet can be up
    during navigation, and the video lesson pages' forward button is
    exactly "Next": left untapped, the tap-through fallback pokes the
    video's "Show player controls" toggle instead and counts the
    overlay flipping as progress, forever (2026-08-04). The survey and
    update sheet keep their immunity — their forward buttons belong to
    their dismissers, and a swallowed tap here would reset the
    push-through loop forever.
    """
    descs = [d for _, d in parse_screen(driver.page_source) if d]
    if looks_like_survey(descs) or looks_like_update_sheet(descs):
        return False
    label = next((d for d in descs if d in NEXT_LABELS), None)
    if label and tap_desc(driver, label):
        print(f"Tapped: {label} (lesson page)")
        time.sleep(1)
        return True
    return False


def dismiss_survey(driver, descs):
    """Answer the "How did you hear about us?" survey and move past it.

    The page pops up at unpredictable points. Its option Buttons make it
    look like a question, but no answer ever brings a feedback sheet, so
    the question machinery skips it (poll_once) and this handler owns
    it: choose the client's answer (TV), then tap whatever submits or
    skips the page. Succeeds only once the survey is actually gone — one
    that refuses to clear returns False, so the idle timer keeps running
    and the usual restart + dump captures the tree this handler guessed
    wrong about.
    """
    choice = next(
        (d for d in descs if d.strip().lower() in SURVEY_TV_LABELS), None
    )
    if choice and tap_desc(driver, choice):
        print(f"Survey: chose '{choice.strip()}'")
        time.sleep(0.5)
        descs = [d for _, d in parse_screen(driver.page_source) if d]
    if looks_like_survey(descs):
        for label in SURVEY_FORWARD:
            target = next((d for d in descs if d.strip() == label), None)
            if target and tap_desc(driver, target):
                print(f"Survey: tapped '{label}'")
                time.sleep(1)
                break
        descs = [d for _, d in parse_screen(driver.page_source) if d]
    if looks_like_survey(descs):
        return False
    print("Survey page cleared")
    return True


def dismiss_update_sheet(driver, descs):
    """Close the ask-to-update bottom sheet without updating.

    Updating is never the run's job — the Update button walks out to
    the Play Store — so it is never tapped (and SKIP_BUTTONS fences it
    out of the blind tap-through too). A decline button is tried first,
    then the Android back button, the standard way a bottom sheet
    closes. True only once the sheet is actually gone; one that refuses
    to close returns False so the idle timer keeps running and the
    usual restart + dump captures the real tree.
    """
    for label in UPDATE_DECLINE:
        target = next((d for d in descs if d.strip().lower() == label), None)
        if target and tap_desc(driver, target):
            print(f"Update sheet: declined via '{target.strip()}'")
            time.sleep(1)
            break
    else:
        driver.back()
        print("Update sheet: no decline button — pressed the Android back button")
        time.sleep(1)
    if looks_like_update_sheet([d for _, d in parse_screen(driver.page_source) if d]):
        return False
    print("Update sheet closed")
    return True


# Poll instead of a blind sleep after pressing back on a bottom sheet:
# most close in well under a second, and a fixed time.sleep(1) here paid
# that full second even when the close animation had already finished
# (2026-08-06 — made the Daily Reward sheet visibly slow to clear on
# every app launch). Same worst-case wait as before, just no longer paid
# on the common fast path.
BACK_CLOSE_POLL = 0.15
BACK_CLOSE_TIMEOUT = 1.0


def _closed_via_back(driver, markers, label):
    """Press back, then confirm a marker-identified sheet is really gone.

    True only once the markers are actually absent — a sheet that
    refuses to close must still read as NOT dismissed so the idle timer
    keeps running into the recovering restart.
    """
    driver.back()
    print(f"{label} — pressed the Android back button")
    deadline = time.time() + BACK_CLOSE_TIMEOUT
    while True:
        still = [d for _, d in parse_screen(driver.page_source) if d]
        if not any(m in d for d in still for m in markers):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(BACK_CLOSE_POLL)


def dismiss_popup(driver):
    """If a popup with a close (X) icon is on screen, tap the X.

    A labeled icon is found by name. The streak and Pro-offer popups' X
    has no label, so it is tapped by position instead — except on screens
    where a blind top-left tap would navigate away (home, finish/start).
    The full-screen IELTS promo interstitial has no X at all (in any
    form), so a promo screen with no X left to try is dismissed with the
    Android back button — verified to land back on the covered screen.
    The hear-about-us survey has no X either — it is answered and
    submitted instead (dismiss_survey) — and the ask-to-update bottom
    sheet is declined or backed out of (dismiss_update_sheet). The
    payment sheet and the language-course cross-sell sheet share one
    shape (a Scrim-backed sheet with a single CTA Button that must never
    be tapped) and are both closed with the Android back button — as are
    the Play Store "Update available" nag and the Daily Reward
    streak-claim sheet, recognized by their own title text alone.
    """
    xml = driver.page_source
    nodes = parse_screen(xml)
    descs = [d for _, d in nodes if d]
    if looks_like_survey(descs):
        return dismiss_survey(driver, descs)
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
    if looks_like_update_sheet(descs):
        return dismiss_update_sheet(driver, descs)
    # The payment bottom sheet (2026-08-04): its "Toʻlovni amalga
    # oshirish" button must NEVER be tapped, and its unlabeled icon is
    # a payment logo, not an X. Back out, and only claim success once
    # the sheet is actually gone — one that refuses to close falls
    # through to the recovering restart with its tree saved.
    if any(d == "Scrim" for d in descs) and any(
            m in d for d in descs for m in PAYMENT_SHEET_MARKERS):
        return _closed_via_back(driver, PAYMENT_SHEET_MARKERS, "Payment sheet")
    if any(d == "Scrim" for d in descs) and any(
            m in d for d in descs for m in LANGUAGE_CROSS_SELL_MARKERS):
        return _closed_via_back(driver, LANGUAGE_CROSS_SELL_MARKERS,
                                 "Language cross-sell sheet")
    # A native system dialog, not a Flutter sheet — no Scrim desc to
    # check for. Its "Update"/"Learn more" buttons both lead out of the
    # app, so back is used directly rather than tapping either.
    if any(m in d for d in descs for m in PLAY_STORE_UPDATE_MARKERS):
        return _closed_via_back(driver, PLAY_STORE_UPDATE_MARKERS,
                                 "Play Store update nag")
    if any(m in d for d in descs for m in DAILY_REWARD_MARKERS):
        return _closed_via_back(driver, DAILY_REWARD_MARKERS, "Daily Reward sheet")
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
        # Success only once the promo is really gone: a back press the
        # screen swallows would otherwise be claimed as a dismissal
        # every cycle, resetting the idle timer forever and locking the
        # run out of its recovering restart.
        return not looks_like_promo(
            [d for _, d in parse_screen(driver.page_source) if d]
        )
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
    # Nothing to re-tap on the survey or under the update sheet — their
    # buttons are their dismissers' to press, and one that can't be
    # cleared should read as a dead screen so the wait gives up into the
    # recovering restart.
    descs = [d for _, d in nodes if d]
    if looks_like_survey(descs) or looks_like_update_sheet(descs):
        return None
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


def rejoin_lesson_sequence(driver):
    """On a lessons list, reopen whatever the sequence says is next.

    The answering loop lands here when the pass-stats screen's only
    enabled button, "Lessons", was its way forward (2026-08-04). The
    list offers that loop nothing to answer and nothing forward to
    tap, so reopening the sequence is what beats an app restart.
    """
    nodes = parse_screen(driver.page_source)
    if len([d for _, d in nodes if LESSON_ITEM_RE.match(d)]) < 2:
        return False
    return open_next_in_sequence(driver)


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


def card_on_screen(driver, desc):
    return any(d == desc for _, d in parse_screen(driver.page_source))


def scroll_down(driver):
    """One swipe up the middle of the screen. False when it can't swipe."""
    try:
        size = driver.get_window_size()
        x, height = size["width"] // 2, size["height"]
        driver.swipe(x, int(height * 0.75), x, int(height * 0.35), 600)
    except SWIPE_ERRORS:
        return False
    time.sleep(1)
    return True


def reveal_card(driver, desc):
    """Scroll until `desc` is in the element tree.

    UiAutomator2 leaves off-screen nodes out of the tree entirely, so
    anything below the fold cannot be waited for — it does not exist yet,
    and find_element fails instantly instead of settling. On a 1080x2340
    phone the home screen's collection grid puts the Program Certificate
    card below the screen, so scrolling is what makes it real. A card
    already in view costs no swipe.

    A chest-reward flow covering the screen is walked first: it cannot
    scroll, so swiping at it only burned the whole swipe budget while
    the chest sat there (2026-08-04).
    """
    for _ in range(REVEAL_SWIPES):
        nodes = parse_screen(driver.page_source)
        if any(d == desc for _, d in nodes):
            return True
        if on_chest_screen(nodes):
            clear_launch_popups(driver)
            continue
        if not scroll_down(driver):
            break
    return card_on_screen(driver, desc)


# The quiz Start page, which is also what a quiz looks like while it
# opens: tapping Start removes the button, and for a few seconds the
# card is all that is left — no button, no question, nothing to tap.
QUIZ_START_MARKERS = ("Press the start button", "Quizzes")


def looks_like_quiz_start(descs):
    """True on the quiz start card, with or without its Start button."""
    return any(m in d for d in descs for m in QUIZ_START_MARKERS)


def has_forward_control(nodes):
    """True when something on screen already moves the run forward.

    find_forward_button hides a plain "Next" deliberately — poll_once
    owns that button. For deciding whether to SCROLL, though, a visible
    "Next" absolutely counts: a lesson page carries one, and swiping
    past it drags it out of the tree, stranding the runner on a page it
    only had to tap (seen on a client's phone, 2026-08-04).
    """
    if find_forward_button(nodes):
        return True
    return any(d in NEXT_LABELS for _, d in nodes)


def reveal_forward_button(driver):
    """Scroll a too-tall screen until its forward button is in the tree.

    Same off-screen blindness as reveal_card, hit one screen deeper: a
    quiz Start page's button sits under the info card, so on a tall
    phone the runner sees no Start, no question and nothing to tap, and
    strands on a screen a human would simply scroll. Screens that
    already show a way forward are left alone.
    """
    for _ in range(REVEAL_SWIPES):
        if has_forward_control(parse_screen(driver.page_source)):
            return True
        if not scroll_down(driver):
            break
    return bool(find_forward_button(parse_screen(driver.page_source)))


def navigate_to_test(driver, wait, wait_long):
    """Returns True when the question screen is reached, False otherwise."""
    clear_launch_popups(driver)
    if not reveal_card(driver, HOME_CARD_DESC):
        print(f"'{HOME_CARD_DESC}' is not on the home screen — "
              "is the app signed in to the right account?")
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

            nodes = parse_screen(driver.page_source)
            if looks_like_question(nodes):
                print("Questions already started")
                return True

            # Knocked back to the Assigned-courses list (the app does
            # this on its own sometimes, e.g. after "Try again" on the
            # 2026-08-02 account): re-enter the course and rejoin the
            # sequence instead of dying as a dead screen.
            if any(d == "Assigned courses" for _, d in nodes):
                try:
                    xpath = f"//*[@content-desc={xpath_literal(config.COURSE_DESCRIPTION)}]"
                    driver.find_element(AppiumBy.XPATH, xpath).click()
                    print("Re-entered the course from the Assigned-courses list")
                    time.sleep(1)
                    if open_next_in_sequence(driver):
                        continue
                except (NoSuchElementException, StaleElementReferenceException):
                    pass

            # A lessons list (e.g. after the Modules-screen recovery
            # opened the module card): rejoin the lesson sequence — the
            # list itself has no Buttons, so tap-through dies on it.
            # Two items minimum: a lesson PAGE carries its own single
            # "Dars N ..." title and must be pushed through, not re-listed.
            items = [d for _, d in nodes if LESSON_ITEM_RE.match(d)]
            if len(items) >= 2 and open_next_in_sequence(driver):
                continue

            # A finish/start/streak screen: its forward button (Next ... /
            # Start / Continue) beats blind-tapping in tree order — and a
            # video lesson page's bare "Next" counts here too, since no
            # feedback sheet can be up during navigation.
            if tap_forward_button(driver) or tap_plain_next(driver):
                continue

            # Nothing actionable in the tree yet — the button may simply
            # be below the fold on a tall screen, where it is absent
            # rather than merely out of reach.
            if reveal_forward_button(driver) and (
                    tap_forward_button(driver) or tap_plain_next(driver)):
                continue

            print("No Start button — tapping through this screen...")
            tap_through_buttons(driver)

        if not wait_for_manual_advance(driver):
            raise StuckScreenError("navigation stranded on a screen with nothing to tap")
