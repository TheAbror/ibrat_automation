"""Unit tests for the shared screen logic, watcher polling, and strategies.

Run with: python3 -m unittest test_watcher -v
"""
import os
import re
import tempfile
import unittest

from selenium.common.exceptions import (
    NoSuchElementException,
    StaleElementReferenceException,
    WebDriverException,
)

import question_handler as qh
import watcher


class FakeElement:
    def __init__(self, desc, click_fails=0):
        self._desc = desc
        self._click_fails = click_fails
        self.clicked = False

    def get_attribute(self, name):
        return self._desc

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True

    def click(self):
        if self._click_fails > 0:
            self._click_fails -= 1
            raise StaleElementReferenceException("element gone from DOM")
        self.clicked = True


class FakeDriver:
    def __init__(self, elements):
        self._elements = elements

    def find_elements(self, by, value):
        return self._elements

    def find_element(self, by, value):
        return self._elements[0]


class FakeTime:
    """Instant time for wait loops: sleep() just advances a fake clock."""

    def __init__(self, start=1000.0):
        self.now = start

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class TestLastLessonDesc(unittest.TestCase):
    def test_picks_last_dars_or_test_item(self):
        import navigation
        nodes = [
            ("android.view.View", "Lessons"),
            ("android.widget.Button", "Dars 68 A vs The | Articles\n9 minutes"),
            ("android.widget.Button", "Test 68.1 A / an vs The articles"),
            ("android.widget.Button", "Dars 69 The article\n14 minutes"),
            ("android.widget.Button", "Test 69.1 The article"),
            ("android.widget.Button", "null"),
        ]
        self.assertEqual(navigation.last_lesson_desc(nodes), "Test 69.1 The article")

    def test_returns_none_when_no_lessons(self):
        import navigation
        self.assertIsNone(navigation.last_lesson_desc([("android.view.View", "Lessons")]))

    def test_finish_screen_texts_are_not_lesson_items(self):
        # "Test completed" starts with "Test " but is the pass-stats
        # screen — treating it as a list item would fling and retap on a
        # screen that has no list.
        import navigation
        self.assertIsNone(navigation.last_lesson_desc([
            ("android.view.View", "Test completed"),
            ("android.view.View", "Test finished!"),
        ]))


# A fresh course entry always opens the list at the top of the module.
# Once progress moves past the first screenful, every visible item is
# already completed — descs carry no locked/completed marker, so the only
# safe target is the true last item, reached by flinging to the end.
LESSONS_TOP_XML = """<hierarchy>
  <node class="android.widget.ScrollView" bounds="[0,0][720,1600]" clickable="false" scrollable="true" content-desc=""/>
  <node class="android.view.View" bounds="[0,0][720,634]" clickable="true" content-desc="Lessons\n6 / \n86\n4 / \n88"/>
  <node class="android.view.View" bounds="[0,634][720,781]" clickable="true" content-desc="Dars 68 A vs The | Articles\n9 minutes"/>
  <node class="android.view.View" bounds="[0,1414][720,1558]" clickable="true" content-desc="Test 70.1  Go + prepositions"/>
</hierarchy>"""

LESSONS_END_XML = """<hierarchy>
  <node class="android.widget.ScrollView" bounds="[0,0][720,1600]" clickable="false" scrollable="true" content-desc=""/>
  <node class="android.view.View" bounds="[0,1198][720,1345]" clickable="true" content-desc="Dars 153 At, on, in (time)\n7 minutes"/>
  <node class="android.view.View" bounds="[0,1345][720,1488]" clickable="true" content-desc="Test 153 At, on, in (time)"/>
</hierarchy>"""


class TestOpenNextInSequence(unittest.TestCase):
    def test_flings_to_list_end_before_tapping_last_item(self):
        import navigation

        taps = []

        class ScrollDriver:
            def __init__(self):
                self.page_source = LESSONS_TOP_XML

            def find_element(self, by, value):
                if "flingToEnd" in value:
                    self.page_source = LESSONS_END_XML
                    return FakeElement("scrollable")
                el = FakeElement("item")
                el.click = lambda: taps.append(value)
                return el

        self.assertTrue(navigation.open_next_in_sequence(ScrollDriver()))
        self.assertTrue(any("Test 153" in t for t in taps), taps)
        self.assertFalse(any("Test 70.1" in t for t in taps), taps)

    def test_falls_back_to_visible_last_item_when_fling_fails(self):
        import navigation

        taps = []

        class NoScrollDriver:
            page_source = LESSONS_TOP_XML

            def find_element(self, by, value):
                if "flingToEnd" in value:
                    raise NoSuchElementException("no scrollable view")
                el = FakeElement("item")
                el.click = lambda: taps.append(value)
                return el

        self.assertTrue(navigation.open_next_in_sequence(NoScrollDriver()))
        self.assertTrue(any("Test 70.1" in t for t in taps), taps)


class TestRecoveryHelpers(unittest.TestCase):
    def test_find_close_icon(self):
        import navigation
        nodes = [
            ("android.view.View", "Special offer!"),
            ("android.widget.Button", "Buy now"),
            ("android.widget.Button", "X"),
        ]
        self.assertEqual(navigation.find_close_icon(nodes), "X")
        self.assertIsNone(navigation.find_close_icon(nodes[:2]))

    def test_candidate_buttons_skips_back_close_and_null(self):
        import navigation
        nodes = [
            ("android.view.View", "Dars 70 Go to work vs Go home"),
            ("android.widget.Button", "Go back"),
            ("android.widget.Button", "null"),
            ("android.widget.Button", "X"),
            ("android.widget.Button", "Play"),
            ("android.widget.Button", "Mark as done"),
        ]
        self.assertEqual(
            navigation.candidate_buttons(nodes), ["Play", "Mark as done"]
        )

    def test_candidate_buttons_never_include_retry(self):
        # Retry restarts a finished test — the blind tap-through fallback
        # must never press it, whatever the tree order.
        import navigation
        nodes = qh.parse_screen(FINISH_SCREEN_XML)
        self.assertEqual(navigation.candidate_buttons(nodes), ["Next lesson"])

    def test_tap_through_buttons_never_taps_retry(self):
        # Tree order on the finish screen puts Retry first — the blind
        # fallback must still skip it and tap Next lesson.
        import navigation

        taps = []

        class FinishDriver:
            page_source = FINISH_SCREEN_XML

            def find_element(self, by, value):
                el = FakeElement("btn")
                el.click = lambda: taps.append(value)
                return el

        navigation.tap_through_buttons(FinishDriver())
        self.assertTrue(all("Retry" not in t for t in taps), taps)
        self.assertTrue(any("Next lesson" in t for t in taps), taps)

    def test_find_forward_button_prefers_next_over_retry(self):
        import navigation
        finish_screen = [
            ("android.view.View", "Test finished!"),
            ("android.widget.Button", "Retry"),
            ("android.widget.Button", "Next lesson"),
        ]
        self.assertEqual(navigation.find_forward_button(finish_screen), "Next lesson")

    def test_find_forward_button_taps_start_page(self):
        import navigation
        start_page = [
            ("android.view.View", "Quizzes"),
            ("android.widget.Button", "Start"),
        ]
        self.assertEqual(navigation.find_forward_button(start_page), "Start")

    def test_find_forward_button_retries_a_failed_test(self):
        # "Try again" is the failed-test screen's only button — the way
        # forward is retaking with the learned answers. Without it the
        # runner burned an app restart on every first-attempt fail.
        import navigation
        nodes = qh.parse_screen(FAILED_QUIZ_XML)
        self.assertEqual(navigation.find_forward_button(nodes), "Try again")
        self.assertEqual(navigation.forward_tap_label(nodes), "Try again")

    def test_find_forward_button_continue_closes_streak_popup(self):
        import navigation
        streak_popup = [
            ("android.view.View", "3"),
            ("android.view.View", "Day"),
            ("android.view.View", "Parvoz boshlandi!"),
            ("android.widget.Button", "Last week"),
            ("android.widget.Button", "Continue"),
        ]
        self.assertEqual(navigation.find_forward_button(streak_popup), "Continue")

    def test_find_forward_button_ignores_plain_next_and_other_screens(self):
        import navigation
        sheet = [("android.widget.Button", "Next")]
        self.assertIsNone(navigation.find_forward_button(sheet))
        self.assertIsNone(navigation.find_forward_button([("android.view.View", "x")]))

    def test_looks_like_question(self):
        import navigation
        question_nodes = [
            ("android.view.View", "He's reading ___ interesting book."),
            ("android.widget.Button", "a"),
            ("android.widget.Button", "an"),
        ]
        video_nodes = [
            ("android.view.View", "Dars 70"),
            ("android.widget.Button", "Play"),
        ]
        self.assertTrue(navigation.looks_like_question(question_nodes))
        self.assertFalse(navigation.looks_like_question(video_nodes))


STREAK_POPUP_XML = """<hierarchy>
  <node class="android.widget.FrameLayout" bounds="[0,0][720,1600]" clickable="false" content-desc=""/>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[26,102][117,154]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[262,300][458,540]" clickable="false" content-desc="3"/>
  <node class="android.view.View" bounds="[300,560][420,600]" clickable="false" content-desc="Day"/>
  <node class="android.view.View" bounds="[180,1050][540,1100]" clickable="false" content-desc="Parvoz boshlandi!"/>
  <node class="android.widget.Button" bounds="[520,1140][690,1195]" clickable="true" content-desc="Last week"/>
  <node class="android.widget.Button" bounds="[42,1418][678,1502]" clickable="true" content-desc="Continue"/>
</hierarchy>"""

PRO_OFFER_XML = """<hierarchy>
  <node class="android.widget.FrameLayout" bounds="[0,0][720,1600]" clickable="false" content-desc=""/>
  <node class="android.view.View" bounds="[40,110][110,180]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[250,115][680,175]" clickable="false" content-desc="35 130+ subscribers"/>
  <node class="android.widget.Button" bounds="[42,700][678,800]" clickable="true" content-desc="Yillik\n26 500 uzs/oy\n318 000 soums"/>
  <node class="android.widget.Button" bounds="[42,830][678,930]" clickable="true" content-desc="3 Oylik\n53 000 uzs/oy\n159 000 soums"/>
  <node class="android.widget.Button" bounds="[42,1440][678,1520]" clickable="true" content-desc="Subscribe · 318 000 soums"/>
</hierarchy>"""

# The full-screen IELTS promo interstitial ("O'ychi o'yini o'ylaguncha
# boshqalar IELTS olib ketadi"): no X icon in any form — not labeled, not
# an unlabeled top-left clickable. Its two CTAs are real Buttons, so it
# clears the 2-option question floor; only the promo markers keep the
# runner from tapping "IELTSGA GOO!" as option A. The Android back button
# is the only safe way out (verified by hand: it lands on the screen the
# promo covered, e.g. a quiz Start page).
PROMO_INTERSTITIAL_XML = """<hierarchy>
  <node class="android.widget.FrameLayout" bounds="[0,0][720,1600]" clickable="false" content-desc=""/>
  <node class="android.view.View" bounds="[55,60][300,125]" clickable="true" content-desc="35 130+ subscribers"/>
  <node class="android.view.View" bounds="[100,650][620,960]" clickable="false" content-desc="O'ychi o'yini o'ylaguncha boshqalar IELTS olib ketadi"/>
  <node class="android.widget.Button" bounds="[42,1220][678,1305]" clickable="true" content-desc="IELTSGA GOO!"/>
  <node class="android.widget.Button" bounds="[42,1340][678,1400]" clickable="true" content-desc="VAQT TOPILAVERADI"/>
</hierarchy>"""

# The Assigned-courses list (live dump 2026-08-02 16:40): its top-left
# back arrow is an unlabeled icon-sized clickable exactly where a popup's
# X would sit. Blind-tapping it walked the runner backwards out of the
# whole course — only screens that LOOK like a known popup may have
# their unlabeled X tapped.
COURSES_LIST_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.widget.ImageView" bounds="[0,77][123,175]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[238,103][482,149]" clickable="false" content-desc="Assigned courses"/>
  <node class="android.view.View" bounds="[42,336][678,476]" clickable="true" content-desc="Ingliz tili B2\nRustam Qoriyev"/>
  <node class="android.view.View" bounds="[42,504][678,644]" clickable="true" content-desc="Nemis tili B1\nFeruza Uralova"/>
</hierarchy>"""

# The failed-test screen (live dump 2026-08-02 16:02): "Try again" is its
# ONLY button — retaking with the freshly learned answers is the way
# forward, unlike "Retry" on the pass-finish screen.
FAILED_QUIZ_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[42,714][678,840]" clickable="false" content-desc="Sorry‚ your score is a little low!"/>
  <node class="android.view.View" bounds="[63,861][657,903]" clickable="false" content-desc="You will definitely succeed in your next attempt"/>
  <node class="android.view.View" bounds="[533,973][634,1008]" clickable="false" content-desc="Accuracy"/>
  <node class="android.view.View" bounds="[554,1043][614,1085]" clickable="false" content-desc="30%"/>
  <node class="android.widget.Button" bounds="[42,1376][678,1460]" clickable="true" content-desc="Try again"/>
</hierarchy>"""

# A question screen's only unlabeled clickables are the full-screen
# scrim and the full-width top bar (the quit-X row) — never icon-sized.
QUESTION_BOUNDS_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.widget.Button" bounds="[0,77][720,175]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[42,189][678,245]" clickable="false" content-desc="Ular tez-tez suzishga borishadi. "/>
  <node class="android.widget.Button" bounds="[56,1177][136,1261]" clickable="true" content-desc="in"/>
  <node class="android.widget.Button" bounds="[154,1177][256,1261]" clickable="true" content-desc="They "/>
</hierarchy>"""

# The home screen's gear icon is an unlabeled top-left clickable, exactly
# where a popup's X would be — the bottom nav labels must veto the tap.
HOME_SCREEN_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[26,102][117,154]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[374,91][552,165]" clickable="true" content-desc="Streak"/>
  <node class="android.widget.ImageView" bounds="[14,1408][152,1516]" clickable="true" content-desc="Main"/>
  <node class="android.widget.ImageView" bounds="[152,1408][291,1516]" clickable="true" content-desc="Learn"/>
  <node class="android.widget.ImageView" bounds="[568,1408][706,1516]" clickable="true" content-desc="Profile"/>
</hierarchy>"""

FINISH_SCREEN_XML = """<hierarchy>
  <node class="android.view.View" bounds="[30,100][100,170]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[180,600][540,660]" clickable="false" content-desc="Test finished!"/>
  <node class="android.widget.Button" bounds="[42,1300][678,1380]" clickable="true" content-desc="Retry"/>
  <node class="android.widget.Button" bounds="[42,1418][678,1502]" clickable="true" content-desc="Next lesson"/>
</hierarchy>"""

# The pass-finish screen on the 2026-08-02 account ("Test completed" +
# stats): the stat labels render as Buttons, so they clear the 2-option
# question floor unless the finish marker vetoes it.
TEST_COMPLETED_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[42,714][678,840]" clickable="false" content-desc="Test completed"/>
  <node class="android.widget.Button" bounds="[91,973][181,1008]" clickable="true" content-desc="Lessons"/>
  <node class="android.widget.Button" bounds="[317,973][403,1008]" clickable="true" content-desc="Quizzes"/>
  <node class="android.widget.Button" bounds="[533,973][634,1008]" clickable="true" content-desc="Accuracy"/>
  <node class="android.widget.Button" bounds="[42,1418][678,1502]" clickable="true" content-desc="Next lesson"/>
</hierarchy>"""


class TapDriver:
    """Fake driver for dismiss_popup: static XML, records coordinate taps."""

    current_package = "uz.ibrat.farzandlari"

    def __init__(self, xml):
        self.xml = xml
        self.taps = []
        self.back_presses = 0

    @property
    def page_source(self):
        return self.xml

    def find_element(self, by, value):
        raise NoSuchElementException(value)

    def tap(self, positions, duration=None):
        self.taps.append(positions[0])

    def back(self):
        self.back_presses += 1


class TestUnlabeledClose(unittest.TestCase):
    def setUp(self):
        import navigation
        self._sleep = navigation.time.sleep
        navigation.time.sleep = lambda s: None

    def tearDown(self):
        import navigation
        navigation.time.sleep = self._sleep

    def test_finds_streak_popup_x_by_geometry(self):
        import navigation
        center = navigation.find_unlabeled_close_center(STREAK_POPUP_XML)
        self.assertEqual(center, (71, 128))

    def test_question_screen_has_no_icon_sized_x(self):
        import navigation
        self.assertIsNone(
            navigation.find_unlabeled_close_center(QUESTION_BOUNDS_XML)
        )

    def test_dismisses_streak_popup_via_x_not_continue(self):
        import navigation
        driver = TapDriver(STREAK_POPUP_XML)
        self.assertTrue(navigation.dismiss_popup(driver))
        self.assertEqual(driver.taps, [(71, 128)])

    def test_dismisses_pro_offer_via_x(self):
        import navigation
        driver = TapDriver(PRO_OFFER_XML)
        self.assertTrue(navigation.dismiss_popup(driver))
        self.assertEqual(driver.taps, [(75, 145)])

    def test_home_screen_gear_icon_is_never_blind_tapped(self):
        import navigation
        driver = TapDriver(HOME_SCREEN_XML)
        self.assertFalse(navigation.dismiss_popup(driver))
        self.assertEqual(driver.taps, [])

    def test_courses_list_back_arrow_is_never_blind_tapped(self):
        # Its back arrow is icon-sized, unlabeled, top-left — exactly a
        # popup X's geometry. Tapping it walked the runner out of the
        # course (2026-08-02); the screen shows no popup markers, so no
        # blind tap is allowed.
        import navigation
        driver = TapDriver(COURSES_LIST_XML)
        self.assertFalse(navigation.dismiss_popup(driver))
        self.assertEqual(driver.taps, [])
        self.assertEqual(driver.back_presses, 0)

    def test_finish_screen_back_arrow_is_never_blind_tapped(self):
        import navigation
        driver = TapDriver(FINISH_SCREEN_XML)
        self.assertFalse(navigation.dismiss_popup(driver))
        self.assertEqual(driver.taps, [])


class TestPromoInterstitial(unittest.TestCase):
    def setUp(self):
        import navigation
        self._sleep = navigation.time.sleep
        navigation.time.sleep = lambda s: None

    def tearDown(self):
        import navigation
        navigation.time.sleep = self._sleep

    def test_dismissed_via_android_back_button(self):
        # No X of any kind on this screen — back is the only way out.
        # Success is claimed only once the promo is really gone: a
        # swallowed back claimed as a dismissal would reset the idle
        # timer every cycle and lock the run out of its restart.
        import navigation

        class BackDriver(TapDriver):
            def back(self):
                super().back()
                self.xml = "<hierarchy/>"

        driver = BackDriver(PROMO_INTERSTITIAL_XML)
        self.assertTrue(navigation.dismiss_popup(driver))
        self.assertEqual(driver.back_presses, 1)
        self.assertEqual(driver.taps, [], "nothing to blind-tap on this screen")

        swallowed = TapDriver(PROMO_INTERSTITIAL_XML)
        self.assertFalse(navigation.dismiss_popup(swallowed))
        self.assertEqual(swallowed.back_presses, 1)

    def test_pro_offer_still_dismissed_via_its_x_not_back(self):
        import navigation
        driver = TapDriver(PRO_OFFER_XML)
        self.assertTrue(navigation.dismiss_popup(driver))
        self.assertEqual(driver.taps, [(75, 145)])
        self.assertEqual(driver.back_presses, 0)

    def test_ordinary_screens_are_never_backed_out_of(self):
        # Back on a non-promo screen would abandon the course flow — the
        # fallback must stay promo-only.
        import navigation
        for xml in (HOME_SCREEN_XML, FINISH_SCREEN_XML, LESSON_SCREEN_XML, QUIZ_START_XML):
            driver = TapDriver(xml)
            navigation.dismiss_popup(driver)
            self.assertEqual(driver.back_presses, 0, xml)

    def test_promo_ctas_are_never_tap_through_candidates(self):
        import navigation
        for xml in (PROMO_INTERSTITIAL_XML, PRO_OFFER_XML):
            self.assertEqual(
                navigation.candidate_buttons(qh.parse_screen(xml)), [], xml
            )

    def test_promo_interstitial_is_not_a_question(self):
        # Its two CTA Buttons clear the 2-option floor — without the promo
        # guard the runner would tap "IELTSGA GOO!" as option A, straight
        # into the paywall.
        driver = XmlDriver(PROMO_INTERSTITIAL_XML)
        state = watcher.fresh_state()
        results = []
        self.assertIsNone(watcher.poll_once(driver, state, results))
        self.assertIsNone(state["question"])
        self.assertEqual(results, [])

    def test_pro_offer_is_not_a_question(self):
        # Same trap: three subscription-plan Buttons look like options.
        driver = XmlDriver(PRO_OFFER_XML)
        state = watcher.fresh_state()
        self.assertIsNone(watcher.poll_once(driver, state, []))
        self.assertIsNone(state["question"])


# The "How did you hear about us?" survey interstitial, as reported from
# a client's phone (2026-08-04). The page has never been captured in a
# dump, so this tree is inferred — and the handler under test must
# survive label drift: no TV option, a different submit label, or a page
# that refuses to clear at all.
SURVEY_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[42,189][678,245]" clickable="false" content-desc="How did you hear about us?"/>
  <node class="android.widget.Button" bounds="[42,400][678,490]" clickable="true" content-desc="Instagram"/>
  <node class="android.widget.Button" bounds="[42,510][678,600]" clickable="true" content-desc="Telegram"/>
  <node class="android.widget.Button" bounds="[42,620][678,710]" clickable="true" content-desc="TV"/>
  <node class="android.widget.Button" bounds="[42,730][678,820]" clickable="true" content-desc="Friends"/>
  <node class="android.widget.Button" bounds="[42,1418][678,1502]" clickable="true" content-desc="Continue"/>
</hierarchy>"""

# The same page carrying its own "Next" — which classify_sheet would read
# as a feedback sheet and blind-tap, skipping the survey unanswered.
SURVEY_NEXT_XML = SURVEY_XML.replace(
    'content-desc="Continue"', 'content-desc="Next"'
)

# Label drift: no TV among the options, a Skip instead of a submit.
SURVEY_NO_TV_XML = SURVEY_XML.replace(
    'content-desc="TV"', 'content-desc="Radio"'
).replace('content-desc="Continue"', 'content-desc="Skip"')

# The same survey under a title no English marker can see (another
# phrasing or language): only the option shape — TV next to
# untranslatable social-brand names — identifies it.
SURVEY_UZBEK_XML = SURVEY_XML.replace(
    'content-desc="How did you hear about us?"',
    'content-desc="Biz haqimizda qayerdan eshitdingiz?"',
)

# TV rendered in Uzbek as well.
SURVEY_TELEVIZOR_XML = SURVEY_UZBEK_XML.replace(
    'content-desc="TV"', 'content-desc="Televizor"'
)


class SurveyDriver:
    """The survey page: desc taps recorded in order; the page clears (to
    a quiz Start page) only when one of `clears_on` is tapped."""

    def __init__(self, xml, clears_on=("Continue",)):
        self.xml = xml
        self.taps = []
        self.back_presses = 0
        self.clears_on = clears_on

    @property
    def page_source(self):
        return self.xml

    def find_element(self, by, value):
        m = re.search(r"content-desc=(?:'([^']*)'|\"([^\"]*)\")", value)
        desc = m and (m.group(1) if m.group(1) is not None else m.group(2))
        if not desc or f'content-desc="{desc}"' not in self.xml:
            raise NoSuchElementException(value)
        el = FakeElement(desc)

        def click():
            self.taps.append(desc)
            if desc in self.clears_on:
                self.xml = QUIZ_START_XML

        el.click = click
        return el

    def tap(self, positions, duration=None):
        self.taps.append(positions[0])

    def back(self):
        self.back_presses += 1


class TestSurveyPage(unittest.TestCase):
    def setUp(self):
        import navigation
        # poll_once on a misread survey would write results.json into cwd
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        self._sleep = navigation.time.sleep
        navigation.time.sleep = lambda s: None

    def tearDown(self):
        import navigation
        navigation.time.sleep = self._sleep
        os.chdir(self._cwd)

    def test_survey_titles_match_case_and_phrasing_variants(self):
        self.assertTrue(qh.looks_like_survey(["How did you hear about us?"]))
        self.assertTrue(qh.looks_like_survey(["Where did you hear about us"]))
        self.assertTrue(qh.looks_like_survey(["How did you find out about us?"]))
        self.assertFalse(qh.looks_like_survey(
            ["He's reading ___ interesting book.", "a", "an"]
        ))

    def test_unknown_title_is_caught_by_the_option_shape(self):
        # A title in another language (or any unseen phrasing) must not
        # blind the detector: TV next to an untranslated social brand is
        # this survey and nothing else.
        self.assertTrue(qh.looks_like_survey(
            ["Biz haqimizda qayerdan eshitdingiz?", "Instagram", "TV", "Friends"]
        ))
        self.assertTrue(qh.looks_like_survey(["Telegram", "Televizor"]))

    def test_tv_without_a_social_brand_is_not_the_survey(self):
        # A genuine media-vocabulary question can offer TV — without a
        # social brand beside it, it stays a question.
        self.assertFalse(qh.looks_like_survey(
            ["Television - ?", "TV", "Radio", "Newspaper"]
        ))
        # A brand inside longer text (a promo caption) is not an option.
        self.assertFalse(qh.looks_like_survey(
            ["Follow us on Instagram", "TV shows to watch", "Continue"]
        ))

    def test_drifted_title_survey_is_dismissed_via_tv(self):
        import navigation
        driver = SurveyDriver(SURVEY_UZBEK_XML, clears_on=("Continue",))
        self.assertTrue(navigation.dismiss_popup(driver))
        self.assertEqual(driver.taps, ["TV", "Continue"])

    def test_televizor_label_counts_as_tv(self):
        import navigation
        driver = SurveyDriver(SURVEY_TELEVIZOR_XML, clears_on=("Continue",))
        self.assertTrue(navigation.dismiss_popup(driver))
        self.assertEqual(driver.taps, ["Televizor", "Continue"])

    def test_survey_is_not_a_question(self):
        # Four option Buttons plus Continue read as word_translation —
        # the runner would tap "Instagram" as its answer and then wait
        # forever for a feedback sheet.
        driver = XmlDriver(SURVEY_XML)
        state = watcher.fresh_state()
        results = []
        self.assertIsNone(watcher.poll_once(driver, state, results))
        self.assertIsNone(state["question"])
        self.assertEqual(results, [])

    def test_survey_with_next_is_not_a_feedback_sheet(self):
        # Its "Next" would classify as an "other" sheet: a bogus results
        # entry, plus a blind Next tap that skips the survey unanswered.
        driver = XmlDriver(SURVEY_NEXT_XML)
        state = watcher.fresh_state()
        results = []
        self.assertIsNone(watcher.poll_once(driver, state, results))
        self.assertEqual(results, [])

    def test_survey_forward_is_owned_by_the_survey_handler_alone(self):
        # tap_forward_button re-tapping the survey's Continue on every
        # poll would reset the idle timer forever — the run would never
        # reach its restart-and-dump recovery.
        import navigation
        nodes = qh.parse_screen(SURVEY_XML)
        self.assertIsNone(navigation.find_forward_button(nodes))
        self.assertIsNone(navigation.forward_tap_label(nodes))

    def test_dismissed_by_choosing_tv_then_submitting(self):
        import navigation
        driver = SurveyDriver(SURVEY_XML, clears_on=("Continue",))
        self.assertTrue(navigation.dismiss_popup(driver))
        self.assertEqual(driver.taps, ["TV", "Continue"])

    def test_tv_choice_that_advances_by_itself_needs_no_submit(self):
        import navigation
        driver = SurveyDriver(SURVEY_XML, clears_on=("TV",))
        self.assertTrue(navigation.dismiss_popup(driver))
        self.assertEqual(driver.taps, ["TV"], "no stray tap on the next screen")

    def test_survey_without_tv_is_still_skipped(self):
        import navigation
        driver = SurveyDriver(SURVEY_NO_TV_XML, clears_on=("Skip",))
        self.assertTrue(navigation.dismiss_popup(driver))
        self.assertEqual(driver.taps, ["Skip"], "no blind option guessing")

    def test_survey_that_refuses_to_clear_leaves_the_restart_armed(self):
        # False keeps the idle timer running, so the usual restart plus
        # stuck-screen dump captures the tree this handler guessed
        # wrong about.
        import navigation
        driver = SurveyDriver(SURVEY_XML, clears_on=())
        self.assertFalse(navigation.dismiss_popup(driver))
        self.assertEqual(driver.taps, ["TV", "Continue"])
        self.assertEqual(driver.back_presses, 0)


# The ask-to-update bottom sheet (client report, 2026-08-04 — never
# captured in a dump), risen over a word-translation screen whose own
# Continue is still in the tree: forward taps must be suppressed while
# the sheet is up, and Update itself must never be tapped — it walks out
# to the Play Store.
UPDATE_SHEET_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[42,189][678,245]" clickable="false" content-desc="Clever -"/>
  <node class="android.widget.Button" bounds="[56,700][336,784]" clickable="true" content-desc="Aqlli "/>
  <node class="android.widget.Button" bounds="[384,700][664,784]" clickable="true" content-desc="Mehribon"/>
  <node class="android.widget.Button" bounds="[42,820][678,900]" clickable="true" content-desc="Continue"/>
  <node class="android.view.View" bounds="[0,1000][720,1600]" clickable="true" content-desc="A new version of the app is available"/>
  <node class="android.widget.Button" bounds="[42,1418][678,1502]" clickable="true" content-desc="Update"/>
  <node class="android.widget.Button" bounds="[42,1520][678,1580]" clickable="true" content-desc="Later"/>
</hierarchy>"""

# The same sheet offering nothing but Update: the Android back button —
# how any bottom sheet closes — is the only way out.
UPDATE_SHEET_NO_DECLINE_XML = UPDATE_SHEET_XML.replace(
    '  <node class="android.widget.Button" bounds="[42,1520][678,1580]"'
    ' clickable="true" content-desc="Later"/>\n', ''
)


class UpdateSheetDriver(SurveyDriver):
    """SurveyDriver that can also clear its page on a back press."""

    def __init__(self, xml, clears_on=(), back_clears=False):
        super().__init__(xml, clears_on)
        self.back_clears = back_clears

    def back(self):
        self.back_presses += 1
        if self.back_clears:
            self.xml = QUIZ_START_XML


class TestUpdateSheet(unittest.TestCase):
    def setUp(self):
        import navigation
        # poll_once on a misread sheet would write results.json into cwd
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        self._sleep = navigation.time.sleep
        navigation.time.sleep = lambda s: None

    def tearDown(self):
        import navigation
        navigation.time.sleep = self._sleep
        os.chdir(self._cwd)

    def test_update_sheet_is_recognized_by_its_button_alone(self):
        self.assertTrue(qh.looks_like_update_sheet(["Update", "Later"]))
        self.assertTrue(qh.looks_like_update_sheet(["UPDATE "]))
        # the word inside a sentence (a lesson text) is not the sheet
        self.assertFalse(qh.looks_like_update_sheet(
            ["Please update your homework", "Next"]
        ))

    def test_update_sheet_is_not_a_question(self):
        # Its Update/Later Buttons join the covered screen's options —
        # option A could be Update itself, walking out to the Play Store.
        driver = XmlDriver(UPDATE_SHEET_XML)
        state = watcher.fresh_state()
        results = []
        self.assertIsNone(watcher.poll_once(driver, state, results))
        self.assertIsNone(state["question"])
        self.assertEqual(results, [])

    def test_update_button_is_never_a_tap_through_candidate(self):
        import navigation
        buttons = navigation.candidate_buttons(qh.parse_screen(UPDATE_SHEET_XML))
        self.assertNotIn("Update", buttons)

    def test_forward_taps_are_suppressed_under_the_sheet(self):
        # The covered screen's Continue is still in the tree; re-tapping
        # it through the sheet resets the idle timer forever.
        import navigation
        nodes = qh.parse_screen(UPDATE_SHEET_XML)
        self.assertIsNone(navigation.find_forward_button(nodes))
        self.assertIsNone(navigation.forward_tap_label(nodes))

    def test_declined_via_later_without_touching_update(self):
        import navigation
        driver = UpdateSheetDriver(UPDATE_SHEET_XML, clears_on=("Later",))
        self.assertTrue(navigation.dismiss_popup(driver))
        self.assertEqual(driver.taps, ["Later"])
        self.assertEqual(driver.back_presses, 0)

    def test_backed_out_when_it_offers_nothing_but_update(self):
        import navigation
        driver = UpdateSheetDriver(UPDATE_SHEET_NO_DECLINE_XML, back_clears=True)
        self.assertTrue(navigation.dismiss_popup(driver))
        self.assertEqual(driver.taps, [], "Update must never be tapped")
        self.assertEqual(driver.back_presses, 1)

    def test_sheet_that_refuses_to_close_leaves_the_restart_armed(self):
        import navigation
        driver = UpdateSheetDriver(UPDATE_SHEET_NO_DECLINE_XML, back_clears=False)
        self.assertFalse(navigation.dismiss_popup(driver))
        self.assertEqual(driver.taps, [])


class TestTapNext(unittest.TestCase):
    def test_tap_next_retries_after_stale_click(self):
        el = FakeElement("Next", click_fails=1)
        driver = FakeDriver([el])
        self.assertTrue(qh.tap_next(driver))
        self.assertTrue(el.clicked)


class TestDetectQuestionType(unittest.TestCase):
    def test_underscore_blank_is_multiple_choice(self):
        self.assertEqual(
            qh.detect_question_type(
                "He's reading ___ interesting book.", ["a", "an", "the"]
            ),
            "multiple_choice",
        )

    def test_pipe_blank_is_multiple_choice(self):
        self.assertEqual(
            qh.detect_question_type(
                "We're going to |_| restaurant for dinner tonight.",
                ["an", "a", "the"],
            ),
            "multiple_choice",
        )

    def test_moslashtiring_is_matching(self):
        self.assertEqual(
            qh.detect_question_type(
                "Moslashtiring.",
                ["The", "letter he wrote", "An", "apple", "A", "cat"],
            ),
            "matching",
        )

    def test_sozlarni_moslashtiring_is_matching(self):
        self.assertEqual(
            qh.detect_question_type(
                "So‘zlarni moslashtiring.",
                ["An", "plans", "A", "poems I wrote", "The", "fox", "-", "airplane"],
            ),
            "matching",
        )

    def test_blank_prompt_with_many_chips_is_fill_the_blank(self):
        # The sentence-building questions carry a "___" blank AND a chip
        # tray; the chip count must win, or the runner taps one chip as an
        # "option" and waits forever for a feedback sheet (2026-08-02).
        self.assertEqual(
            qh.detect_question_type(
                "The phone rang, but I didn't hear it. ___.",
                ["must", "I", "slept", "asleep", "sleeping", "been", "can", "have "],
            ),
            "fill_the_blank",
        )

    def test_many_chips_is_fill_the_blank(self):
        self.assertEqual(
            qh.detect_question_type(
                "U (qiz) sen tavsiya qilgan kitobni o‘qiyapti.",
                ["She", "is", "reading", "the", "book", "you", "recommended."],
            ),
            "fill_the_blank",
        )

    def test_few_options_without_blank_falls_back_to_multiple_choice(self):
        self.assertEqual(
            qh.detect_question_type("Choose the correct sentence", ["A cat", "An cat"]),
            "multiple_choice",
        )

    def test_few_chips_with_continue_is_word_translation(self):
        # The vocabulary screens ("Clever -" + 4 chips, 2026-08-02) sit
        # under the fill_the_blank chip floor; only the Continue button on
        # screen tells them apart from tap-to-submit multiple choice.
        self.assertEqual(
            qh.detect_question_type(
                "Clever -",
                ["Nohaq ", "Qizg’anchiq", "Aqlli ", "Mehribon"],
                descs=["Clever -", "Nohaq ", "Qizg’anchiq", "Aqlli ",
                       "Mehribon", "Continue"],
            ),
            "word_translation",
        )

    def test_few_options_without_continue_stays_multiple_choice(self):
        self.assertEqual(
            qh.detect_question_type(
                "Under -",
                ["ustida", "ostida", "ichida", "yonida"],
                descs=["Under -", "ustida", "ostida", "ichida", "yonida"],
            ),
            "multiple_choice",
        )

    def test_chip_count_beats_continue_button(self):
        # Sentence builders carry a Continue button too — the chip count
        # must keep them fill_the_blank, or a known multi-word answer
        # would be tapped as a single "option".
        self.assertEqual(
            qh.detect_question_type(
                "Men darsga kechikdim.",
                ["for", "I", "was", "am", "lesson.", "late", "the "],
                descs=["Men darsga kechikdim.", "for", "I", "was", "am",
                       "lesson.", "late", "the ", "Continue"],
            ),
            "fill_the_blank",
        )

    def test_moslashtiring_beats_continue_button(self):
        self.assertEqual(
            qh.detect_question_type(
                "Moslashtiring.",
                ["The", "letter he wrote", "An", "apple"],
                descs=["Moslashtiring.", "The", "letter he wrote", "An",
                       "apple", "Continue"],
            ),
            "matching",
        )


class TestStrategies(unittest.TestCase):
    def test_build_answer_map_uses_any_entry_with_captured_answer(self):
        results = [
            {"question": "Q1", "result": "correct", "correct_answer": ["an"]},
            {"question": "Q2", "result": "incorrect", "correct_answer": ["the"]},
            {"question": "Q3", "result": "incorrect"},  # nothing revealed
            {"question": "Q4", "result": "correct"},  # old format, no answer
        ]
        self.assertEqual(
            qh.build_answer_map(results), {"Q1": "an", "Q2": "the"}
        )

    def test_mc_known_answer_wins(self):
        choice = qh.choose_mc_option(
            "Q1", ["a", "an", "the"], {"Q1": "The"}, {}
        )
        self.assertEqual(choice, "the")

    def test_mc_unknown_starts_with_option_a(self):
        self.assertEqual(
            qh.choose_mc_option("Q1", ["a", "an", "the"], {}, {}), "a"
        )

    def test_mc_repeat_tries_next_untried_option(self):
        attempted = {}
        first = qh.choose_mc_option("Q1", ["a", "an", "the"], {}, attempted)
        second = qh.choose_mc_option("Q1", ["a", "an", "the"], {}, attempted)
        self.assertEqual((first, second), ("a", "an"))

    def test_chip_sequence_uses_known_sentence_order(self):
        known = {"Q1": "She is reading the book you recommended."}
        chips = ["you", "She", "recommended.", "is", "the", "reading", "book"]
        self.assertEqual(
            qh.chip_sequence("Q1", chips, known),
            ["She", "is", "reading", "the", "book", "you", "recommended."],
        )

    def test_build_answer_map_picks_longest_when_diff_has_stray_chip(self):
        results = [{
            "question": "Ular tez-tez suzishga borishadi. ",
            "type": "fill_the_blank",
            "result": "incorrect",
            "correct_answer": ["swimming.", "They often go swimming."],
        }]
        self.assertEqual(
            qh.build_answer_map(results),
            {"Ular tez-tez suzishga borishadi. ": "They often go swimming."},
        )

    def test_mc_answer_is_the_option_not_the_filled_sentence(self):
        entry = {
            "question": "I need to buy |_| new laptop.",
            "type": "multiple_choice",
            "result": "incorrect",
            "options": ["the", "a", "an"],
            # sheet echoed the sentence filled with the WRONG word
            "correct_answer": ["I need to buy \nthe\n new laptop.", "a"],
        }
        self.assertEqual(qh.pick_revealed_answer(entry), "a")

    def test_mc_answer_duplicated_in_diff_wins_by_frequency(self):
        entry = {
            "question": "He's reading ___ book you recommended.",
            "type": "multiple_choice",
            "options": ["for", "the", "an", "a"],
            "correct_answer": ["the", "the", "an", "a"],
        }
        self.assertEqual(qh.pick_revealed_answer(entry), "the")

    def test_mc_answer_untrusted_when_nothing_matches_options(self):
        entry = {
            "question": "Q1",
            "type": "multiple_choice",
            "options": ["a", "an"],
            "correct_answer": ["Some unrelated sheet text"],
        }
        self.assertIsNone(qh.pick_revealed_answer(entry))

    def test_word_translation_answer_matches_option_despite_chip_padding(self):
        # The sheet reveals the bare word ("Aqlli") while the chip label
        # carries a trailing space ("Aqlli ") — the match must strip.
        entry = {
            "question": "Clever -",
            "type": "word_translation",
            "options": ["Nohaq ", "Qizg’anchiq", "Aqlli ", "Mehribon"],
            "correct_answer": ["Aqlli"],
        }
        self.assertEqual(qh.pick_revealed_answer(entry), "Aqlli")

    def test_word_translation_answer_untrusted_when_nothing_matches(self):
        # A noisy diff (e.g. the filled-in bubble text) must not be
        # mistaken for the answer — longest-element would grab it.
        entry = {
            "question": "Clever -",
            "type": "word_translation",
            "options": ["Nohaq ", "Aqlli "],
            "correct_answer": ["Clever - Aqlli"],
        }
        self.assertIsNone(qh.pick_revealed_answer(entry))

    def test_word_translation_ambiguous_echo_teaches_nothing(self):
        # The live 2026-08-02 15:05 entry: the diff caught the tapped chip
        # ("Nohaq " moved into the answer area) NEXT TO the revealed word.
        # Both match options and nothing says which is which — learning
        # the first would repeat the wrong guess forever, so learn nothing.
        entry = {
            "question": "Clever -",
            "type": "word_translation",
            "options": ["Nohaq ", "Qizg’anchiq", "Aqlli ", "Mehribon"],
            "correct_answer": ["Nohaq ", "Aqlli"],
        }
        self.assertIsNone(qh.pick_revealed_answer(entry))

    def test_word_translation_echo_of_the_right_chip_still_learned(self):
        # When the tapped chip WAS the answer, the echo and the reveal
        # agree — one distinct option, safe to learn.
        entry = {
            "question": "Clever -",
            "type": "word_translation",
            "options": ["Nohaq ", "Qizg’anchiq", "Aqlli ", "Mehribon"],
            "correct_answer": ["Aqlli ", "Aqlli"],
        }
        self.assertEqual(qh.pick_revealed_answer(entry).strip(), "Aqlli")

    def test_chip_sequence_matches_chips_with_trailing_spaces(self):
        known = {"Q1": "They often go swimming."}
        chips = ["swimming.", "in", "often ", "They ", "go "]
        self.assertEqual(
            qh.chip_sequence("Q1", chips, known),
            ["They ", "often ", "go ", "swimming."],
        )

    def test_chip_sequence_handles_multiword_chips(self):
        # The live n=149 entry: the chip row carries "the station." as ONE
        # chip, so word-by-word matching dead-ends and the runner used to
        # fall back to a single-chip guess every encounter.
        known = {"Q1": "This is not the way to the station."}
        chips = ["not", "is", "the station.", "way", "are", "the",
                 "those", "This", "for", "to"]
        self.assertEqual(
            qh.chip_sequence("Q1", chips, known),
            ["This", "is", "not", "the", "way", "to", "the station."],
        )

    def test_fill_blank_reveal_untrusted_when_not_buildable_from_chips(self):
        # The live n=227 entry: the reveal diff caught the NEXT lesson's
        # video player, not the sheet. Learning that garbage repeats a
        # wrong single-chip answer forever; teaching nothing lets the
        # dedupe upgrade rule replace the entry from a later good reveal.
        entry = {
            "question": "Kitobda mundarija yo‘q.",
            "type": "fill_the_blank",
            "options": ["The", "has", "book", "no", "table of contents.", "a"],
            "correct_answer": ["Hide player controls", "Unmute",
                               "Playback Settings"],
        }
        self.assertIsNone(qh.pick_revealed_answer(entry))

    def test_fill_blank_reveal_prefers_buildable_sentence_over_longer_noise(self):
        # The live n=108 entry: the diff holds a lesson paragraph (longest)
        # AND the real revealed sentence — the sentence the chips can build
        # must win over raw length.
        entry = {
            "question": "U (qiz) ishidan keyin maktabga boradi.",
            "type": "fill_the_blank",
            "options": ["goes ", "work.", "school ", "to ", "go",
                        "after ", "She "],
            "correct_answer": [
                "Demonstrative pronouns are used to point to specific"
                " people, objects, or places. They help distinguish and"
                " indicate the proximity of objects.",
                "She goes to school after work.",
            ],
        }
        self.assertEqual(
            qh.pick_revealed_answer(entry), "She goes to school after work."
        )

    def test_chip_sequence_unknown_taps_first_chip_only(self):
        chips = ["She", "is", "reading"]
        self.assertEqual(qh.chip_sequence("Q1", chips, {}), ["She"])

    def test_chip_sequence_ignores_known_answer_with_missing_chip(self):
        known = {"Q1": "She is sleeping."}
        chips = ["She", "is", "reading"]
        self.assertEqual(qh.chip_sequence("Q1", chips, known), ["She"])

    def test_parse_cards_extracts_labels_centers_and_clickability(self):
        cards = qh.parse_cards(MATCHING_MIDGAME_XML)
        self.assertEqual(len(cards), 8, "the desc-less quit-X Button is not a card")
        apple = next(c for c in cards if c["label"] == "Apple")
        self.assertEqual((apple["x"], apple["y"]), (194, 1029))
        self.assertTrue(apple["clickable"])
        story = next(c for c in cards if c["label"] == "Story")
        self.assertFalse(story["clickable"])

    def test_split_matching_columns_by_geometry_skips_locked_pairs(self):
        lefts, rights = qh.split_matching_columns(qh.parse_cards(MATCHING_MIDGAME_XML))
        self.assertEqual([c["label"] for c in lefts], ["Apple", "People"])
        self.assertEqual([c["label"] for c in rights], ["Singular", "Plural"])

    def test_card_signature_sees_a_lock_that_labels_alone_miss(self):
        # After Jeans+Plural locked, the label sequence reads exactly the
        # same — only the clickable flip reveals the match (this is how
        # round 24 of the 2026-08-02 run recorded a real match as
        # "not a pair").
        before = MATCHING_MIDGAME_XML.replace(
            'bounds="[44,600][344,865]" clickable="false" content-desc="Jeans"',
            'bounds="[44,600][344,865]" clickable="true" content-desc="Jeans"',
        ).replace(
            'bounds="[376,600][676,865]" clickable="false" content-desc="Plural"',
            'bounds="[376,600][676,865]" clickable="true" content-desc="Plural"',
        )
        self.assertNotEqual(
            qh.card_signature(qh.parse_cards(before)),
            qh.card_signature(qh.parse_cards(MATCHING_MIDGAME_XML)),
        )
        self.assertEqual(
            [c["label"] for c in qh.parse_cards(before)],
            [c["label"] for c in qh.parse_cards(MATCHING_MIDGAME_XML)],
            "the label list alone is blind to this lock",
        )

    def test_build_pair_map_collects_pairs_from_matching_entries(self):
        results = [
            {
                "question": "Moslashtiring.",
                "type": "matching",
                "result": "correct",
                "correct_answer": [["Drive", "along the road"], ["Fly", "to Tashkent"]],
            },
            {"question": "Q1", "type": "multiple_choice", "correct_answer": ["an"]},
            {"question": "Moslashtiring.", "type": "matching"},  # no pairs saved
        ]
        self.assertEqual(
            qh.build_pair_map(results),
            {"Drive": "along the road", "Fly": "to Tashkent"},
        )

    def test_build_answer_map_skips_matching_entries(self):
        results = [{
            "question": "Moslashtiring.",
            "type": "matching",
            "correct_answer": [["Drive", "along the road"]],
        }]
        self.assertEqual(qh.build_answer_map(results), {})

    def test_pair_attempt_order_tries_known_partner_instances_first(self):
        rights = [
            {"label": "Plural", "x": 526, "y": 435, "clickable": True},
            {"label": "Singular", "x": 526, "y": 732, "clickable": True},
            {"label": "Plural", "x": 526, "y": 1029, "clickable": True},
        ]
        ordered = qh.pair_attempt_order("Jeans", rights, {"Jeans": "Plural"})
        self.assertEqual(
            [c["y"] for c in ordered], [435, 1029, 732],
            "every instance of the known label first, each group top-down",
        )
        # unknown left card: top-to-bottom order untouched
        self.assertEqual(qh.pair_attempt_order("Go", rights, {}), rights)

    def test_xpath_literal_handles_apostrophes(self):
        self.assertEqual(qh.xpath_literal("the"), "'the'")
        self.assertEqual(qh.xpath_literal("He's"), '"He\'s"')
        self.assertTrue(qh.xpath_literal("He's \"x\"").startswith("concat("))


# Trimmed from the real stuck_screen.xml captured 2026-08-02 15:17: the
# category-matching board mid-game. "Singular" and "Plural" each appear
# twice; the first two rows are already-locked pairs (clickable=false)
# that keep their labels. A text tap on "Singular" always lands on the
# locked row-1 card and is swallowed — the runner burned every attempt
# that way.
MATCHING_MIDGAME_XML = """<hierarchy>
  <node class="android.widget.Button" bounds="[0,77][720,175]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[42,189][678,245]" clickable="false" content-desc="So‘zlarni moslashtiring."/>
  <node class="android.widget.Button" bounds="[44,303][344,568]" clickable="false" content-desc="Story"/>
  <node class="android.widget.Button" bounds="[376,303][676,568]" clickable="false" content-desc="Singular"/>
  <node class="android.widget.Button" bounds="[44,600][344,865]" clickable="false" content-desc="Jeans"/>
  <node class="android.widget.Button" bounds="[376,600][676,865]" clickable="false" content-desc="Plural"/>
  <node class="android.widget.Button" bounds="[44,896][344,1162]" clickable="true" content-desc="Apple"/>
  <node class="android.widget.Button" bounds="[376,896][676,1162]" clickable="true" content-desc="Singular"/>
  <node class="android.widget.Button" bounds="[44,1193][344,1458]" clickable="true" content-desc="People"/>
  <node class="android.widget.Button" bounds="[376,1193][676,1458]" clickable="true" content-desc="Plural"/>
</hierarchy>"""


class GeoMatchingDriver:
    """The category board as the app really behaves (2026-08-02): right
    labels repeat, each left card matches exactly ONE right card
    instance, locked pairs stay on screen unclickable with unchanged
    labels, and a tap on a locked card is swallowed. Coordinate taps are
    resolved to whichever card sits at that position NOW."""

    # displayed rows: left | right
    ROWS = [("Story", "Plural"), ("Jeans", "Singular"),
            ("Apple", "Singular"), ("People", "Plural")]
    # left card of row i pairs with the right card of row PARTNER[i]
    PARTNER = {0: 1, 1: 0, 2: 2, 3: 3}

    def __init__(self):
        self.active_lefts = [0, 1, 2, 3]
        self.active_rights = [0, 1, 2, 3]
        self.locked = []      # (left_row, right_row) in lock order
        self.selected = None  # ("L"|"R", card row id)
        self.taps = []

    def _board(self):
        """Visible rows top to bottom: locked pairs first, then actives."""
        rows = list(self.locked) + list(zip(self.active_lefts, self.active_rights))
        board = []
        for pos, (lrow, rrow) in enumerate(rows):
            y1 = 303 + pos * 297
            board.append((pos, lrow, rrow, y1, y1 + 265))
        return board

    @property
    def page_source(self):
        if not self.active_lefts:
            return ('<hierarchy>'
                    '<node class="android.view.View" content-desc="So‘zlarni moslashtiring."/>'
                    '<node class="android.view.View" content-desc="Nicely done!"/>'
                    '<node class="android.widget.Button" bounds="[42,1418][678,1502]"'
                    ' clickable="true" content-desc="Next"/></hierarchy>')
        nodes = ['<node class="android.view.View" bounds="[42,189][678,245]"'
                 ' clickable="false" content-desc="So‘zlarni moslashtiring."/>']
        for pos, lrow, rrow, y1, y2 in self._board():
            locked = pos < len(self.locked)
            click = "false" if locked else "true"
            nodes.append(f'<node class="android.widget.Button" bounds="[44,{y1}][344,{y2}]"'
                         f' clickable="{click}" content-desc="{self.ROWS[lrow][0]}"/>')
            nodes.append(f'<node class="android.widget.Button" bounds="[376,{y1}][676,{y2}]"'
                         f' clickable="{click}" content-desc="{self.ROWS[rrow][1]}"/>')
        return "<hierarchy>" + "".join(nodes) + "</hierarchy>"

    def tap(self, positions, duration=None):
        self.taps.append(positions[0])
        x, y = positions[0]
        side = "L" if x < 360 else "R"
        hit = next((row for row in self._board() if row[3] <= y <= row[4]), None)
        if hit is None:
            return
        pos, lrow, rrow, _, _ = hit
        if pos < len(self.locked):
            return  # locked cards swallow taps — the real trap
        card = lrow if side == "L" else rrow
        if self.selected is None or self.selected[0] == side:
            self.selected = (side, card)
            return
        prev_side, prev_card = self.selected
        self.selected = None
        left = prev_card if prev_side == "L" else card
        right = card if prev_side == "L" else prev_card
        if self.PARTNER[left] == right:
            self.active_lefts.remove(left)
            self.active_rights.remove(right)
            self.locked.append((left, right))
        # wrong pair: the board silently resets


class FlashingMatchingDriver(GeoMatchingDriver):
    """GeoMatchingDriver plus the wrong-pair flash animation: after a
    missed attempt, the next N dumps show the two tapped cards
    clickable="false" (input frozen mid-flash) before the board resets.
    This is the transient that made the runner record phantom matches —
    results.json n=986 (2026-08-03 01:00) holds 16 fake pairs, all on
    the same left card, from one 4-pair board."""

    def __init__(self, flash_reads=1):
        super().__init__()
        self.flash_reads_per_miss = flash_reads
        self._flash_labels = ()
        self._flash_reads = 0

    def tap(self, positions, duration=None):
        pre_selected, pre_locks = self.selected, len(self.locked)
        hit = next((row for row in self._board()
                    if row[3] <= positions[0][1] <= row[4]), None)
        super().tap(positions, duration)
        completed_wrong = (
            pre_selected is not None and self.selected is None
            and len(self.locked) == pre_locks
        )
        if completed_wrong and hit is not None:
            side, row = pre_selected
            first = self.ROWS[row][0] if side == "L" else self.ROWS[row][1]
            _, lrow, rrow, _, _ = hit
            second = (self.ROWS[lrow][0] if positions[0][0] < 360
                      else self.ROWS[rrow][1])
            self._flash_labels = (first, second)
            self._flash_reads = self.flash_reads_per_miss

    @property
    def page_source(self):
        xml = GeoMatchingDriver.page_source.fget(self)
        if self._flash_reads > 0:
            self._flash_reads -= 1
            for label in self._flash_labels:
                xml = xml.replace(
                    f'clickable="true" content-desc="{label}"',
                    f'clickable="false" content-desc="{label}"', 1)
        return xml


class TestJudgePairAttempt(unittest.TestCase):
    BEFORE = [
        {"label": "Story", "x": 194, "y": 435, "clickable": True},
        {"label": "Jeans", "x": 194, "y": 732, "clickable": True},
        {"label": "Plural", "x": 526, "y": 435, "clickable": True},
        {"label": "Singular", "x": 526, "y": 732, "clickable": True},
    ]

    @staticmethod
    def _without(cards, *labels):
        remaining = list(labels)
        out = []
        for c in cards:
            if c["label"] in remaining:
                remaining.remove(c["label"])
                out.append({**c, "clickable": False})
            else:
                out.append(c)
        return out

    def test_unchanged_board_is_a_reset(self):
        self.assertEqual(
            qh.judge_pair_attempt(self.BEFORE, self.BEFORE, "Story", "Plural"),
            "reset",
        )

    def test_rearranged_reset_is_still_a_reset(self):
        # A wrong pair never rearranges cards, but the judge must compare
        # active labels, not positions — locking DOES rearrange, so a
        # position-sensitive reset check would misread settled boards.
        shuffled = [dict(c, y=c["y"] + 297) for c in reversed(self.BEFORE)]
        self.assertEqual(
            qh.judge_pair_attempt(self.BEFORE, shuffled, "Story", "Plural"),
            "reset",
        )

    def test_exactly_the_tapped_two_leaving_play_is_a_match(self):
        after = self._without(self.BEFORE, "Story", "Plural")
        self.assertEqual(
            qh.judge_pair_attempt(self.BEFORE, after, "Story", "Plural"),
            "matched",
        )

    def test_flash_frame_freezing_all_cards_is_unsettled(self):
        after = [{**c, "clickable": False} for c in self.BEFORE]
        self.assertEqual(
            qh.judge_pair_attempt(self.BEFORE, after, "Story", "Plural"),
            "unsettled",
        )

    def test_two_other_cards_leaving_play_is_unsettled(self):
        # Some pair locked, but not the tapped one (a late-rendering lock
        # from a previous attempt) — nothing to learn about THIS attempt.
        after = self._without(self.BEFORE, "Jeans", "Singular")
        self.assertEqual(
            qh.judge_pair_attempt(self.BEFORE, after, "Story", "Plural"),
            "unsettled",
        )

    def test_duplicate_labels_lose_one_instance_each(self):
        before = self.BEFORE + [
            {"label": "Story", "x": 194, "y": 1029, "clickable": True},
            {"label": "Plural", "x": 526, "y": 1029, "clickable": True},
        ]
        after = self._without(before, "Story", "Plural")
        self.assertEqual(
            qh.judge_pair_attempt(before, after, "Story", "Plural"),
            "matched",
        )


class TestPairAttemptOrderFailed(unittest.TestCase):
    RIGHTS = [
        {"label": "Plural", "x": 526, "y": 435, "clickable": True},
        {"label": "Singular", "x": 526, "y": 732, "clickable": True},
        {"label": "Plural", "x": 526, "y": 1029, "clickable": True},
    ]

    def test_failed_pairs_sink_to_the_back(self):
        ordered = qh.pair_attempt_order(
            "Jeans", self.RIGHTS, {}, failed={("Jeans", "Plural")}
        )
        self.assertEqual(
            [c["y"] for c in ordered], [732, 435, 1029],
            "fresh combinations first, failed ones last but never dropped",
        )

    def test_failed_known_partner_no_longer_goes_first(self):
        # The answer book can be wrong (a phantom pair learned by the
        # 2026-08-03 bug) — once its pair fails on this board, fresh
        # cards must come first or the board loops on the bad "known".
        ordered = qh.pair_attempt_order(
            "Jeans", self.RIGHTS, {"Jeans": "Plural"},
            failed={("Jeans", "Plural")},
        )
        self.assertEqual([c["y"] for c in ordered], [732, 435, 1029])

    def test_other_lefts_failures_do_not_reorder(self):
        ordered = qh.pair_attempt_order(
            "Jeans", self.RIGHTS, {}, failed={("Story", "Plural")}
        )
        self.assertEqual([c["y"] for c in ordered], [435, 732, 1029])


class TestTapPair(unittest.TestCase):
    def test_both_taps_ride_in_one_actions_request(self):
        import main as main_mod

        class W3CDriver:
            def __init__(self):
                self.calls = []
                self.taps = []

            def execute(self, command, params=None):
                self.calls.append((command, params))
                return {"value": None}

            def tap(self, positions, duration=None):
                self.taps.append(positions)

        driver = W3CDriver()
        main_mod.tap_pair(driver, (194, 435), (526, 732))
        self.assertEqual(len(driver.calls), 1, "one request for both taps")
        self.assertEqual(driver.taps, [], "no per-tap fallback calls")

    def test_falls_back_to_two_plain_taps(self):
        import main as main_mod
        self._time = main_mod.time
        main_mod.time = FakeTime()

        class PlainDriver:  # no execute() — like the test fakes
            def __init__(self):
                self.taps = []

            def tap(self, positions, duration=None):
                self.taps.append(positions[0])

        driver = PlainDriver()
        try:
            main_mod.tap_pair(driver, (194, 435), (526, 732))
        finally:
            main_mod.time = self._time
        self.assertEqual(driver.taps, [(194, 435), (526, 732)])


class TestAnswerMatchingFlow(unittest.TestCase):
    def setUp(self):
        import main as main_mod
        self._time = main_mod.time
        main_mod.time = FakeTime()

    def tearDown(self):
        import main as main_mod
        main_mod.time = self._time

    def test_completes_duplicate_label_board_by_position(self):
        import main as main_mod
        driver = GeoMatchingDriver()
        state = {}
        self.assertTrue(main_mod.answer_matching(driver, state, {}))
        self.assertEqual(driver.active_lefts, [], "every pair locked")
        self.assertEqual(driver.locked, [(0, 1), (1, 0), (2, 2), (3, 3)])
        self.assertEqual(state["pending_pairs"], [
            ["Story", "Singular"], ["Jeans", "Plural"],
            ["Apple", "Singular"], ["People", "Plural"],
        ])

    def test_known_pairs_need_no_wrong_attempts(self):
        import main as main_mod
        known = {"Story": "Singular", "Jeans": "Plural",
                 "Apple": "Singular", "People": "Plural"}
        driver = GeoMatchingDriver()
        self.assertTrue(main_mod.answer_matching(driver, {}, known))
        self.assertEqual(driver.active_lefts, [])
        self.assertEqual(len(driver.taps), 8, "two taps per pair, no misses")

    def test_wrong_pair_flash_is_not_a_match(self):
        # The 2026-08-03 01:00 field failure: a dump mid wrong-pair flash
        # differs from the pre-attempt board, the old any-change check
        # declared "matched", and the round reset to the same left card —
        # re-trying the same wrong rights until the attempt budget died
        # (16 phantom pairs on one board in results.json n=986).
        import main as main_mod
        driver = FlashingMatchingDriver(flash_reads=1)
        state = {}
        self.assertTrue(main_mod.answer_matching(driver, state, {}))
        self.assertEqual(driver.active_lefts, [], "board completed")
        self.assertEqual(state["pending_pairs"], [
            ["Story", "Singular"], ["Jeans", "Plural"],
            ["Apple", "Singular"], ["People", "Plural"],
        ], "only real locks recorded — no phantom pairs")

    def test_longer_flash_is_still_not_a_match(self):
        # Two consecutive dumps can both land inside one flash — a match
        # verdict must outlast any flash, not just a single frame.
        import main as main_mod
        driver = FlashingMatchingDriver(flash_reads=2)
        state = {}
        self.assertTrue(main_mod.answer_matching(driver, state, {}))
        self.assertEqual(driver.active_lefts, [], "board completed")
        self.assertEqual(state["pending_pairs"], [
            ["Story", "Singular"], ["Jeans", "Plural"],
            ["Apple", "Singular"], ["People", "Plural"],
        ])


QUESTION_XML = """<hierarchy>
  <node class="android.view.View" content-desc="He's reading ___ interesting book."/>
  <node class="android.widget.Button" content-desc="null"/>
  <node class="android.widget.Button" content-desc="a"/>
  <node class="android.widget.Button" content-desc="an"/>
  <node class="android.widget.Button" content-desc="the"/>
</hierarchy>"""

FEEDBACK_XML = """<hierarchy>
  <node class="android.view.View" content-desc="He's reading ___ interesting book."/>
  <node class="android.widget.Button" content-desc="null"/>
  <node class="android.widget.Button" content-desc="a"/>
  <node class="android.widget.Button" content-desc="an"/>
  <node class="android.widget.Button" content-desc="the"/>
  <node class="android.view.View" content-desc="Nicely done!"/>
  <node class="android.view.View" content-desc="an"/>
  <node class="android.widget.Button" content-desc="Next"/>
</hierarchy>"""

INCORRECT_XML = """<hierarchy>
  <node class="android.view.View" content-desc="He's reading ___ interesting book."/>
  <node class="android.widget.Button" content-desc="null"/>
  <node class="android.widget.Button" content-desc="a"/>
  <node class="android.widget.Button" content-desc="an"/>
  <node class="android.widget.Button" content-desc="the"/>
  <node class="android.view.View" content-desc="Incorrect Answer!"/>
  <node class="android.view.View" content-desc="an"/>
  <node class="android.widget.Button" content-desc="Next"/>
</hierarchy>"""


class XmlDriver:
    def __init__(self, xml, next_el=None):
        self.xml = xml
        self.next_el = next_el

    @property
    def page_source(self):
        return self.xml

    def find_element(self, by, value):
        if self.next_el:
            return self.next_el
        raise NoSuchElementException(value)

    def find_elements(self, by, value):
        return [self.next_el] if self.next_el else []


class TestSaveResults(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())

    def tearDown(self):
        os.chdir(self._cwd)

    def test_every_entry_is_numbered_with_n_first(self):
        watcher.save_results([{"question": "Q1"}, {"question": "Q2", "n": 99}])
        saved = watcher.load_results()
        self.assertEqual([e["n"] for e in saved], [1, 2], "stale n gets renumbered")
        self.assertEqual(list(saved[0].keys())[0], "n", "n leads each entry")
        self.assertEqual(saved[1]["question"], "Q2")

    def test_save_folds_repeats_into_one_entry_per_question(self):
        # The first entry is CONFIRMED correct here, so it must win over
        # the repeat regardless — the "an incorrect first entry can be
        # superseded" case has its own dedicated tests below.
        results = [
            {"question": "Q1", "type": "multiple_choice", "options": ["a", "b"],
             "result": "correct", "correct_answer": ["b"]},
            {"question": "Q2", "type": "multiple_choice", "options": ["x", "y"]},
            {"question": "Q1", "type": "multiple_choice", "options": ["a", "b"],
             "result": "correct", "correct_answer": ["b"], "time": "later"},
        ]
        watcher.save_results(results)
        self.assertEqual([e["question"] for e in results], ["Q1", "Q2"])
        self.assertNotEqual(results[0].get("time"), "later", "first entry wins")
        self.assertEqual(results, watcher.load_results(),
                         "in-memory list stays the saved list")

    def test_dedupe_never_replaces_a_learned_answer_with_an_empty_reveal(self):
        results = [
            {"question": "Q1", "type": "multiple_choice", "options": ["a", "b"],
             "result": "incorrect", "correct_answer": ["b"]},
            {"question": "Q1", "type": "multiple_choice", "options": ["a", "b"],
             "result": "other"},  # nothing revealed
        ]
        deduped = qh.dedupe_results(results)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0]["correct_answer"], ["b"])

    def test_dedupe_lets_a_fresh_reveal_replace_an_unconfirmed_wrong_answer(self):
        # Live 2026-08-06 run: a fill_the_blank answer learned from an
        # incorrect attempt (n=267, 2026-08-02) kept reproducing the same
        # wrong sentence every time the question recurred — dedupe never
        # revisited it because it already "taught" something. An entry
        # whose own result was incorrect is not yet confirmed, so a fresh
        # teaching repeat must be allowed through.
        options = ["of", "the", "came", "students", "None", "lesson.", "the", "to"]
        first = {"question": "Talabalarning hech biri darsga kelmadi.",
                 "type": "fill_the_blank", "options": options,
                 "result": "incorrect",
                 "correct_answer": ["None of the students came to the lesson."]}
        repeat = {"question": "Talabalarning hech biri darsga kelmadi.",
                  "type": "fill_the_blank", "options": options,
                  "result": "incorrect",
                  "correct_answer": ["None of the lesson. came to the students"]}
        deduped = qh.dedupe_results([first, repeat])
        self.assertEqual(deduped, [repeat])

    def test_dedupe_never_touches_a_confirmed_correct_answer(self):
        # The common case must stay stable: once an answer has actually
        # worked, later repeats (even unrelated reveals) never displace it.
        first = {"question": "Q1", "type": "multiple_choice",
                  "options": ["a", "b"], "result": "correct",
                  "correct_answer": ["b"]}
        repeat = {"question": "Q1", "type": "multiple_choice",
                   "options": ["a", "b"], "result": "incorrect",
                   "correct_answer": ["a"]}
        deduped = qh.dedupe_results([first, repeat])
        self.assertEqual(deduped, [first])

    def test_dedupe_keeps_the_first_entry_untouched_when_a_question_repeats(self):
        first = {"question": "Q1", "time": "2026-08-02 15:44:12",
                 "type": "multiple_choice", "options": ["quite", "rather"],
                 "result": "correct", "correct_answer": ["rather"]}
        snapshot = dict(first)
        repeat = {"question": "Q1", "time": "2026-08-02 20:49:43",
                  "type": "multiple_choice", "options": ["rather", "quite"],
                  "result": "correct", "correct_answer": ["rather"]}
        deduped = qh.dedupe_results([first, repeat])
        self.assertEqual(deduped, [snapshot],
                         "a re-answered question keeps its original entry")

    def test_dedupe_upgrades_an_answerless_entry_when_a_repeat_reveals_one(self):
        first = {"question": "Q1", "type": "multiple_choice",
                 "options": ["a", "b"], "result": "other"}
        repeat = {"question": "Q1", "type": "multiple_choice",
                  "options": ["b", "a"], "result": "correct",
                  "correct_answer": ["b"]}
        deduped = qh.dedupe_results([first, repeat])
        self.assertEqual(deduped, [repeat])

    def test_dedupe_keeps_matching_entries_per_board(self):
        board_a = {"question": "Moslashtiring.", "type": "matching",
                   "options": ["Drive", "along the road", "Fly", "to Tashkent"],
                   "correct_answer": [["Drive", "along the road"]]}
        board_b = {"question": "Moslashtiring.", "type": "matching",
                   "options": ["Exam", "Imtihon", "Boring", "Zerikarli"],
                   "correct_answer": [["Exam", "Imtihon"]]}
        # board A re-served with shuffled cards and a newly discovered
        # pair: the original entry stays, only the new pair folds in
        board_a2 = {"question": "Moslashtiring.", "type": "matching",
                    "options": ["Fly", "to Tashkent", "Drive", "along the road"],
                    "correct_answer": [["Fly", "to Tashkent"], ["Drive", "along the road"]]}
        deduped = qh.dedupe_results([board_a, board_b, board_a2])
        self.assertEqual(len(deduped), 2, "different boards both kept")
        self.assertIn(board_b, deduped)
        merged = next(e for e in deduped if "Drive" in e["options"])
        self.assertEqual(merged["options"], board_a["options"],
                         "first board entry keeps its card order")
        self.assertEqual(merged["correct_answer"],
                         [["Drive", "along the road"], ["Fly", "to Tashkent"]])

    def test_dedupe_drops_entries_with_no_question(self):
        # A feedback sheet met before any question (attach mid-sheet)
        # logs question None — pure noise in the answer book.
        deduped = qh.dedupe_results([{"question": None, "result": "incorrect"}])
        self.assertEqual(deduped, [])


class TestPollOnce(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())

    def tearDown(self):
        os.chdir(self._cwd)

    def test_question_screen_updates_state_and_reports_it(self):
        driver = XmlDriver(QUESTION_XML)
        state = watcher.fresh_state()
        results = []
        status = watcher.poll_once(driver, state, results)
        self.assertEqual(status, "question")
        self.assertEqual(state["question"], "He's reading ___ interesting book.")
        self.assertEqual(state["options"], ["a", "an", "the"])
        self.assertEqual(results, [])

    def test_finish_screen_is_not_a_question(self):
        # "Test finished!" plus [Retry, Next lesson] must never register as
        # a multiple-choice question — option A would be Retry, silently
        # restarting the whole test.
        driver = XmlDriver(FINISH_SCREEN_XML)
        state = watcher.fresh_state()
        self.assertIsNone(watcher.poll_once(driver, state, []))
        self.assertIsNone(state["question"])

    def test_modules_list_forward_is_the_course_module_card(self):
        # After some reward flows the app falls back to the Modules list
        # (live dump 2026-08-02 21:02) — all Views, no Buttons, nothing
        # the runner knew. Forward = the configured module's card.
        import navigation
        nodes = [
            ("android.view.View", "Placement test"),
            ("android.view.View", "Modules"),
            ("android.view.View", "A1 | Beginner and elementary\n16\n25\n4 hours"),
            ("android.view.View", "B2 | Upper-Intermediate\n86\n88\n13 hours"),
        ]
        self.assertEqual(
            navigation.find_forward_button(nodes),
            "B2 | Upper-Intermediate\n86\n88\n13 hours",
        )
        # without the Modules header, module-like descs are never tapped
        self.assertIsNone(navigation.find_forward_button(nodes[2:]))

    def test_push_through_rejoins_sequence_from_a_lessons_list(self):
        # The Modules recovery lands on the module's lessons list — all
        # Views, no Buttons — where tap-through stranded (2026-08-02
        # 21:10). Rejoin the lesson sequence instead.
        import navigation
        import locators as loc
        from selenium.common.exceptions import TimeoutException

        driver = TapDriver(LESSONS_TOP_XML)
        rejoined = []

        def fake_open(d):
            rejoined.append(1)
            d.xml = QUIZ_START_XML
            return True

        def fake_tap(d, w, locator, label):
            if locator == loc.START_TEST and "Start" in d.xml:
                return
            raise TimeoutException(label)

        saved = (navigation.tap, navigation.open_next_in_sequence)
        navigation.tap = fake_tap
        navigation.open_next_in_sequence = fake_open
        try:
            self.assertTrue(navigation.push_through_to_start(driver, attempts=3))
        finally:
            navigation.tap, navigation.open_next_in_sequence = saved
        self.assertEqual(rejoined, [1])

    def test_completed_stats_screen_is_not_a_question(self):
        # The 2026-08-02 pass screen: its stat labels are Buttons, enough
        # to clear the 2-option floor — the runner tapped "Lessons" as an
        # answer and walked out of the flow. Never a question; its
        # "Next lesson" is the way forward.
        import navigation
        driver = XmlDriver(TEST_COMPLETED_XML)
        state = watcher.fresh_state()
        self.assertIsNone(watcher.poll_once(driver, state, []))
        self.assertIsNone(state["question"])
        nodes = qh.parse_screen(TEST_COMPLETED_XML)
        self.assertEqual(navigation.find_forward_button(nodes), "Next lesson")

    def test_streak_popup_is_not_a_question(self):
        # Its "3" View plus at most one candidate button ("Last week") must
        # not count as a question — the runner has to fall through to
        # dismiss_popup instead of waiting for a feedback sheet forever.
        driver = XmlDriver(STREAK_POPUP_XML)
        state = watcher.fresh_state()
        results = []
        self.assertIsNone(watcher.poll_once(driver, state, results))
        self.assertIsNone(state["question"])
        self.assertEqual(results, [])

    def test_feedback_sheet_logs_type_result_and_correct_answer(self):
        driver = XmlDriver(QUESTION_XML)
        state = watcher.fresh_state()
        results = []
        watcher.poll_once(driver, state, results)  # remember the question

        class NextEl(FakeElement):
            def click(inner):
                driver.xml = QUESTION_XML

        driver.next_el = NextEl("Next")
        driver.xml = FEEDBACK_XML
        status = watcher.poll_once(driver, state, results)

        self.assertEqual(status, "correct")
        entry = results[0]
        self.assertEqual(entry["type"], "multiple_choice")
        self.assertEqual(entry["result"], "correct")
        self.assertEqual(entry["question"], "He's reading ___ interesting book.")
        self.assertEqual(entry["options"], ["a", "an", "the"])
        self.assertEqual(entry["correct_answer"], ["an"])
        self.assertEqual(state["correct"], 1)

    def test_incorrect_sheet_saves_revealed_answer_too(self):
        driver = XmlDriver(QUESTION_XML)
        state = watcher.fresh_state()
        results = []
        watcher.poll_once(driver, state, results)  # remember the question

        class NextEl(FakeElement):
            def click(inner):
                driver.xml = QUESTION_XML

        driver.next_el = NextEl("Next")
        driver.xml = INCORRECT_XML
        watcher.poll_once(driver, state, results)

        entry = results[0]
        self.assertEqual(entry["result"], "incorrect")
        self.assertEqual(entry["correct_answer"], ["an"])
        self.assertEqual(state["incorrect"], 1)

    def test_matching_sheet_saves_pairs_discovered_by_runner(self):
        matching_sheet = """<hierarchy>
          <node class="android.view.View" content-desc="Moslashtiring."/>
          <node class="android.view.View" content-desc="Nicely done!"/>
          <node class="android.widget.Button" content-desc="Next"/>
        </hierarchy>"""

        driver = XmlDriver(matching_sheet)
        state = watcher.fresh_state()
        state["question"] = "Moslashtiring."
        state["options"] = ["Drive", "along the road", "Fly", "to Tashkent"]
        state["pending_pairs"] = [["Drive", "along the road"], ["Fly", "to Tashkent"]]
        results = []

        class NextEl(FakeElement):
            def click(inner):
                driver.xml = QUESTION_XML

        driver.next_el = NextEl("Next")
        watcher.poll_once(driver, state, results)

        entry = results[0]
        self.assertEqual(entry["type"], "matching")
        self.assertEqual(
            entry["correct_answer"],
            [["Drive", "along the road"], ["Fly", "to Tashkent"]],
        )
        self.assertNotIn("pending_pairs", state)

    def test_stale_pending_pairs_discarded_on_non_matching_sheet(self):
        driver = XmlDriver(QUESTION_XML)
        state = watcher.fresh_state()
        results = []
        watcher.poll_once(driver, state, results)  # remember the MC question
        state["pending_pairs"] = [["Drive", "along the road"]]  # leftover

        class NextEl(FakeElement):
            def click(inner):
                driver.xml = QUESTION_XML

        driver.next_el = NextEl("Next")
        driver.xml = FEEDBACK_XML
        watcher.poll_once(driver, state, results)

        entry = results[0]
        self.assertEqual(entry["correct_answer"], ["an"])  # diff, not pairs
        self.assertNotIn("pending_pairs", state)


class TestReconnect(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        self._saved = (watcher.POLL_INTERVAL, watcher.RECONNECT_DELAY, watcher.MAX_RECONNECTS)
        watcher.POLL_INTERVAL = 0
        watcher.RECONNECT_DELAY = 0

    def tearDown(self):
        watcher.POLL_INTERVAL, watcher.RECONNECT_DELAY, watcher.MAX_RECONNECTS = self._saved
        os.chdir(self._cwd)

    def test_run_reconnects_after_connection_error(self):
        class DeadDriver:
            quit_called = False

            @property
            def page_source(self):
                raise WebDriverException("socket hang up")

            def quit(self):
                self.quit_called = True

        class StopDriver:
            @property
            def page_source(self):
                raise KeyboardInterrupt

            def quit(self):
                pass

        dead = DeadDriver()
        drivers = [dead, StopDriver()]
        made = []
        watcher.run(lambda: made.append(1) or drivers[len(made) - 1])
        self.assertEqual(len(made), 2, "should reconnect once then stop cleanly")
        self.assertTrue(dead.quit_called, "dead session should be closed before reconnect")

    def test_run_gives_up_after_max_reconnects(self):
        watcher.MAX_RECONNECTS = 1

        class DeadDriver:
            @property
            def page_source(self):
                raise WebDriverException("socket hang up")

            def quit(self):
                pass

        made = []
        with self.assertRaises(WebDriverException):
            watcher.run(lambda: made.append(1) or DeadDriver())
        self.assertEqual(len(made), 2, "initial connect + one reconnect, then give up")


class TestSessionSettings(unittest.TestCase):
    """Every session must CAP UiAutomator2's wait-for-idle, not disable it.
    Never-idle screens (the chest reward's looping animation) stall every
    command for the full timeout, so 10s is minutes per chest — but 0 makes
    dumps and chip taps race the question screens' entry animation and the
    runner answers from half a chip row (2026-08-03: ~33% wrong)."""

    def test_connect_caps_wait_for_idle(self):
        captured = {}

        class FakeRemote:
            def __init__(self, server, options=None, client_config=None):
                pass

            def update_settings(self, settings):
                captured.update(settings)

        original = watcher.webdriver.Remote
        watcher.webdriver.Remote = FakeRemote
        try:
            watcher.connect(attach=True)
        finally:
            watcher.webdriver.Remote = original
        timeout = captured.get("waitForIdleTimeout")
        self.assertIsNotNone(timeout, "connect() must cap waitForIdleTimeout")
        self.assertTrue(
            0 < timeout <= 3000,
            f"waitForIdleTimeout must stay small but nonzero, got {timeout}",
        )


class TestConnectionResilience(unittest.TestCase):
    """A command lost over Wi-Fi adb must raise within a bounded time and
    trigger a reconnect — never block the runner forever."""

    def setUp(self):
        self._saved = (watcher.RECONNECT_DELAY, watcher.MAX_RECONNECTS)
        watcher.RECONNECT_DELAY = 0

    def tearDown(self):
        watcher.RECONNECT_DELAY, watcher.MAX_RECONNECTS = self._saved

    def test_connect_bounds_every_command_with_a_timeout(self):
        captured = {}

        class FakeRemote:
            def __init__(self, server, options=None, client_config=None):
                captured["client_config"] = client_config

            def update_settings(self, settings):
                pass

        original = watcher.webdriver.Remote
        watcher.webdriver.Remote = FakeRemote
        try:
            watcher.connect(attach=True)
        finally:
            watcher.webdriver.Remote = original
        cc = captured["client_config"]
        self.assertIsNotNone(cc, "connect() must pass a client_config")
        self.assertEqual(cc.timeout, watcher.COMMAND_TIMEOUT)

    def test_connection_errors_include_transport_timeouts(self):
        # selenium does not wrap urllib3 errors: a timed-out request raises
        # ReadTimeoutError/MaxRetryError, not WebDriverException
        from urllib3.exceptions import MaxRetryError, ReadTimeoutError
        self.assertTrue(issubclass(ReadTimeoutError, watcher.CONNECTION_ERRORS))
        self.assertTrue(issubclass(MaxRetryError, watcher.CONNECTION_ERRORS))
        self.assertTrue(issubclass(WebDriverException, watcher.CONNECTION_ERRORS))

    def test_answer_until_done_reattaches_after_transport_stall(self):
        import main as main_mod
        from urllib3.exceptions import ReadTimeoutError

        class FakeSession:
            quit_called = False

            def quit(self):
                self.quit_called = True

        first, second = FakeSession(), FakeSession()
        seen, attaches = [], []

        def fake_loop(driver):
            seen.append(driver)
            if driver is first:
                raise ReadTimeoutError(None, "127.0.0.1", "Read timed out")

        def fake_connect(attach=True):
            attaches.append(attach)
            return second

        saved = (main_mod.auto_answer_loop, watcher.connect)
        main_mod.auto_answer_loop, watcher.connect = fake_loop, fake_connect
        try:
            main_mod.answer_until_done(first)
        finally:
            main_mod.auto_answer_loop, watcher.connect = saved

        self.assertEqual(seen, [first, second], "loop should resume on the new session")
        self.assertEqual(attaches, [True], "must reattach, not relaunch the app")
        self.assertTrue(first.quit_called, "stalled session should be closed")
        self.assertTrue(second.quit_called, "helper owns final cleanup")

    def test_answer_until_done_gives_up_after_max_reconnects(self):
        import main as main_mod
        from urllib3.exceptions import ReadTimeoutError

        watcher.MAX_RECONNECTS = 1

        class FakeSession:
            def quit(self):
                pass

        attempts = []

        def fake_loop(driver):
            attempts.append(1)
            raise ReadTimeoutError(None, "127.0.0.1", "Read timed out")

        saved = (main_mod.auto_answer_loop, watcher.connect)
        main_mod.auto_answer_loop = fake_loop
        watcher.connect = lambda attach=True: FakeSession()
        try:
            with self.assertRaises(ReadTimeoutError):
                main_mod.answer_until_done(FakeSession())
        finally:
            main_mod.auto_answer_loop, watcher.connect = saved

        self.assertEqual(len(attempts), 2, "initial attempt + one reconnect, then give up")


# The task-reward chest flow has no X icon on any of its three screens:
# a tasks screen whose only way forward is "Open chest", then a chest
# image that must be tapped by position (no buttons at all), then a
# "+50 stars" screen with Continue. The unlabeled top-left clickable in
# the first two fixtures stands in for a decorative icon that must never
# be mistaken for a popup's X.
CHEST_TASKS_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[26,102][117,154]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[150,180][570,240]" clickable="false" content-desc="+1 point for completed task"/>
  <node class="android.view.View" bounds="[100,300][500,390]" clickable="false" content-desc="1 ta darsni 100% aniqlikda bajaring"/>
  <node class="android.view.View" bounds="[200,400][280,440]" clickable="false" content-desc="0/1"/>
  <node class="android.view.View" bounds="[100,520][500,570]" clickable="false" content-desc="Bugun 3 ta darsni yakunlang"/>
  <node class="android.view.View" bounds="[120,590][600,630]" clickable="false" content-desc="3/3"/>
  <node class="android.widget.Button" bounds="[42,1418][678,1502]" clickable="true" content-desc="Open chest"/>
</hierarchy>"""

CHEST_TAP_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[26,102][117,154]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[180,290][540,350]" clickable="false" content-desc="Get your reward"/>
  <node class="android.view.View" bounds="[80,420][560,470]" clickable="false" content-desc="5 ta turli darsni yakunlang"/>
  <node class="android.view.View" bounds="[570,420][650,470]" clickable="false" content-desc="+50"/>
  <node class="android.view.View" bounds="[230,770][490,1000]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[200,1230][520,1280]" clickable="false" content-desc="Tap on the chest!"/>
</hierarchy>"""

CHEST_STARS_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[540,90][700,160]" clickable="false" content-desc="115"/>
  <node class="android.view.View" bounds="[240,600][480,660]" clickable="false" content-desc="+50 stars"/>
  <node class="android.widget.Button" bounds="[42,1418][678,1502]" clickable="true" content-desc="Continue"/>
</hierarchy>"""


class ChestFlowDriver:
    """Fake driver that walks the chest-reward flow as taps land."""

    def __init__(self):
        self.xml = CHEST_TASKS_XML
        self.coord_taps = []
        self.clicked = []

    @property
    def page_source(self):
        return self.xml

    def find_element(self, by, value):
        el = FakeElement("forward")

        def click():
            self.clicked.append(value)
            if "Open chest" in value:
                self.xml = CHEST_TAP_XML
            elif "Continue" in value:
                self.xml = HOME_SCREEN_XML

        el.click = click
        return el

    def tap(self, positions, duration=None):
        self.coord_taps.append(positions[0])
        self.xml = CHEST_STARS_XML


class TestChestRewardFlow(unittest.TestCase):
    def setUp(self):
        import navigation
        self._sleep = navigation.time.sleep
        navigation.time.sleep = lambda s: None

    def tearDown(self):
        import navigation
        navigation.time.sleep = self._sleep

    def test_find_forward_button_opens_task_chest(self):
        import navigation
        nodes = qh.parse_screen(CHEST_TASKS_XML)
        self.assertEqual(navigation.find_forward_button(nodes), "Open chest")

    def test_tap_forward_button_taps_the_chest_image_by_position(self):
        import navigation
        driver = ChestFlowDriver()
        driver.xml = CHEST_TAP_XML
        self.assertTrue(navigation.tap_forward_button(driver))
        self.assertEqual(driver.coord_taps, [(360, 992)])

    def test_chest_tap_walks_nearby_heights_until_screen_changes(self):
        # If the first tap misses the chest (screen unchanged), nearby
        # heights on the center column are tried before giving up.
        import navigation
        driver = TapDriver(CHEST_TAP_XML)
        self.assertFalse(navigation.tap_forward_button(driver))
        self.assertEqual(driver.taps, [(360, 992), (360, 880), (360, 1088)])

    def test_full_flow_open_chest_then_tap_then_continue(self):
        import navigation
        driver = ChestFlowDriver()
        for _ in range(3):
            self.assertTrue(navigation.tap_forward_button(driver))
        self.assertTrue(any("Open chest" in v for v in driver.clicked), driver.clicked)
        self.assertEqual(driver.coord_taps, [(360, 992)])
        self.assertTrue(any("Continue" in v for v in driver.clicked), driver.clicked)

    def test_chest_screens_are_never_blind_tapped(self):
        # These screens have no X at all — an unlabeled top-left icon on
        # them is decoration, and tapping it must not count as a dismissal
        # (a phantom success would loop forever without moving forward).
        import navigation
        for xml in (CHEST_TASKS_XML, CHEST_TAP_XML):
            driver = TapDriver(xml)
            self.assertFalse(navigation.dismiss_popup(driver))
            self.assertEqual(driver.taps, [])

    def test_chest_tasks_screen_is_not_a_question(self):
        # Guard: one "Open chest" button stays under the 2-option floor,
        # so the runner falls through to tap_forward_button.
        driver = XmlDriver(CHEST_TASKS_XML)
        state = watcher.fresh_state()
        self.assertIsNone(watcher.poll_once(driver, state, []))
        self.assertIsNone(state["question"])


# The 2026-08-02 stranding proved the real chest screen exposes none of
# the descs CHEST_TAP_XML expects (no chest tap, no question, no dismiss
# attempt in the idle window — and no dump was ever captured). These two
# fixtures model how it may actually render: texts merged/padded into
# larger descs, or (native rendering) in text attributes with no descs.
CHEST_TAP_MERGED_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[100,290][620,470]" clickable="false" content-desc="Get your reward\n5 ta turli darsni yakunlang\n+50"/>
  <node class="android.view.View" bounds="[200,1230][520,1280]" clickable="false" content-desc="Tap on the chest! "/>
</hierarchy>"""

CHEST_TAP_TEXT_ATTR_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true"/>
  <node class="android.widget.TextView" bounds="[180,290][540,350]" clickable="false" text="Get your reward"/>
  <node class="android.widget.TextView" bounds="[200,1230][520,1280]" clickable="false" text="Tap on the chest!"/>
</hierarchy>"""


# The full-screen chest overlay as it renders since the 2026-08-02 app
# update (live dump): ALL texts merged into one desc on one clickable
# full-screen View. It can pop over any screen mid-navigation.
CHEST_OVERLAY_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc="Get your reward\nDaily streak\n+10\nLesson completed\n+5\nTotal\n+15\nTap on the chest!"/>
</hierarchy>"""


class TestTapClearsOverlay(unittest.TestCase):
    def setUp(self):
        import navigation
        self._saved = (navigation.dismiss_popup, navigation.tap_forward_button,
                       navigation.time.sleep)
        navigation.time.sleep = lambda s: None

    def tearDown(self):
        import navigation
        (navigation.dismiss_popup, navigation.tap_forward_button,
         navigation.time.sleep) = self._saved

    def test_tap_clears_a_covering_overlay_and_retries(self):
        import navigation
        from selenium.common.exceptions import TimeoutException

        attempts = []

        class Waiter:
            def until(self, cond):
                attempts.append(1)
                if len(attempts) == 1:
                    raise TimeoutException("covered by the chest overlay")
                return FakeElement("target")

        cleared = []
        navigation.dismiss_popup = lambda d: False
        navigation.tap_forward_button = lambda d: cleared.append(1) or True
        navigation.tap(None, Waiter(), ("xpath", "x"), "Get certificate")
        self.assertEqual(cleared, [1])
        self.assertEqual(len(attempts), 2, "must retry after clearing")

    def test_tap_reraises_when_nothing_clears(self):
        import navigation
        from selenium.common.exceptions import TimeoutException

        class Waiter:
            def until(self, cond):
                raise TimeoutException("nothing there")

        navigation.dismiss_popup = lambda d: False
        navigation.tap_forward_button = lambda d: False
        with self.assertRaises(TimeoutException):
            navigation.tap(None, Waiter(), ("xpath", "x"), "Get certificate")

    def test_full_screen_merged_overlay_is_recognized_as_chest(self):
        import navigation
        nodes = qh.parse_screen(CHEST_OVERLAY_XML)
        self.assertTrue(navigation.on_chest_screen(nodes))
        self.assertTrue(navigation.chest_tap_caption(nodes))
        driver = TapDriver(CHEST_OVERLAY_XML)
        self.assertFalse(navigation.dismiss_popup(driver),
                         "never blind-tapped — the tap-chest path handles it")
        self.assertEqual(driver.taps, [])


# The 2026-08-04 chest slowdown: the flow popped over the home screen at
# launch, and every clearing round only ran AFTER tap()'s full 20s
# presence timeout — three flow screens cost a minute-plus of staring at
# "Tap on the chest!". reveal_card made it worse by spending its swipe
# budget on a screen that cannot scroll. The chest markers are
# unambiguous, so a covering chest is cleared the moment it is seen.
class ChestOverHomeDriver:
    """Chest flow covering the home screen; the target card becomes
    findable only after the flow is walked (chest tap → stars → Continue)."""

    def __init__(self):
        self.xml = CHEST_TAP_XML
        self.coord_taps = []
        self.clicked = []
        self.swipes = 0

    @property
    def page_source(self):
        return self.xml

    def get_window_size(self):
        return {"width": 720, "height": 1600}

    def swipe(self, *a, **kw):
        self.swipes += 1

    def find_element(self, by, value):
        m = re.search(r"content-desc=(?:'([^']*)'|\"([^\"]*)\")", value)
        wanted = (m.group(1) or m.group(2)) if m else value
        if wanted not in self.xml:
            raise NoSuchElementException(wanted)
        el = FakeElement(wanted)

        def click():
            self.clicked.append(wanted)
            if "Continue" in wanted:
                self.xml = HOME_SCROLLED_XML

        el.click = click
        return el

    def tap(self, positions, duration=None):
        self.coord_taps.append(positions[0])
        if self.xml == CHEST_TAP_XML:
            self.xml = CHEST_STARS_XML


class TestChestClearedMidWait(unittest.TestCase):
    def setUp(self):
        import navigation
        self._sleep = navigation.time.sleep
        navigation.time.sleep = lambda s: None

    def tearDown(self):
        import navigation
        navigation.time.sleep = self._sleep

    def test_tap_clears_a_covering_chest_without_waiting_out_the_timeout(self):
        # Every clearing round used to cost the waiter's FULL timeout
        # (20s live) before the chest was even looked at — the whole
        # flow must clear in well under one timeout's worth of waiting.
        import time as real_time
        import locators as loc
        import navigation
        from selenium.webdriver.support.ui import WebDriverWait

        driver = ChestOverHomeDriver()
        started = real_time.time()
        navigation.tap(driver, WebDriverWait(driver, 2),
                       loc.PROGRAM_CERTIFICATE, "Program Certificate")
        elapsed = real_time.time() - started
        self.assertEqual(driver.coord_taps, [(360, 992)])
        self.assertIn("Continue", driver.clicked)
        self.assertIn("2+6 Program Certificate", driver.clicked)
        self.assertLess(elapsed, 1.5,
                        "the chest must be cleared as soon as it is seen, "
                        "not after full presence timeouts")

    def test_reveal_card_clears_the_chest_instead_of_swiping_at_it(self):
        import navigation
        driver = ChestOverHomeDriver()
        self.assertTrue(navigation.reveal_card(driver, "2+6 Program Certificate"))
        self.assertEqual(driver.coord_taps, [(360, 992)])
        self.assertEqual(driver.swipes, 0,
                         "the chest screen cannot scroll — swiping it is "
                         "pure wasted time")


class DailyRewardOverHomeDriver:
    """Daily Reward sheet covering the home screen; the target card
    becomes findable only after the sheet is backed out of — mirrors
    ChestOverHomeDriver above, for the same early-detection path."""

    def __init__(self):
        self.xml = DAILY_REWARD_XML
        self.clicked = []
        self.back_presses = 0

    @property
    def page_source(self):
        return self.xml

    def find_element(self, by, value):
        m = re.search(r"content-desc=(?:'([^']*)'|\"([^\"]*)\")", value)
        wanted = (m.group(1) or m.group(2)) if m else value
        if wanted not in self.xml:
            raise NoSuchElementException(wanted)
        el = FakeElement(wanted)
        el.click = lambda: self.clicked.append(wanted)
        return el

    def back(self):
        self.back_presses += 1
        self.xml = HOME_SCROLLED_XML


class TestDailyRewardClearedMidWait(unittest.TestCase):
    def setUp(self):
        import navigation
        self._sleep = navigation.time.sleep
        navigation.time.sleep = lambda s: None

    def tearDown(self):
        import navigation
        navigation.time.sleep = self._sleep

    def test_tap_clears_a_covering_daily_reward_sheet_without_waiting_out_the_timeout(self):
        # Same failure shape as the chest overlay (see
        # TestChestClearedMidWait above): left to the except-
        # TimeoutException fallback, closing the sheet paid the waiter's
        # FULL timeout (20-30s live) before dismiss_popup ever ran — a
        # live run.log confirmed every Daily Reward close that run went
        # through the slow 'Cleared an overlay ... retrying' path, never
        # a fast one. Must now clear in well under one timeout's worth
        # of waiting.
        import time as real_time
        import locators as loc
        import navigation
        from selenium.webdriver.support.ui import WebDriverWait

        driver = DailyRewardOverHomeDriver()
        started = real_time.time()
        navigation.tap(driver, WebDriverWait(driver, 2),
                       loc.PROGRAM_CERTIFICATE, "Program Certificate")
        elapsed = real_time.time() - started
        self.assertEqual(driver.back_presses, 1)
        self.assertIn("2+6 Program Certificate", driver.clicked)
        self.assertLess(elapsed, 1.5,
                        "the Daily Reward sheet must be cleared as soon as "
                        "it is seen, not after full presence timeouts")


class TestTapRetriesAfterStaleClick(unittest.TestCase):
    def setUp(self):
        import navigation
        self._sleep = navigation.time.sleep
        navigation.time.sleep = lambda s: None

    def tearDown(self):
        import navigation
        navigation.time.sleep = self._sleep

    def test_tap_re_locates_after_a_stale_click_instead_of_raising(self):
        # A screen just after a fresh app launch can still be settling
        # (a live run's page-source size climbed across three checks in
        # a row before it stopped changing), so an element found a
        # moment ago can already be gone by click time. Every other
        # find-then-click site in navigation.py already tolerates that
        # (tap_desc, tap_forward_button) — tap() must too, instead of
        # letting a stale click escalate all the way to main.py's
        # app-restart handler (observed live as a "crash at the start"
        # loop: restart landed on an equally unsettled screen and raced
        # again).
        import locators as loc
        import navigation
        from selenium.webdriver.support.ui import WebDriverWait

        el = FakeElement("2+6 Program Certificate", click_fails=1)
        driver = FakeDriver([el])
        navigation.tap(driver, WebDriverWait(driver, 2),
                       loc.PROGRAM_CERTIFICATE, "Program Certificate")
        self.assertTrue(el.clicked)


class TestChestScreenVariants(unittest.TestCase):
    def setUp(self):
        import navigation
        self._sleep = navigation.time.sleep
        navigation.time.sleep = lambda s: None

    def tearDown(self):
        import navigation
        navigation.time.sleep = self._sleep

    def test_parse_screen_falls_back_to_text_attribute(self):
        nodes = qh.parse_screen(CHEST_TAP_TEXT_ATTR_XML)
        self.assertIn(("android.widget.TextView", "Tap on the chest!"), nodes)

    def test_parse_screen_prefers_desc_over_text(self):
        xml = '<hierarchy><node class="c" content-desc="desc" text="text"/></hierarchy>'
        self.assertIn(("c", "desc"), qh.parse_screen(xml))

    def test_variant_chest_screens_are_tapped_by_position(self):
        import navigation
        for xml in (CHEST_TAP_MERGED_XML, CHEST_TAP_TEXT_ATTR_XML):
            driver = TapDriver(xml)
            navigation.tap_forward_button(driver)
            self.assertEqual(driver.taps[0], (360, 992), xml)

    def test_variant_chest_screens_never_dismissed_or_backed_out_of(self):
        import navigation
        for xml in (CHEST_TAP_MERGED_XML, CHEST_TAP_TEXT_ATTR_XML):
            driver = TapDriver(xml)
            self.assertFalse(navigation.dismiss_popup(driver))
            self.assertEqual(driver.taps, [], xml)
            self.assertEqual(driver.back_presses, 0, xml)

    def test_open_chest_inside_merged_desc_is_the_forward_button(self):
        # The full desc is returned so the content-desc xpath finds it.
        import navigation
        nodes = [("android.view.View", "Bugungi vazifalar\nOpen chest")]
        self.assertEqual(
            navigation.find_forward_button(nodes), "Bugungi vazifalar\nOpen chest"
        )


# Ad screens the app may add at any time, in the two shapes the answer
# loop can meet: an unrecognized screen (below the 2-option question
# floor), and a fake question — a text plus 2+ buttons whose "answers"
# never produce a feedback sheet. Both must lead to an app restart with
# the tree saved, and the ad's buttons must never be tapped blindly.
UNKNOWN_AD_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[100,600][620,760]" clickable="false" content-desc="Yangi imkoniyat!"/>
  <node class="android.widget.Button" bounds="[42,1418][678,1502]" clickable="true" content-desc="Ochish"/>
</hierarchy>"""

AD_QUESTION_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[100,600][620,760]" clickable="false" content-desc="Ibrat Pro — yangi imkoniyat!"/>
  <node class="android.widget.Button" bounds="[42,1200][678,1290]" clickable="true" content-desc="Ochish"/>
  <node class="android.widget.Button" bounds="[42,1320][678,1410]" clickable="true" content-desc="Keyinroq"/>
</hierarchy>"""


class TestStuckScreenRestart(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        import main as main_mod
        self._main_time = main_mod.time
        main_mod.time = FakeTime()

    def tearDown(self):
        import main as main_mod
        main_mod.time = self._main_time
        os.chdir(self._cwd)

    def test_unrecognized_screen_saves_tree_and_restarts_after_10s(self):
        import main as main_mod
        driver = TapDriver(UNKNOWN_AD_XML)
        with self.assertRaises(main_mod.StuckScreenError):
            main_mod.auto_answer_loop(driver)
        with open(main_mod.STUCK_SCREEN_FILE, encoding="utf-8") as f:
            self.assertEqual(f.read(), UNKNOWN_AD_XML)
        self.assertEqual(driver.taps, [], "an unknown ad must never be tapped")
        self.assertEqual(driver.back_presses, 0)

    def test_fake_question_restarts_after_two_sheetless_attempts(self):
        import main as main_mod

        class AdDriver(TapDriver):
            def find_element(self, by, value):
                return FakeElement("ad button")

        driver = AdDriver(AD_QUESTION_XML)
        with self.assertRaises(main_mod.StuckScreenError):
            main_mod.auto_answer_loop(driver)
        self.assertTrue(os.path.exists(main_mod.STUCK_SCREEN_FILE))

    def test_unprepared_question_tries_first_option_plus_continue_before_restart(self):
        # A question shape we're not ready for: the typed attempt gets no
        # sheet, so the second attempt must be the generic move — first
        # option, then Continue — and only then the restart hammer.
        import main as main_mod
        clicks = []

        class AdDriver(TapDriver):
            def find_element(self, by, value):
                el = FakeElement("btn")
                el.click = lambda: clicks.append(value)
                return el

        driver = AdDriver(AD_QUESTION_XML)
        with self.assertRaises(main_mod.StuckScreenError):
            main_mod.auto_answer_loop(driver)
        self.assertTrue(any("Ochish" in v for v in clicks), clicks)
        self.assertTrue(
            any("Continue" in v for v in clicks),
            f"the fallback must try Continue: {clicks}",
        )


# Trimmed from the real stuck_screen.xml captured 2026-08-02: the app had
# crashed mid-question and the "unrecognized screen" was the Android
# launcher. Blind taps there can open unrelated apps, so the runner must
# detect the foreign foreground package and relaunch instead.
LAUNCHER_XML = """<hierarchy>
  <node class="android.widget.TextView" bounds="[14,703][187,1002]" clickable="true" content-desc="WhatsApp" text="WhatsApp"/>
  <node class="android.view.View" bounds="[0,77][720,1516]" clickable="false" content-desc="Home"/>
  <node class="android.widget.TextView" bounds="[360,1344][533,1481]" clickable="true" content-desc="Chrome" text="Chrome"/>
</hierarchy>"""


class TestAppLostRecovery(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())

    def tearDown(self):
        os.chdir(self._cwd)

    def test_launcher_in_foreground_aborts_the_loop_without_taps(self):
        import main as main_mod
        driver = TapDriver(LAUNCHER_XML)
        driver.current_package = "com.android.launcher3"
        with self.assertRaises(main_mod.AppLostError):
            main_mod.auto_answer_loop(driver)
        self.assertEqual(driver.taps, [], "never blind-tap another app's screen")
        self.assertEqual(driver.back_presses, 0)

    def test_main_relaunches_the_app_after_a_crash(self):
        import main as main_mod
        sessions = []

        class FakeSession:
            def quit(self):
                pass

        def fake_connect(attach=False):
            sessions.append(attach)
            return FakeSession()

        outcomes = [
            main_mod.AppLostError("com.android.launcher3"),
            main_mod.StuckScreenError("unrecognized screen for over 10s"),
            None,
        ]

        def fake_answer(driver):
            outcome = outcomes.pop(0)
            if outcome:
                raise outcome

        saved = (main_mod.wake_device, main_mod.force_stop_app, watcher.connect,
                 main_mod.navigate_to_test, main_mod.answer_until_done, main_mod.time)
        main_mod.wake_device = lambda: True
        main_mod.force_stop_app = lambda: True
        watcher.connect = fake_connect
        main_mod.navigate_to_test = lambda d, w, wl: True
        main_mod.answer_until_done = fake_answer
        main_mod.time = FakeTime()
        try:
            main_mod.main()
        finally:
            (main_mod.wake_device, main_mod.force_stop_app, watcher.connect,
             main_mod.navigate_to_test, main_mod.answer_until_done, main_mod.time) = saved
        self.assertEqual(len(sessions), 3, "a fresh app launch after each restart cause")
        self.assertEqual(outcomes, [], "answering resumed on the final session")

    def test_main_restarts_after_connection_error_during_navigation(self):
        # The device-side instrumentation can die while navigating (e.g. a
        # second runner on the same phone restarts it) — that must restart
        # the cycle with a fresh session, not kill the process.
        import main as main_mod
        sessions = []

        class FakeSession:
            def quit(self):
                pass

        outcomes = [WebDriverException("instrumentation is not running"), None]

        def fake_navigate(driver, wait, wait_long):
            outcome = outcomes.pop(0)
            if outcome:
                raise outcome
            return True

        saved = (main_mod.wake_device, main_mod.force_stop_app, main_mod.adb_shell,
                 watcher.connect, main_mod.navigate_to_test,
                 main_mod.answer_until_done, main_mod.time)
        main_mod.wake_device = lambda: True
        main_mod.force_stop_app = lambda: True
        main_mod.adb_shell = lambda *args: True
        watcher.connect = lambda attach=False: sessions.append(1) or FakeSession()
        main_mod.navigate_to_test = fake_navigate
        main_mod.answer_until_done = lambda driver: None
        main_mod.time = FakeTime()
        try:
            main_mod.main()
        finally:
            (main_mod.wake_device, main_mod.force_stop_app, main_mod.adb_shell,
             watcher.connect, main_mod.navigate_to_test,
             main_mod.answer_until_done, main_mod.time) = saved
        self.assertEqual(len(sessions), 2, "a fresh session after the dropped one")
        self.assertEqual(outcomes, [], "navigation succeeded on the retry")

    def test_connect_retries_after_clearing_stale_instrumentation(self):
        import main as main_mod
        attempts = []

        def flaky_connect(attach=False):
            attempts.append(1)
            if len(attempts) == 1:
                raise WebDriverException("instrumentation cannot be initialized")
            return "session"

        killed = []
        saved = (watcher.connect, main_mod.adb_shell, main_mod.time)
        watcher.connect = flaky_connect
        main_mod.adb_shell = lambda *args: killed.append(args) or True
        main_mod.time = FakeTime()
        try:
            self.assertEqual(main_mod.connect_fresh_session(), "session")
        finally:
            watcher.connect, main_mod.adb_shell, main_mod.time = saved
        self.assertEqual(len(attempts), 2)
        self.assertTrue(
            any("io.appium.uiautomator2.server" in a for k in killed for a in k), killed
        )


# The lesson page the course sequence often lands on: a video player up
# top, the lesson text, and a "Next" button. While the video is still
# loading, taps on Next are swallowed and the screen stays exactly like
# this — only a later re-tap (or a human tap) moves it forward.
LESSON_SCREEN_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[100,60][620,300]" clickable="true" content-desc="That/This/Those/These IBRAT FARZANDLARI"/>
  <node class="android.view.View" bounds="[42,350][678,420]" clickable="false" content-desc="Dars 73 That / This / Those / These"/>
  <node class="android.view.View" bounds="[42,440][678,700]" clickable="false" content-desc="Demonstrative pronouns are used to point to specific people, objects, or places."/>
  <node class="android.widget.Button" bounds="[26,1418][117,1502]" clickable="true" content-desc="null"/>
  <node class="android.widget.Button" bounds="[160,1418][678,1502]" clickable="true" content-desc="Next"/>
</hierarchy>"""

QUIZ_START_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[30,100][100,170]" clickable="true" content-desc="Go back"/>
  <node class="android.view.View" bounds="[280,600][440,660]" clickable="false" content-desc="Quizzes"/>
  <node class="android.view.View" bounds="[100,700][620,760]" clickable="false" content-desc="Press the start button when you are ready"/>
  <node class="android.widget.Button" bounds="[42,1418][678,1502]" clickable="true" content-desc="Start"/>
</hierarchy>"""


class StuckLessonDriver:
    """Lesson page that swallows Next taps until the video has 'loaded'."""

    def __init__(self, works_after_taps):
        self.xml = LESSON_SCREEN_XML
        self.taps = []
        self.works_after = works_after_taps

    @property
    def page_source(self):
        return self.xml

    def find_element(self, by, value):
        el = FakeElement("forward")

        def click():
            self.taps.append(value)
            if len(self.taps) >= self.works_after:
                self.xml = QUIZ_START_XML

        el.click = click
        return el


class ManualAdvanceDriver:
    """Lesson page that only moves because the human tapped on the phone:
    the screen changes after a fixed number of reads, never from a tap."""

    def __init__(self, change_after_reads):
        self.reads = 0
        self.change_after = change_after_reads
        self.taps = []

    @property
    def page_source(self):
        self.reads += 1
        return QUIZ_START_XML if self.reads > self.change_after else LESSON_SCREEN_XML

    def find_element(self, by, value):
        el = FakeElement("forward")
        el.click = lambda: self.taps.append(value)
        return el


class NeverAdvancingDriver:
    """A screen that offers a forward button but never actually moves.

    The 2026-08-04 "Student Already Enrolled" banner on the Modules list
    behaved exactly this way: every Start tap "succeeded", the banner
    redrew, and the screen stayed put. page_source raises once the reads
    pass `read_limit` so a regression fails the suite instead of hanging
    it forever.
    """

    def __init__(self, read_limit=10000):
        self.taps = []
        self.reads = 0
        self.read_limit = read_limit

    @property
    def page_source(self):
        self.reads += 1
        if self.reads > self.read_limit:
            raise AssertionError(
                f"still waiting after {self.reads} screen reads — "
                "the wait never gives up on a button that does nothing"
            )
        return LESSON_SCREEN_XML

    def find_element(self, by, value):
        el = FakeElement("forward")
        el.click = lambda: self.taps.append(value)
        return el


class TestWaitForManualAdvance(unittest.TestCase):
    def setUp(self):
        import navigation
        self._time = navigation.time
        navigation.time = FakeTime()

    def tearDown(self):
        import navigation
        navigation.time = self._time

    def test_retaps_next_until_slow_video_lets_the_screen_move(self):
        import navigation
        driver = StuckLessonDriver(works_after_taps=2)
        self.assertTrue(navigation.wait_for_manual_advance(driver))
        self.assertEqual(len(driver.taps), 2)
        self.assertTrue(all("Next" in t for t in driver.taps), driver.taps)

    def test_returns_when_the_human_taps_on_the_phone(self):
        import navigation
        driver = ManualAdvanceDriver(change_after_reads=3)
        self.assertTrue(navigation.wait_for_manual_advance(driver))
        self.assertEqual(driver.taps, [], "no re-tap needed before the change")

    def test_forward_tap_label_includes_plain_next(self):
        # find_forward_button deliberately skips a plain "Next"; the stuck-
        # screen re-tap must include it — it IS the lesson page's button.
        import navigation
        nodes = qh.parse_screen(LESSON_SCREEN_XML)
        self.assertEqual(navigation.forward_tap_label(nodes), "Next")

    def test_gives_up_on_a_dead_screen_with_nothing_to_tap(self):
        # The Assigned-courses list: no forward button, nothing moving.
        # Waiting forever stranded the runner (2026-08-02) — after the
        # dead-screen limit it must give up so the app restart recovers.
        import navigation

        class DeadDriver:
            page_source = COURSES_LIST_XML

            def find_element(self, by, value):
                raise NoSuchElementException(value)

        self.assertFalse(navigation.wait_for_manual_advance(DeadDriver()))

    def test_gives_up_when_the_forward_button_never_advances_the_screen(self):
        # 2026-08-04, the client's phone: the Modules list answered every
        # Start tap with "Student Already Enrolled" and never moved. The
        # give-up branch was reachable only with NO forward button, so a
        # button that does nothing was re-tapped forever — and because
        # each tap prints, the supervisor's silence watchdog stayed quiet
        # too. Nothing anywhere ended the run.
        import navigation
        driver = NeverAdvancingDriver()
        self.assertFalse(navigation.wait_for_manual_advance(driver))
        self.assertTrue(driver.taps, "should re-tap a while before giving up")

    def test_push_through_stops_when_the_screen_changes_but_never_starts(self):
        # The same stranding one level up: the banner redrawing counts as
        # "screen changed", so the wait returns True, the attempts loop
        # runs again, and `while True` never exits. A screen that keeps
        # changing without ever reaching the questions must still strand.
        import navigation
        from selenium.common.exceptions import TimeoutException

        driver = NeverAdvancingDriver(read_limit=500)
        saved = (navigation.tap, navigation.wait_for_manual_advance,
                 navigation.open_next_in_sequence, navigation.tap_forward_button,
                 navigation.dismiss_popup)
        navigation.tap = lambda d, w, locator, label: (_ for _ in ()).throw(
            TimeoutException(label)
        )
        navigation.wait_for_manual_advance = lambda d, deadline=None: True
        navigation.open_next_in_sequence = lambda d: False
        # The Start button that taps "successfully" and changes nothing.
        navigation.tap_forward_button = lambda d: True
        navigation.dismiss_popup = lambda d: False
        try:
            with self.assertRaises(navigation.StuckScreenError):
                navigation.push_through_to_start(driver, attempts=2)
        finally:
            (navigation.tap, navigation.wait_for_manual_advance,
             navigation.open_next_in_sequence, navigation.tap_forward_button,
             navigation.dismiss_popup) = saved

    def test_push_through_raises_stuck_when_the_wait_gives_up(self):
        import navigation
        import locators as loc
        from selenium.common.exceptions import TimeoutException

        driver = TapDriver(COURSES_LIST_XML)
        saved = (navigation.tap, navigation.wait_for_manual_advance,
                 navigation.open_next_in_sequence)
        navigation.tap = lambda d, w, locator, label: (_ for _ in ()).throw(
            TimeoutException(label)
        )
        navigation.wait_for_manual_advance = lambda d: False
        navigation.open_next_in_sequence = lambda d: False
        try:
            with self.assertRaises(navigation.StuckScreenError):
                navigation.push_through_to_start(driver, attempts=1)
        finally:
            (navigation.tap, navigation.wait_for_manual_advance,
             navigation.open_next_in_sequence) = saved

    def test_push_through_reenters_course_from_assigned_courses_list(self):
        import navigation
        import config
        import locators as loc
        from selenium.common.exceptions import TimeoutException

        driver = TapDriver(COURSES_LIST_XML)
        clicks = []

        def course_click(by, value):
            el = FakeElement("course")

            def click():
                clicks.append(value)
                driver.xml = QUIZ_START_XML

            el.click = click
            return el

        driver.find_element = course_click

        def fake_tap(d, w, locator, label):
            if locator == loc.START_TEST and "Start" in d.xml:
                return
            raise TimeoutException(label)

        saved = (navigation.tap, navigation.open_next_in_sequence)
        navigation.tap = fake_tap
        navigation.open_next_in_sequence = lambda d: True
        try:
            self.assertTrue(navigation.push_through_to_start(driver, attempts=3))
        finally:
            navigation.tap, navigation.open_next_in_sequence = saved
        self.assertTrue(any("Ingliz tili" in c for c in clicks),
                        f"must tap the configured course: {clicks}")


class TestPushThroughWaitsWhenStuck(unittest.TestCase):
    def setUp(self):
        import navigation
        self._time = navigation.time
        navigation.time = FakeTime()

    def tearDown(self):
        import navigation
        navigation.time = self._time

    def test_push_through_waits_out_a_stuck_lesson_screen(self):
        # A stuck round must not give up: it waits for the screen to move
        # (manual tap), then pushes on to the Start button.
        import navigation
        import locators as loc
        from selenium.common.exceptions import TimeoutException

        driver = TapDriver(LESSON_SCREEN_XML)
        calls = {"waits": 0, "started": False}

        def fake_wait(d):
            calls["waits"] += 1
            d.xml = QUIZ_START_XML  # the human tapped Next on the phone
            return True

        def fake_tap(d, waiter, locator, label):
            if locator == loc.START_TEST and "Start" in d.xml:
                calls["started"] = True
                return
            raise TimeoutException(label)

        saved = (navigation.tap, navigation.wait_for_manual_advance)
        navigation.tap, navigation.wait_for_manual_advance = fake_tap, fake_wait
        try:
            result = navigation.push_through_to_start(driver, attempts=1)
        finally:
            navigation.tap, navigation.wait_for_manual_advance = saved

        self.assertTrue(result)
        self.assertEqual(calls["waits"], 1)
        self.assertTrue(calls["started"])


# The Dars 73 video lesson page as the 2026-08-04 run met it: the
# player's "Show player controls" toggle sits before "Next" in tree
# order, and each toggle redraws the overlay — so the blind tap-through
# read that churn as progress and never reached the Next button.
VIDEO_LESSON_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.widget.Button" bounds="[100,60][620,300]" clickable="true" content-desc="Show player controls"/>
  <node class="android.view.View" bounds="[42,350][678,420]" clickable="false" content-desc="Dars 73 That / This / Those / These"/>
  <node class="android.view.View" bounds="[42,440][678,700]" clickable="false" content-desc="Demonstrative pronouns are used to point to specific people, objects, or places."/>
  <node class="android.widget.Button" bounds="[26,1418][117,1502]" clickable="true" content-desc="null"/>
  <node class="android.widget.Button" bounds="[160,1418][678,1502]" clickable="true" content-desc="Next"/>
</hierarchy>"""


class VideoLessonDriver:
    """The video lesson page: Next advances, the player toggle only
    redraws the overlay (the tree changes, the page goes nowhere)."""

    current_package = "uz.ibrat.farzandlari"

    def __init__(self):
        self.xml = VIDEO_LESSON_XML
        self.clicks = []

    @property
    def page_source(self):
        return self.xml

    def find_element(self, by, value):
        el = FakeElement("btn")

        def click():
            self.clicks.append(value)
            if "player controls" in value:
                swap = (("Show player controls", "Hide player controls")
                        if "Show player controls" in self.xml
                        else ("Hide player controls", "Show player controls"))
                self.xml = self.xml.replace(*swap)
            elif "Next" in value:
                self.xml = QUIZ_START_XML

        el.click = click
        return el


class TestVideoLessonNavigation(unittest.TestCase):
    def setUp(self):
        import navigation
        self._time = navigation.time
        navigation.time = FakeTime()

    def tearDown(self):
        import navigation
        navigation.time = self._time

    def test_candidate_buttons_skip_the_player_controls_toggle(self):
        # Toggling the player's overlay never moves forward, and the
        # redraw it causes reads as "Screen changed" — so the tap-through
        # loop kept "progressing" without ever reaching Next (2026-08-04).
        import navigation
        nodes = qh.parse_screen(VIDEO_LESSON_XML)
        self.assertEqual(navigation.candidate_buttons(nodes), ["Next"])

    def test_tap_forward_button_ignores_plain_next_by_default(self):
        # The answer loop's unrecognized-screen path must keep leaving
        # the feedback sheet's exact "Next" to poll_once.
        import navigation
        driver = VideoLessonDriver()
        self.assertFalse(navigation.tap_forward_button(driver))
        self.assertEqual(driver.clicks, [])

    def test_tap_plain_next_taps_the_lesson_pages_bare_next(self):
        # No feedback sheet exists during navigation, so the lesson
        # page's exact "Next" is safe there — and IS the way forward.
        import navigation
        driver = VideoLessonDriver()
        self.assertTrue(navigation.tap_plain_next(driver))
        self.assertTrue(any("Next" in c for c in driver.clicks), driver.clicks)

    def test_tap_plain_next_leaves_the_survey_forward_alone(self):
        # The survey swallows blind forward taps until an option is
        # chosen — its "Next" belongs to dismiss_survey, and a swallowed
        # tap here would reset the push-through loop forever.
        import navigation
        driver = TapDriver(SURVEY_NEXT_XML)
        self.assertFalse(navigation.tap_plain_next(driver))

    def test_push_through_taps_next_and_never_the_player_toggle(self):
        import navigation
        import locators as loc
        from selenium.common.exceptions import TimeoutException

        driver = VideoLessonDriver()
        calls = {"started": False}

        def fake_tap(d, waiter, locator, label):
            if locator == loc.START_TEST and "Start" in d.xml:
                calls["started"] = True
                return
            raise TimeoutException(label)

        saved = navigation.tap
        navigation.tap = fake_tap
        try:
            self.assertTrue(navigation.push_through_to_start(driver, attempts=3))
        finally:
            navigation.tap = saved

        self.assertTrue(calls["started"])
        self.assertTrue(any("Next" in c for c in driver.clicks), driver.clicks)
        self.assertTrue(all("player controls" not in c for c in driver.clicks),
                        driver.clicks)


class TestPollOnceStuckLesson(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        self._time = watcher.time
        watcher.time = FakeTime()

    def tearDown(self):
        watcher.time = self._time
        os.chdir(self._cwd)

    def test_stuck_lesson_screen_is_retapped_until_it_moves(self):
        # A lesson page's exact "Next" classifies as an "other" sheet. When
        # the video hasn't loaded the first tap is swallowed — poll_once
        # must keep re-tapping instead of spinning silently forever.
        driver = XmlDriver(LESSON_SCREEN_XML)
        clicks = []

        class NextEl(FakeElement):
            def click(inner):
                clicks.append(1)
                if len(clicks) >= 2:  # video finished loading by now
                    driver.xml = QUIZ_START_XML

        driver.next_el = NextEl("Next")
        status = watcher.poll_once(driver, watcher.fresh_state(), [])

        self.assertEqual(status, "other")
        self.assertEqual(len(clicks), 2, "should re-tap Next after the retry interval")


# Trimmed from the real stuck_screen.xml captured 2026-08-02 14:46: the
# vocabulary screen ("Clever -" + 4 word chips). Its Continue button is in
# the tree but disabled until a chip is picked; tapping a chip alone
# submits nothing, so treating this as multiple choice waits forever for
# a feedback sheet and restarts the app in a loop.
WORD_TRANSLATION_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[42,189][678,245]" clickable="false" content-desc="Clever -"/>
  <node class="android.view.View" bounds="[236,326][337,368]" clickable="false" content-desc="Clever -"/>
  <node class="android.widget.Button" bounds="[58,1177][180,1261]" clickable="true" content-desc="Nohaq "/>
  <node class="android.widget.Button" bounds="[197,1177][375,1261]" clickable="true" content-desc="Qizg’anchiq"/>
  <node class="android.widget.Button" bounds="[393,1177][492,1261]" clickable="true" content-desc="Aqlli "/>
  <node class="android.widget.Button" bounds="[510,1177][662,1261]" clickable="true" content-desc="Mehribon"/>
  <node class="android.widget.Button" bounds="[42,1418][678,1502]" clickable="false" enabled="false" content-desc="Continue"/>
</hierarchy>"""

# The screen after a chip tap: the tapped chip's text ALSO appears in
# the answer area, so its desc is now on screen twice. A pre-sheet
# snapshot taken before the tap makes the sheet diff echo the tapped
# chip next to the revealed word (the live 2026-08-02 15:05 entry).
def word_translation_tapped_xml(chip="Nohaq "):
    echo = (f'<node class="android.view.View" bounds="[42,700][678,760]"'
            f' clickable="false" content-desc="{chip}"/>')
    return WORD_TRANSLATION_XML.replace("</hierarchy>", echo + "</hierarchy>")


def word_translation_feedback_xml(chip="Nohaq "):
    sheet = (
        '<node class="android.view.View" bounds="[42,1050][678,1110]"'
        ' clickable="false" content-desc="Incorrect Answer!"/>'
        '<node class="android.view.View" bounds="[42,1130][678,1180]"'
        ' clickable="false" content-desc="Aqlli"/>'
        '<node class="android.widget.Button" bounds="[42,1418][678,1502]"'
        ' clickable="true" content-desc="Next"/>'
    )
    return word_translation_tapped_xml(chip).replace("</hierarchy>", sheet + "</hierarchy>")


class WordTranslationFlowDriver:
    """The vocabulary screen as the runner meets it: a chip tap moves the
    chip into the answer area but submits nothing, Continue (dead until a
    chip is picked) brings the sheet, Next lands on an unrecognized
    screen that ends the test."""

    current_package = "uz.ibrat.farzandlari"

    def __init__(self):
        self.phase = "question"
        self.clicked = []
        self.chip_tapped = None
        self.taps = []
        self.back_presses = 0

    @property
    def page_source(self):
        if self.phase == "question":
            if self.chip_tapped:
                return word_translation_tapped_xml(self.chip_tapped)
            return WORD_TRANSLATION_XML
        if self.phase == "sheet":
            return word_translation_feedback_xml(self.chip_tapped)
        return UNKNOWN_AD_XML

    def find_element(self, by, value):
        if self.phase == "done":
            raise NoSuchElementException(value)
        el = FakeElement("btn")
        el.click = lambda: self._click(value)
        return el

    def _click(self, value):
        self.clicked.append(value)
        if "'Continue'" in value:
            if self.chip_tapped:  # disabled until a chip is picked
                self.phase = "sheet"
        elif "'Next'" in value:
            self.phase = "done"
        else:
            self.chip_tapped = value.split("'")[1]

    def tap(self, positions, duration=None):
        self.taps.append(positions[0])

    def back(self):
        self.back_presses += 1


class TestAnswerWordTranslation(unittest.TestCase):
    def test_taps_known_chip_then_continue(self):
        import main as main_mod
        driver = WordTranslationFlowDriver()
        state = {"descs": []}
        # known answer is the bare word; the chip label has a trailing space
        self.assertTrue(main_mod.answer_word_translation(
            driver, "Clever -",
            ["Nohaq ", "Qizg’anchiq", "Aqlli ", "Mehribon"],
            {"Clever -": "Aqlli"}, {}, state,
        ))
        self.assertIn("Aqlli ", driver.clicked[0])
        self.assertIn("Continue", driver.clicked[1])
        # the pre-sheet snapshot was refreshed AFTER the chip landed, so
        # the echo in the answer area is part of the baseline
        self.assertEqual(state["descs"].count("Aqlli "), 2)

    def test_unknown_taps_first_chip_and_rotates_on_repeat(self):
        import main as main_mod
        attempted = {}
        options = ["Nohaq ", "Qizg’anchiq", "Aqlli ", "Mehribon"]
        first = WordTranslationFlowDriver()
        main_mod.answer_word_translation(first, "Clever -", options, {}, attempted, {})
        second = WordTranslationFlowDriver()
        main_mod.answer_word_translation(second, "Clever -", options, {}, attempted, {})
        self.assertIn("Nohaq ", first.clicked[0])
        self.assertIn("Qizg’anchiq", second.clicked[0])
        self.assertTrue(any("Continue" in v for v in second.clicked))


class TestAccuracyThrottle(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        import main as main_mod
        import navigation
        self._main_time = main_mod.time
        self._stats = dict(main_mod.RUN_STATS)
        main_mod.time = FakeTime()
        self._nav_sleep = navigation.time.sleep
        navigation.time.sleep = lambda s: None

    def tearDown(self):
        import main as main_mod
        import navigation
        main_mod.time = self._main_time
        main_mod.RUN_STATS.clear()
        main_mod.RUN_STATS.update(self._stats)
        navigation.time.sleep = self._nav_sleep
        os.chdir(self._cwd)

    def test_answer_wrong_taps_a_non_answer_chip_and_continue(self):
        import main as main_mod
        driver = WordTranslationFlowDriver()
        state = {}
        self.assertTrue(main_mod.answer_wrong(
            driver, "word_translation", "Clever -",
            ["Nohaq ", "Qizg’anchiq", "Aqlli ", "Mehribon"],
            {"Clever -": "Aqlli"}, state,
        ))
        self.assertIn("Nohaq ", driver.clicked[0], "must not tap the known answer")
        self.assertTrue(any("Continue" in v for v in driver.clicked))

    def test_should_miss_keeps_accuracy_inside_the_band(self):
        import main as main_mod
        # floor not secured: answer honestly no matter how high the score is
        self.assertFalse(main_mod.should_miss(40, 0))
        self.assertFalse(main_mod.should_miss(774, 0))
        # 775 correct banked = 88% of 880 secured; 100% is over the cap: miss
        self.assertTrue(main_mod.should_miss(775, 0))
        # above the floor at 95%: another correct would nudge past the cap
        self.assertTrue(main_mod.should_miss(807, 42))
        # above the floor but under the cap: play it straight
        self.assertFalse(main_mod.should_miss(775, 70))

    def test_loop_misses_a_known_answer_when_accuracy_is_too_high(self):
        import main as main_mod
        watcher.save_results([{
            "question": "Clever -",
            "type": "word_translation",
            "result": "incorrect",
            "options": ["Nohaq ", "Qizg’anchiq", "Aqlli ", "Mehribon"],
            "correct_answer": ["Aqlli"],
        }])
        # floor secured and the run so far is perfect — the governor must
        # throw this one
        main_mod.RUN_STATS.update({"correct": 800, "incorrect": 0})
        driver = WordTranslationFlowDriver()
        with self.assertRaises(main_mod.StuckScreenError):
            main_mod.auto_answer_loop(driver)
        self.assertIn("Nohaq ", driver.clicked[0],
                      "known answer must be deliberately missed")
        self.assertTrue(any("Continue" in v for v in driver.clicked))

    def test_loop_answers_straight_when_accuracy_is_in_band(self):
        import main as main_mod
        watcher.save_results([{
            "question": "Clever -",
            "type": "word_translation",
            "result": "incorrect",
            "options": ["Nohaq ", "Qizg’anchiq", "Aqlli ", "Mehribon"],
            "correct_answer": ["Aqlli"],
        }])
        # floor secured, 94% so far — inside the band, the known answer is
        # played straight
        main_mod.RUN_STATS.update({"correct": 799, "incorrect": 51})
        driver = WordTranslationFlowDriver()
        with self.assertRaises(main_mod.StuckScreenError):
            main_mod.auto_answer_loop(driver)
        self.assertIn("Aqlli ", driver.clicked[0],
                      "in-band accuracy must not trigger a miss")


class TestPollOnceWordTranslation(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())

    def tearDown(self):
        os.chdir(self._cwd)

    def test_sheet_logs_word_translation_type_and_revealed_word(self):
        driver = XmlDriver(WORD_TRANSLATION_XML)
        state = watcher.fresh_state()
        results = []
        self.assertEqual(watcher.poll_once(driver, state, results), "question")
        self.assertEqual(state["question"], "Clever -")
        self.assertEqual(
            state["options"], ["Nohaq ", "Qizg’anchiq", "Aqlli ", "Mehribon"]
        )

        # watcher mode: a poll lands between the human's chip tap and
        # Continue, so the baseline holds the chip's echo already
        driver.xml = word_translation_tapped_xml("Nohaq ")
        self.assertEqual(watcher.poll_once(driver, state, results), "question")

        class NextEl(FakeElement):
            def click(inner):
                driver.xml = WORD_TRANSLATION_XML

        driver.next_el = NextEl("Next")
        driver.xml = word_translation_feedback_xml("Nohaq ")
        watcher.poll_once(driver, state, results)

        entry = results[0]
        self.assertEqual(entry["type"], "word_translation")
        self.assertEqual(entry["result"], "incorrect")
        self.assertEqual(entry["correct_answer"], ["Aqlli"])


class TestWordTranslationFlow(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        import main as main_mod
        import navigation
        self._main_time = main_mod.time
        main_mod.time = FakeTime()
        self._nav_sleep = navigation.time.sleep
        navigation.time.sleep = lambda s: None

    def tearDown(self):
        import main as main_mod
        import navigation
        main_mod.time = self._main_time
        navigation.time.sleep = self._nav_sleep
        os.chdir(self._cwd)

    def test_first_encounter_submits_with_continue_and_learns(self):
        import json
        import main as main_mod
        driver = WordTranslationFlowDriver()
        with self.assertRaises(main_mod.StuckScreenError):
            main_mod.auto_answer_loop(driver)

        chip, cont, nxt = driver.clicked[:3]
        self.assertIn("Nohaq ", chip)
        self.assertIn("Continue", cont)
        self.assertIn("Next", nxt)

        with open("results.json", encoding="utf-8") as f:
            results = json.load(f)
        entry = results[0]
        self.assertEqual(entry["type"], "word_translation")
        self.assertEqual(entry["result"], "incorrect")
        self.assertEqual(entry["correct_answer"], ["Aqlli"])
        # the wrong first guess still teaches the next encounter
        self.assertEqual(qh.build_answer_map(results), {"Clever -": "Aqlli"})

    def test_known_answer_taps_the_right_chip(self):
        import main as main_mod
        watcher.save_results([{
            "question": "Clever -",
            "type": "word_translation",
            "result": "incorrect",
            "options": ["Nohaq ", "Qizg’anchiq", "Aqlli ", "Mehribon"],
            "correct_answer": ["Aqlli"],
        }])
        driver = WordTranslationFlowDriver()
        with self.assertRaises(main_mod.StuckScreenError):
            main_mod.auto_answer_loop(driver)
        self.assertIn("Aqlli ", driver.clicked[0])
        self.assertTrue(any("Continue" in v for v in driver.clicked))


class TestConfigDeviceOverride(unittest.TestCase):
    def test_env_var_overrides_pinned_device(self):
        import importlib
        import config
        os.environ["IBRAT_DEVICE"] = "ZY22GTXB9R"
        try:
            importlib.reload(config)
            self.assertEqual(config.DEVICE_NAME, "ZY22GTXB9R")
        finally:
            del os.environ["IBRAT_DEVICE"]
            importlib.reload(config)
        self.assertEqual(config.DEVICE_NAME, "192.168.1.16:5555")


class QuietDriver:
    def quit(self):
        pass


class TestWorkerExitCodes(unittest.TestCase):
    def setUp(self):
        # main() now records its give-ups, so run somewhere disposable —
        # a test suite must not drop a problems.log into the project.
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        import main as main_mod
        self.m = main_mod
        self._saved = (main_mod.wake_device, main_mod.force_stop_app,
                       main_mod.connect_fresh_session, main_mod.navigate_to_test,
                       main_mod.answer_until_done, main_mod.APP_RELAUNCHES,
                       main_mod.time)
        main_mod.wake_device = lambda: True
        main_mod.force_stop_app = lambda: True
        main_mod.connect_fresh_session = lambda: QuietDriver()
        main_mod.APP_RELAUNCHES = 0
        main_mod.time = FakeTime()

    def tearDown(self):
        (self.m.wake_device, self.m.force_stop_app, self.m.connect_fresh_session,
         self.m.navigate_to_test, self.m.answer_until_done, self.m.APP_RELAUNCHES,
         self.m.time) = self._saved
        os.chdir(self._cwd)

    def test_course_completion_exits_zero(self):
        self.m.navigate_to_test = lambda *a: True
        self.m.answer_until_done = lambda d: None
        self.assertEqual(self.m.main(), 0)

    def test_giving_up_exits_one(self):
        from navigation import StuckScreenError

        def stuck(*a):
            raise StuckScreenError("stranded")
        self.m.navigate_to_test = stuck
        self.assertEqual(self.m.main(), 1)

    def test_navigation_failure_is_a_stuck_screen_not_success(self):
        # navigate_to_test returning False used to end the run with a
        # quiet success; under the supervisor exit 0 means "course done,
        # stop everything", so a failed navigation must be a give-up.
        self.m.navigate_to_test = lambda *a: False
        self.assertEqual(self.m.main(), 1)

    def test_ctrl_c_exits_130(self):
        def interrupted(*a):
            raise KeyboardInterrupt
        self.m.navigate_to_test = interrupted
        self.assertEqual(self.m.main(), 130)


ADB_HEADER = "List of devices attached\n"


class TestSupervisorPolicy(unittest.TestCase):
    PINNED = "192.168.1.16:5555"

    def test_pinned_wifi_target_wins_when_online(self):
        import supervisor
        out = ADB_HEADER + "ZY22GTXB9R\tdevice\n192.168.1.16:5555\tdevice\n\n"
        self.assertEqual(supervisor.pick_device(out, self.PINNED), self.PINNED)

    def test_usb_serial_used_when_wifi_is_gone(self):
        import supervisor
        out = ADB_HEADER + "ZY22GTXB9R\tdevice\n\n"
        self.assertEqual(supervisor.pick_device(out, self.PINNED), "ZY22GTXB9R")

    def test_usb_preferred_over_another_network_serial(self):
        import supervisor
        out = ADB_HEADER + "192.168.1.99:5555\tdevice\nZY22GTXB9R\tdevice\n\n"
        self.assertEqual(supervisor.pick_device(out, self.PINNED), "ZY22GTXB9R")

    def test_other_network_serial_used_as_last_resort(self):
        import supervisor
        out = ADB_HEADER + "192.168.1.99:5555\tdevice\n\n"
        self.assertEqual(supervisor.pick_device(out, self.PINNED),
                         "192.168.1.99:5555")

    def test_offline_and_unauthorized_devices_are_ignored(self):
        import supervisor
        out = ADB_HEADER + "192.168.1.16:5555\toffline\nZY22GTXB9R\tunauthorized\n\n"
        self.assertIsNone(supervisor.pick_device(out, self.PINNED))

    def test_no_devices_returns_none(self):
        import supervisor
        self.assertIsNone(supervisor.pick_device(ADB_HEADER + "\n", self.PINNED))

    def test_respawn_delay_ladder_caps_at_five_minutes(self):
        import supervisor
        delays = [supervisor.respawn_delay(s) for s in range(1, 9)]
        self.assertEqual(delays, [5, 15, 30, 60, 120, 300, 300, 300])
        self.assertEqual(supervisor.respawn_delay(0), 0)

    def test_streak_resets_after_a_long_lived_worker(self):
        import supervisor
        self.assertEqual(supervisor.update_streak(4, lived_seconds=3600), 1)
        self.assertEqual(supervisor.update_streak(1, lived_seconds=5), 2)

    def test_exit_codes_decide_respawn(self):
        import signal
        import supervisor
        self.assertFalse(supervisor.should_respawn(0))    # course done
        self.assertFalse(supervisor.should_respawn(130))  # worker saw Ctrl+C
        self.assertFalse(supervisor.should_respawn(-signal.SIGINT))
        self.assertTrue(supervisor.should_respawn(1))     # worker gave up
        self.assertTrue(supervisor.should_respawn(-9))    # killed while hung

    def test_silence_detection_uses_the_limit(self):
        import supervisor
        limit = supervisor.SILENCE_LIMIT
        self.assertFalse(supervisor.worker_is_hung(1000.0, 1000.0 + limit))
        self.assertTrue(supervisor.worker_is_hung(1000.0, 1000.0 + limit + 1))


# The home screen as a taller phone renders it (Samsung SM-A performance,
# 1080x2340): the "My collection" grid's second row — which holds the
# "2+6 Program Certificate" card the run must tap — sits below the fold.
# UiAutomator2 omits off-screen nodes from the tree, so the card is not
# merely hard to tap, it is absent: find_element raises instead of
# waiting, and the run dies on its very first navigation step.
HOME_UNSCROLLED_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,704][1080,1608]" clickable="true" content-desc="Jarayondagi topshiriq\nLaunchpad"/>
  <node class="android.view.View" bounds="[54,1736][402,1824]" clickable="false" content-desc="My collection"/>
  <node class="android.view.View" bounds="[0,1878][342,2243]" clickable="true" content-desc="Speaking club"/>
  <node class="android.view.View" bounds="[369,1878][711,2243]" clickable="true" content-desc="Library"/>
  <node class="android.view.View" bounds="[738,1878][1080,2243]" clickable="true" content-desc="Partner"/>
</hierarchy>"""

HOME_SCROLLED_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,348][342,713]" clickable="true" content-desc="Speaking club"/>
  <node class="android.view.View" bounds="[0,713][342,1078]" clickable="true" content-desc="2+6 Program Certificate"/>
  <node class="android.view.View" bounds="[369,713][711,1078]" clickable="true" content-desc="IELTS\nprep"/>
  <node class="android.view.View" bounds="[738,713][1080,1078]" clickable="true" content-desc="Mock exam"/>
</hierarchy>"""

CERT_SCREEN_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,713][1080,1078]" clickable="true" content-desc="Get certificate"/>
  <node class="android.view.View" bounds="[0,1200][1080,1400]" clickable="true" content-desc="Ingliz tili B2\nRustam Qoriyev"/>
</hierarchy>"""


class ScrollHomeDriver:
    """Home screen that only reveals the second collection row after a swipe."""

    def __init__(self, swipes_needed=1):
        self.page_source = HOME_UNSCROLLED_XML
        self._swipes_needed = swipes_needed
        self.swipes = 0

    def get_window_size(self):
        return {"width": 1080, "height": 2340}

    def find_element(self, by, value):
        # Only what the current tree holds can be found — off-screen
        # nodes are absent, exactly as UiAutomator2 reports them.
        match = re.search(r'\.description\("(.*)"\)', value, re.S)
        wanted = match.group(1) if match else value
        if wanted not in self.page_source:
            raise NoSuchElementException(f"{wanted} is not in the tree")
        return FakeElement(wanted)

    def find_elements(self, by, value):
        try:
            return [self.find_element(by, value)]
        except NoSuchElementException:
            return []

    def swipe(self, *a, **kw):
        self.swipes += 1
        if self.swipes >= self._swipes_needed:
            self.page_source = HOME_SCROLLED_XML


class TestRevealHomeCard(unittest.TestCase):
    """The home screen must be scrolled until the target card is real."""

    def setUp(self):
        import navigation
        self._sleep = navigation.time.sleep
        navigation.time.sleep = lambda s: None

    def tearDown(self):
        import navigation
        navigation.time.sleep = self._sleep

    def test_scrolls_until_the_card_enters_the_tree(self):
        import navigation
        driver = ScrollHomeDriver(swipes_needed=1)
        self.assertTrue(
            navigation.reveal_card(driver, "2+6 Program Certificate")
        )
        self.assertEqual(driver.swipes, 1)

    def test_no_scroll_when_the_card_is_already_visible(self):
        import navigation
        driver = ScrollHomeDriver(swipes_needed=1)
        driver.page_source = HOME_SCROLLED_XML
        self.assertTrue(
            navigation.reveal_card(driver, "2+6 Program Certificate")
        )
        self.assertEqual(driver.swipes, 0, "already visible — must not scroll")

    def test_keeps_scrolling_when_one_swipe_is_not_enough(self):
        import navigation
        driver = ScrollHomeDriver(swipes_needed=3)
        self.assertTrue(
            navigation.reveal_card(driver, "2+6 Program Certificate")
        )
        self.assertEqual(driver.swipes, 3)

    def test_gives_up_after_a_bounded_number_of_swipes(self):
        import navigation
        driver = ScrollHomeDriver(swipes_needed=99)
        self.assertFalse(
            navigation.reveal_card(driver, "2+6 Program Certificate")
        )
        self.assertLessEqual(driver.swipes, navigation.REVEAL_SWIPES)

    def test_navigate_to_test_reveals_the_card_before_tapping_it(self):
        # The regression itself: without the reveal step navigate_to_test
        # times out on the first tap on any phone whose home screen puts
        # the collection grid's second row below the fold.
        import navigation
        driver = ScrollHomeDriver(swipes_needed=1)
        tapped = []

        def fake_tap(d, w, locator, label, clear_rounds=3):
            # find_element is the point: a card below the fold is absent
            # from the tree, so this raises unless the reveal scrolled it in.
            d.find_element(*locator)
            tapped.append(label)
            if label == "Program Certificate":
                d.page_source = CERT_SCREEN_XML

        saved = (navigation.clear_launch_popups, navigation.tap,
                 navigation.open_next_in_sequence,
                 navigation.push_through_to_start)
        navigation.clear_launch_popups = lambda d, rounds=5: None
        navigation.tap = fake_tap
        navigation.open_next_in_sequence = lambda d: True
        navigation.push_through_to_start = lambda d, attempts=5: None

        class Waiter:
            def until(self, cond):
                return FakeElement("x")

        try:
            self.assertTrue(
                navigation.navigate_to_test(driver, Waiter(), Waiter())
            )
        finally:
            (navigation.clear_launch_popups, navigation.tap,
             navigation.open_next_in_sequence,
             navigation.push_through_to_start) = saved

        self.assertIn("Program Certificate", tapped)
        self.assertGreaterEqual(driver.swipes, 1,
                                "must scroll the card into the tree first")


# The quiz Start page on a 1080x2340 phone: the info card fills the
# screen and the Start button sits below the fold, so it is missing from
# the tree entirely — the runner finds no Start, no question and no
# forward button, and strands on a screen a human would just scroll.
QUIZ_START_UNSCROLLED_XML = """<hierarchy>
  <node class="android.widget.Button" bounds="[0,123][342,265]" clickable="true" content-desc="Go back"/>
  <node class="android.view.View" bounds="[0,700][1080,900]" clickable="false" content-desc="Quizzes"/>
  <node class="android.view.View" bounds="[0,900][1080,1000]" clickable="false" content-desc="Press the start button when you are ready"/>
  <node class="android.view.View" bounds="[70,1230][850,1450]" clickable="false" content-desc="Quiz difficulty\nMedium"/>
  <node class="android.view.View" bounds="[70,1450][850,1680]" clickable="false" content-desc="The number of quizzes\n24"/>
</hierarchy>"""

QUIZ_START_SCROLLED_XML = """<hierarchy>
  <node class="android.view.View" bounds="[70,600][850,830]" clickable="false" content-desc="Given time\nUnlimited"/>
  <node class="android.widget.Button" bounds="[70,1100][1010,1280]" clickable="true" content-desc="Start"/>
</hierarchy>"""


class QuizStartDriver:
    """A Start page whose button only exists once the page is scrolled."""

    def __init__(self):
        self.page_source = QUIZ_START_UNSCROLLED_XML
        self.swipes = 0
        self.clicked = []

    def get_window_size(self):
        return {"width": 1080, "height": 2340}

    def find_element(self, by, value):
        wanted = value
        match = re.search(r'@content-desc=\'(.*)\'', value, re.S)
        if match:
            wanted = match.group(1)
        if wanted not in self.page_source:
            raise NoSuchElementException(f"{wanted} is not in the tree")
        el = FakeElement(wanted)
        el.click = lambda: self.clicked.append(wanted)
        return el

    def find_elements(self, by, value):
        try:
            return [self.find_element(by, value)]
        except NoSuchElementException:
            return []

    def swipe(self, *a, **kw):
        self.swipes += 1
        self.page_source = QUIZ_START_SCROLLED_XML


class FastWait:
    """WebDriverWait that polls once — keeps the timeout out of the test."""

    def __init__(self, driver, timeout=0, *a, **kw):
        self._driver = driver

    def until(self, condition):
        from selenium.common.exceptions import TimeoutException
        try:
            found = condition(self._driver)
        except NoSuchElementException:
            raise TimeoutException("not on screen")
        if not found:
            raise TimeoutException("not on screen")
        return found


class TestBelowTheFoldStartButton(unittest.TestCase):
    def setUp(self):
        import navigation
        self.nav = navigation
        self._saved = (navigation.WebDriverWait, navigation.time)
        navigation.WebDriverWait = FastWait
        navigation.time = FakeTime()

    def tearDown(self):
        self.nav.WebDriverWait, self.nav.time = self._saved

    def test_scrolls_to_reach_a_start_button_below_the_fold(self):
        driver = QuizStartDriver()
        self.assertTrue(self.nav.push_through_to_start(driver))
        self.assertGreaterEqual(driver.swipes, 1,
                                "must scroll to bring Start into the tree")
        self.assertIn("Start", driver.clicked)


class QuizStartInLoopDriver(TapDriver):
    """The quiz Start page as the answering loop meets it, Start below the fold.

    Trimmed from the real stuck_screen.xml captured 2026-08-03 on a
    1080x2340 phone: finishing a quiz lands on the next quiz's Start
    page, whose button is off-screen and therefore absent from the tree.
    """

    XML = """<hierarchy>
  <node class="android.widget.ImageView" bounds="[0,96][189,285]" clickable="true" content-desc="Back"/>
  <node class="android.view.View" bounds="[189,147][400,234]" clickable="false" content-desc="Go back"/>
  <node class="android.view.View" bounds="[393,825][687,933]" clickable="false" content-desc="Quizzes"/>
  <node class="android.view.View" bounds="[176,960][905,1122]" clickable="false" content-desc="Press the start button when you are ready"/>
  <node class="android.view.View" bounds="[300,1287][692,1375]" clickable="false" content-desc="Quiz difficulty"/>
  <node class="android.view.View" bounds="[300,1760][940,1848]" clickable="false" content-desc="The number of quizzes"/>
</hierarchy>"""

    SCROLLED_XML = """<hierarchy>
  <node class="android.view.View" bounds="[300,600][603,700]" clickable="false" content-desc="Given time"/>
  <node class="android.widget.Button" bounds="[70,1100][1010,1280]" clickable="true" content-desc="Start"/>
</hierarchy>"""

    def __init__(self):
        super().__init__(self.XML)
        self.swipes = 0
        self.clicked = []

    def get_window_size(self):
        return {"width": 1080, "height": 2340}

    def find_element(self, by, value):
        match = re.search(r"@content-desc='(.*)'", value, re.S)
        wanted = match.group(1) if match else value
        if wanted not in self.xml:
            raise NoSuchElementException(f"{wanted} is not in the tree")
        el = FakeElement(wanted)
        el.click = lambda: self._click(wanted)
        return el

    def _click(self, label):
        self.clicked.append(label)
        if label == "Start":
            # The quiz opens for real here; the loop has shown what this
            # test came to see, so end it rather than model a whole quiz.
            raise StopIteration("Start tapped")

    def swipe(self, *a, **kw):
        self.swipes += 1
        self.xml = self.SCROLLED_XML


class TestAnsweringLoopScrollsToStart(unittest.TestCase):
    """The answering loop meets below-the-fold buttons too, not just navigation.

    Between quizzes the loop lands on the next Start page itself. Without
    a scroll it sees nothing to tap, saves a stuck screen and burns a
    whole app restart — once per quiz, all run long.
    """

    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        import main as main_mod
        import navigation
        self.m = main_mod
        self.nav = navigation
        self._saved = (main_mod.time, navigation.time)
        main_mod.time = FakeTime()
        navigation.time = FakeTime()

    def tearDown(self):
        self.m.time, self.nav.time = self._saved
        os.chdir(self._cwd)

    def test_scrolls_to_the_start_button_instead_of_restarting_the_app(self):
        driver = QuizStartInLoopDriver()
        try:
            self.m.auto_answer_loop(driver)
        except self.m.StuckScreenError:
            self.fail("stranded on a Start page it only had to scroll")
        except StopIteration:
            pass
        self.assertGreaterEqual(driver.swipes, 1, "must scroll for the button")
        self.assertIn("Start", driver.clicked)


class SlowQuizStartDriver(TapDriver):
    """Start page below the fold, then a quiz that takes a moment to load.

    The revealing scroll itself costs seconds, so by the time Start is
    tapped the idle timer has already run down. If tapping a revealed
    button does not count as progress, the very next poll restarts the
    app — right after the tap that was about to work.
    """

    START_HIDDEN = """<hierarchy>
  <node class="android.view.View" bounds="[393,825][687,933]" clickable="false" content-desc="Quizzes"/>
  <node class="android.view.View" bounds="[300,1287][692,1375]" clickable="false" content-desc="Quiz difficulty"/>
</hierarchy>"""

    START_VISIBLE = """<hierarchy>
  <node class="android.view.View" bounds="[300,600][603,700]" clickable="false" content-desc="Given time"/>
  <node class="android.widget.Button" bounds="[70,1100][1010,1280]" clickable="true" content-desc="Start"/>
</hierarchy>"""

    LOADING = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][1080,2340]" clickable="false" content-desc="Yuklanmoqda"/>
</hierarchy>"""

    QUESTION = """<hierarchy>
  <node class="android.view.View" bounds="[81,312][999,528]" clickable="false" content-desc="They live in ___ small village."/>
  <node class="android.widget.Button" bounds="[84,1049][996,1211]" clickable="true" content-desc="a"/>
  <node class="android.widget.Button" bounds="[84,1272][996,1434]" clickable="true" content-desc="the"/>
  <node class="android.widget.Button" bounds="[84,1495][996,1657]" clickable="true" content-desc="an"/>
</hierarchy>"""

    def __init__(self, swipes_to_reveal=5, loading_polls=10):
        super().__init__(self.START_HIDDEN)
        self._to_reveal = swipes_to_reveal
        self._loading_left = loading_polls
        self.swipes = 0
        self.clicked = []
        self.started = False

    @property
    def page_source(self):
        if not self.started:
            return self.xml
        if self._loading_left > 0:
            self._loading_left -= 1
            return self.LOADING
        return self.QUESTION

    def get_window_size(self):
        return {"width": 1080, "height": 2340}

    def find_element(self, by, value):
        match = re.search(r"@content-desc='(.*)'", value, re.S)
        wanted = match.group(1) if match else value
        if wanted not in self.page_source:
            raise NoSuchElementException(f"{wanted} is not in the tree")
        el = FakeElement(wanted)
        el.click = lambda: self._click(wanted)
        return el

    def _click(self, label):
        self.clicked.append(label)
        if label == "Start":
            self.started = True
        else:
            # An option on the loaded question: the loop got where it
            # needed to go, so end the test here.
            raise StopIteration("question reached")

    def swipe(self, *a, **kw):
        self.swipes += 1
        if self.swipes >= self._to_reveal:
            self.xml = self.START_VISIBLE


class TestRevealedTapCountsAsProgress(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        import main as main_mod
        import navigation
        self.m, self.nav = main_mod, navigation
        self._saved = (main_mod.time, navigation.time)
        # One clock: the scrolling in navigation must burn the same idle
        # budget that main checks, or the race under test cannot happen.
        clock = FakeTime()
        main_mod.time = clock
        navigation.time = clock

    def tearDown(self):
        self.m.time, self.nav.time = self._saved
        os.chdir(self._cwd)

    def test_tapping_a_revealed_button_refreshes_the_idle_timer(self):
        driver = SlowQuizStartDriver()
        try:
            self.m.auto_answer_loop(driver)
        except self.m.StuckScreenError:
            self.fail("restarted the app right after tapping Start")
        except StopIteration:
            pass
        self.assertIn("Start", driver.clicked)

    def test_scroll_hunt_is_not_repeated_on_every_poll(self):
        # Scrolling costs a second per swipe. Repeating the hunt on each
        # poll of a genuinely dead screen would burn the idle budget that
        # triggers the recovering restart.
        driver = SlowQuizStartDriver(swipes_to_reveal=99)
        with self.assertRaises(self.m.StuckScreenError):
            self.m.auto_answer_loop(driver)
        self.assertLessEqual(driver.swipes, self.nav.REVEAL_SWIPES,
                             "must hunt once per stuck episode, not per poll")


class VanishingStuckDriver(TapDriver):
    """A screen the runner cannot read, which resolves just after the save.

    The real 2026-08-03 case: the runner stalls on something between
    quizzes, and by the time it writes the tree the app has moved on to
    a question. Saving a fresh read captures the question — evidence of
    the screen that actually stalled is lost, which is the whole point
    of the file.
    """

    UNREADABLE = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][1080,2340]" clickable="false" content-desc="Yuklanmoqda"/>
</hierarchy>"""

    QUESTION = """<hierarchy>
  <node class="android.view.View" bounds="[81,312][999,528]" clickable="false" content-desc="Is ___ your pen on the table?"/>
  <node class="android.widget.Button" bounds="[84,1049][996,1211]" clickable="true" content-desc="this"/>
  <node class="android.widget.Button" bounds="[84,1272][996,1434]" clickable="true" content-desc="these"/>
</hierarchy>"""

    def __init__(self):
        super().__init__(self.QUESTION)

    def find_element(self, by, value):
        raise NoSuchElementException(value)


class TestStuckScreenCapturesTheStalledScreen(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        import main as main_mod
        self.m = main_mod
        self._time = main_mod.time
        main_mod.time = FakeTime()

    def tearDown(self):
        self.m.time = self._time
        os.chdir(self._cwd)

    def test_saves_the_observed_screen_not_a_fresh_read(self):
        driver = VanishingStuckDriver()
        self.m.save_stuck_screen(driver, VanishingStuckDriver.UNREADABLE)
        with open(self.m.STUCK_SCREEN_FILE, encoding="utf-8") as f:
            saved = f.read()
        self.assertIn("Yuklanmoqda", saved,
                      "must save the screen the runner could not read")
        self.assertNotIn("Is ___ your pen", saved,
                         "a fresh read captures the wrong screen entirely")

    def test_falls_back_to_the_live_screen_when_none_was_captured(self):
        driver = VanishingStuckDriver()
        self.m.save_stuck_screen(driver)
        with open(self.m.STUCK_SCREEN_FILE, encoding="utf-8") as f:
            self.assertIn("Is ___ your pen", f.read())


class TestWakeDevice(unittest.TestCase):
    """The unlock swipe must only ever land on a lock screen."""

    def setUp(self):
        import main as main_mod
        self.m = main_mod
        self._saved = (main_mod.adb_shell, main_mod.time)
        main_mod.time = FakeTime()
        self.commands = []
        main_mod.adb_shell = lambda *a: self.commands.append(a) or True

    def tearDown(self):
        self.m.adb_shell, self.m.time = self._saved
        if hasattr(self.m, "adb_capture"):
            self.m.adb_capture = self._capture_saved

    def _stub_capture(self, fn):
        self._capture_saved = getattr(self.m, "adb_capture", None)
        self.m.adb_capture = fn

    def swipes(self):
        return [c for c in self.commands if c[:2] == ("input", "swipe")]

    def test_no_unlock_swipe_when_the_phone_is_already_unlocked(self):
        # The swipe is blind: on an unlocked phone it lands on the
        # launcher and drags the app drawer open. That is what the client
        # watched their phone do while the app kept failing to start —
        # every restart added another swipe.
        self._stub_capture(lambda *a: "mShowingDream=false mDreamingLockscreen=false")
        self.assertTrue(self.m.wake_device())
        self.assertEqual(self.swipes(), [], "must not swipe an unlocked phone")

    def test_swipes_when_the_keyguard_is_still_up(self):
        self._stub_capture(lambda *a: "mDreamingLockscreen=true")
        self.assertTrue(self.m.wake_device())
        self.assertEqual(len(self.swipes()), 1)

    def test_unlock_swipe_is_scaled_to_the_screen(self):
        # Coordinates were hardcoded for a 720x1600 phone; on a 1080x2340
        # screen that swipe starts halfway up and can miss the gesture.
        def capture(*args):
            if args[0] == "wm":
                return "Physical size: 1080x2340\n"
            return "mDreamingLockscreen=true"

        self._stub_capture(capture)
        self.m.wake_device()
        _, _, x1, y1, x2, y2, _ = self.swipes()[0]
        self.assertEqual(int(x1), 540, "swipe must run up the middle")
        self.assertGreater(int(y1), 1600, "must start low on a tall screen")
        self.assertLess(int(y2), int(y1), "must swipe upward")

    def test_falls_back_to_a_swipe_when_the_state_cannot_be_read(self):
        # No reading of the lock state (adb refused the dumpsys) — the
        # old blind behaviour is the safe default: better a stray swipe
        # than a phone that stays locked for the whole run.
        self._stub_capture(lambda *a: "")
        self.assertTrue(self.m.wake_device())
        self.assertEqual(len(self.swipes()), 1)

    def test_reports_failure_when_adb_is_unreachable(self):
        self._stub_capture(lambda *a: "")
        self.m.adb_shell = lambda *a: False
        self.assertFalse(self.m.wake_device())


# A lesson page as the client photographed it on 2026-08-04: a video, the
# lesson text, and a plain "Next" button sitting right there. Nothing here
# needs scrolling — and scrolling is actively harmful, because it pushes
# the button out of the tree and strands the runner on a page it could
# have simply tapped.
LESSON_PAGE_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,140][1080,610]" clickable="false" content-desc="That/This/Those/These\nIBRAT FARZANDLARI"/>
  <node class="android.view.View" bounds="[180,660][1000,800]" clickable="false" content-desc="Dars 73 That / This / Those / These"/>
  <node class="android.view.View" bounds="[180,840][1000,1600]" clickable="false" content-desc="Demonstrative pronouns are used to point to specific people, objects, or places."/>
  <node class="android.widget.Button" bounds="[280,1690][1000,1790]" clickable="true" content-desc="Next"/>
</hierarchy>"""


class LessonPageDriver:
    """A lesson page whose Next button scrolls away if the runner swipes."""

    current_package = "uz.ibrat.farzandlari"

    def __init__(self):
        self.page_source = LESSON_PAGE_XML
        self.swipes = 0

    def get_window_size(self):
        return {"width": 1080, "height": 2340}

    def find_element(self, by, value):
        match = re.search(r"@content-desc='(.*)'", value, re.S)
        wanted = match.group(1) if match else value
        if wanted not in self.page_source:
            raise NoSuchElementException(f"{wanted} is not in the tree")
        return FakeElement(wanted)

    def find_elements(self, by, value):
        try:
            return [self.find_element(by, value)]
        except NoSuchElementException:
            return []

    def swipe(self, *a, **kw):
        self.swipes += 1
        # Scrolling carries the button off the bottom of the page.
        self.page_source = LESSON_PAGE_XML.replace(
            '<node class="android.widget.Button" bounds="[280,1690][1000,1790]"'
            ' clickable="true" content-desc="Next"/>', "")


class TestNoScrollingWhenNextIsRightThere(unittest.TestCase):
    """find_forward_button hides a plain "Next" — poll_once owns that case.

    The scroll hunt must not read that silence as "nothing to move
    forward with", or every lesson page gets swiped at until its button
    is gone.
    """

    def setUp(self):
        import navigation
        self.nav = navigation
        self._time = navigation.time
        navigation.time = FakeTime()

    def tearDown(self):
        self.nav.time = self._time

    def test_a_visible_next_button_stops_the_hunt(self):
        driver = LessonPageDriver()
        self.nav.reveal_forward_button(driver)
        self.assertEqual(driver.swipes, 0,
                         "Next was on screen — nothing to hunt for")

    def test_the_button_survives_the_hunt(self):
        driver = LessonPageDriver()
        self.nav.reveal_forward_button(driver)
        self.assertIn("Next", driver.page_source,
                      "scrolling swept away the button that was already there")

    def test_a_screen_with_nothing_forward_is_still_scrolled(self):
        # The below-the-fold Start page must keep working.
        driver = QuizStartDriver()
        self.assertTrue(self.nav.reveal_forward_button(driver))
        self.assertGreaterEqual(driver.swipes, 1)


class TestProblemLogCountIsNotStale(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        import main as main_mod
        self.m = main_mod
        self._saved = (main_mod.wake_device, main_mod.force_stop_app,
                       main_mod.connect_fresh_session, main_mod.navigate_to_test,
                       main_mod.APP_RELAUNCHES, main_mod.time,
                       dict(main_mod.CONTEXT))
        main_mod.wake_device = lambda: True
        main_mod.force_stop_app = lambda: True
        main_mod.connect_fresh_session = lambda: QuietDriver()
        main_mod.APP_RELAUNCHES = 0
        main_mod.time = FakeTime()

    def tearDown(self):
        (self.m.wake_device, self.m.force_stop_app, self.m.connect_fresh_session,
         self.m.navigate_to_test, self.m.APP_RELAUNCHES, self.m.time,
         context) = self._saved
        self.m.CONTEXT.clear()
        self.m.CONTEXT.update(context)
        os.chdir(self._cwd)

    def test_a_new_attempt_does_not_inherit_the_last_ones_count(self):
        # Reporting "answered: 10" for an attempt that answered nothing
        # sends whoever reads the log looking in the wrong place.
        self.m.CONTEXT.update(question="[10] multiple_choice: leftover",
                              answered=10)

        def stuck(*a):
            raise self.m.StuckScreenError("unrecognized screen for over 10s")

        self.m.navigate_to_test = stuck
        self.m.main()
        with open(self.m.PROBLEM_LOG, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("answered: 0 question(s)", text)
        self.assertNotIn("leftover", text)


# The quiz Start page a moment after Start was tapped: the button is
# gone because the page is on its way out, and the quiz behind it has
# not arrived yet. Captured on a 720x1600 Moto on 2026-08-04, with 400px
# of empty screen below the card — nothing to scroll to, nothing to tap,
# and the app perfectly healthy. Left alone for 50s it opened the quiz;
# the runner killed it at 10s, every single quiz.
QUIZ_LOADING_XML = """<hierarchy>
  <node class="android.widget.ImageView" bounds="[0,77][98,175]" clickable="true" content-desc="Back"/>
  <node class="android.view.View" bounds="[98,103][207,149]" clickable="false" content-desc="Go back"/>
  <node class="android.view.View" bounds="[284,532][436,588]" clickable="false" content-desc="Quizzes"/>
  <node class="android.view.View" bounds="[93,602][627,644]" clickable="false" content-desc="Press the start button when you are ready"/>
  <node class="android.view.View" bounds="[156,730][359,776]" clickable="false" content-desc="Quiz difficulty"/>
  <node class="android.view.View" bounds="[156,975][487,1021]" clickable="false" content-desc="The number of quizzes"/>
</hierarchy>"""

DEAD_UNKNOWN_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="false" content-desc="Reklama"/>
</hierarchy>"""


class SlowLoadingQuizDriver(TapDriver):
    """The Start page hangs about for a while, then the quiz appears."""

    QUESTION = """<hierarchy>
  <node class="android.view.View" bounds="[40,200][680,300]" clickable="false" content-desc="I need |_| advice on this matter."/>
  <node class="android.widget.Button" bounds="[40,430][680,520]" clickable="true" content-desc="---"/>
  <node class="android.widget.Button" bounds="[40,545][680,635]" clickable="true" content-desc="an"/>
  <node class="android.widget.Button" bounds="[40,660][680,750]" clickable="true" content-desc="a"/>
</hierarchy>"""

    def __init__(self, clock, loads_after):
        super().__init__(QUIZ_LOADING_XML)
        self._clock = clock
        self._ready_at = clock.time() + loads_after

    @property
    def page_source(self):
        return self.QUESTION if self._clock.time() >= self._ready_at else self.xml

    def find_element(self, by, value):
        raise NoSuchElementException(value)


class TestQuizLoadIsWaitedOut(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        import main as main_mod
        import navigation
        self.m, self.nav = main_mod, navigation
        self._saved = (main_mod.time, navigation.time)
        self.clock = FakeTime()
        main_mod.time = self.clock
        navigation.time = self.clock

    def tearDown(self):
        self.m.time, self.nav.time = self._saved
        os.chdir(self._cwd)

    def test_a_loading_quiz_is_given_time_instead_of_a_restart(self):
        driver = SlowLoadingQuizDriver(self.clock, loads_after=30)
        started = self.clock.time()
        try:
            self.m.auto_answer_loop(driver)
        except self.m.StuckScreenError:
            self.fail("restarted the app while the quiz was still opening")
        except (StopIteration, NoSuchElementException):
            pass
        self.assertGreaterEqual(self.clock.time() - started, 30,
                                "must actually have waited out the load")

    def test_a_quiz_that_never_loads_still_gives_up(self):
        driver = SlowLoadingQuizDriver(self.clock, loads_after=10_000)
        with self.assertRaises(self.m.StuckScreenError):
            self.m.auto_answer_loop(driver)

    def test_an_ordinary_unknown_screen_is_not_given_the_long_wait(self):
        # Only the start/loading page earns patience; a strange ad screen
        # must still recover at the usual speed.
        driver = TapDriver(DEAD_UNKNOWN_XML)
        started = self.clock.time()
        with self.assertRaises(self.m.StuckScreenError):
            self.m.auto_answer_loop(driver)
        self.assertLess(self.clock.time() - started, self.m.LOADING_IDLE_LIMIT,
                        "an unknown screen must not wait for a quiz load")


class TestProblemLog(unittest.TestCase):
    """One appended block per incident, readable by whoever gets it emailed."""

    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        import main as main_mod
        self.m = main_mod
        self._context = dict(main_mod.CONTEXT)

    def tearDown(self):
        self.m.CONTEXT.clear()
        self.m.CONTEXT.update(self._context)
        os.chdir(self._cwd)

    def read(self):
        with open(self.m.PROBLEM_LOG, encoding="utf-8") as f:
            return f.read()

    def test_records_the_reason_and_where_the_run_was(self):
        self.m.CONTEXT.update(phase="answering questions",
                              question="Is ___ your pen on the table?",
                              answered=7)
        self.m.log_problem("app restarted", "app left the foreground (launcher)")
        text = self.read()
        self.assertIn("app restarted", text)
        self.assertIn("left the foreground", text)
        self.assertIn("answering questions", text)
        self.assertIn("Is ___ your pen on the table?", text)
        self.assertIn("7", text)

    def test_appends_so_a_whole_run_is_kept(self):
        self.m.log_problem("app restarted", "first failure")
        self.m.log_problem("gave up", "second failure")
        text = self.read()
        self.assertIn("first failure", text)
        self.assertIn("second failure", text)

    def test_points_at_the_saved_screen_when_there_is_one(self):
        self.m.log_problem("app restarted", "unrecognized screen",
                           screen="stuck_screen_20260803_202400.xml")
        self.assertIn("stuck_screen_20260803_202400.xml", self.read())

    def test_never_breaks_the_run_when_the_log_cannot_be_written(self):
        # Diagnostics must never be the thing that kills a run.
        os.chdir(self._cwd)
        self.m.PROBLEM_LOG = "/nonexistent-dir/problems.log"
        try:
            self.m.log_problem("app restarted", "some reason")
        finally:
            self.m.PROBLEM_LOG = "problems.log"


class TestStuckScreensAreKept(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        import main as main_mod
        self.m = main_mod

    def tearDown(self):
        os.chdir(self._cwd)

    def test_each_stranding_keeps_its_own_tree(self):
        # A run with several strandings used to leave only the last one,
        # so the screen that started the trouble was already gone.
        first = self.m.save_stuck_screen(None, "<hierarchy>first</hierarchy>")
        second = self.m.save_stuck_screen(None, "<hierarchy>second</hierarchy>")
        self.assertNotEqual(first, second)
        with open(first, encoding="utf-8") as f:
            self.assertIn("first", f.read())
        with open(second, encoding="utf-8") as f:
            self.assertIn("second", f.read())

    def test_latest_stranding_is_also_at_the_documented_path(self):
        self.m.save_stuck_screen(None, "<hierarchy>newest</hierarchy>")
        with open(self.m.STUCK_SCREEN_FILE, encoding="utf-8") as f:
            self.assertIn("newest", f.read())


class TestRunRecordsItsProblems(unittest.TestCase):
    """The restart messages must reach the file, not only the console."""

    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        import main as main_mod
        self.m = main_mod
        self._saved = (main_mod.wake_device, main_mod.force_stop_app,
                       main_mod.connect_fresh_session, main_mod.navigate_to_test,
                       main_mod.answer_until_done, main_mod.APP_RELAUNCHES,
                       main_mod.time, dict(main_mod.CONTEXT))
        main_mod.wake_device = lambda: True
        main_mod.force_stop_app = lambda: True
        main_mod.connect_fresh_session = lambda: QuietDriver()
        main_mod.answer_until_done = lambda d: None
        main_mod.APP_RELAUNCHES = 1
        main_mod.time = FakeTime()

    def tearDown(self):
        (self.m.wake_device, self.m.force_stop_app, self.m.connect_fresh_session,
         self.m.navigate_to_test, self.m.answer_until_done, self.m.APP_RELAUNCHES,
         self.m.time, context) = self._saved
        self.m.CONTEXT.clear()
        self.m.CONTEXT.update(context)
        os.chdir(self._cwd)

    def read(self):
        with open(self.m.PROBLEM_LOG, encoding="utf-8") as f:
            return f.read()

    def test_a_restart_and_the_give_up_are_both_recorded(self):
        def stuck(*a):
            raise self.m.StuckScreenError("unrecognized screen for over 10s")

        self.m.navigate_to_test = stuck
        self.assertEqual(self.m.main(), 1)
        text = self.read()
        self.assertIn("unrecognized screen for over 10s", text)
        self.assertIn("gave up", text)

    def test_losing_the_app_names_the_package_that_took_over(self):
        def lost(*a):
            raise self.m.AppLostError("com.sec.android.app.launcher")

        self.m.navigate_to_test = lost
        self.m.main()
        self.assertIn("com.sec.android.app.launcher", self.read())

    def test_a_clean_finish_writes_no_problem_log(self):
        self.m.navigate_to_test = lambda *a: True
        self.assertEqual(self.m.main(), 0)
        self.assertFalse(os.path.exists(self.m.PROBLEM_LOG))


class TestRunLog(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        import supervisor
        self.s = supervisor
        self._path = supervisor.RUN_LOG_PATH
        supervisor.RUN_LOG_PATH = os.path.join(os.getcwd(), "run.log")

    def tearDown(self):
        self.s.RUN_LOG_PATH = self._path
        os.chdir(self._cwd)

    def test_worker_output_is_kept_on_disk_not_just_printed(self):
        # The console scrolls away and the window gets closed; without a
        # file there is nothing for the user to send back.
        class Child:
            stdout = ["Tapped: Start\n", "The app is stuck — restarting it...\n"]

        self.s._pump(Child(), [0.0])
        with open(self.s.RUN_LOG_PATH, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("Tapped: Start", text)
        self.assertIn("restarting it", text)


# The IBRAT PRO paywall (stuck_screen_20260804_180610): the subscriber
# pill renders split into one-glyph nodes, the close X is the unlabeled
# top-left ImageView, and every CTA is a labeled clickable View.
PAYWALL_SPLIT_PILL_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.widget.ImageView" bounds="[42,100][112,170]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[446,117][459,152]" clickable="false" content-desc="3"/>
  <node class="android.view.View" bounds="[459,117][472,152]" clickable="false" content-desc="5"/>
  <node class="android.view.View" bounds="[516,117][529,152]" clickable="false" content-desc="+"/>
  <node class="android.view.View" bounds="[529,121][657,149]" clickable="false" content-desc=" subscribers"/>
  <node class="android.view.View" bounds="[28,656][692,784]" clickable="true" content-desc="Yillik\n26 500 uzs/oy\n318 000 soums"/>
  <node class="android.view.View" bounds="[28,1411][692,1509]" clickable="true" content-desc="Subscribe • 318 000 soums"/>
</hierarchy>"""

# The 30%-discount countdown interstitial (stuck_screen_20260804_183453):
# a full-screen close surface the app itself labels "Dismiss", a glyph-
# split countdown, and a "use the discount" CTA that must stay fenced.
DISCOUNT_COUNTDOWN_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc="Dismiss"/>
  <node class="android.widget.ImageView" bounds="[0,77][126,175]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[150,800][570,845]" clickable="false" content-desc="Masus taklif - 30% chegirma"/>
  <node class="android.widget.Button" bounds="[42,1404][678,1488]" clickable="true" content-desc="Chegirmadan foydalanish"/>
</hierarchy>"""

# The payment bottom sheet (stuck_screen_20260804_182349): a Flutter
# sheet over a Scrim; its Button is a real payment CTA and its unlabeled
# ImageView is a payment logo (mid-screen, not a top-left X).
PAYMENT_SHEET_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[0,0][720,623]" clickable="true" content-desc="Scrim"/>
  <node class="android.widget.ImageView" bounds="[0,623][126,749]" clickable="true" content-desc=""/>
  <node class="android.widget.Button" bounds="[42,1397][678,1495]" clickable="true" content-desc="Toʻlovni amalga oshirish"/>
</hierarchy>"""

# The language-course cross-sell bottom sheet (stuck_screen_20260804_184527,
# captured while the "on:" context still read a matching question — the
# runner had no dismisser for this sheet, so it idled past the 10s limit
# and the app restart that followed looked, on the phone, like the quiz
# had frozen). Same Scrim-backed shape as the payment sheet.
LANGUAGE_CROSS_SELL_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][720,1600]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[0,0][720,623]" clickable="true" content-desc="Scrim"/>
  <node class="android.widget.ImageView" bounds="[0,623][126,749]" clickable="true" content-desc=""/>
  <node class="android.widget.Button" bounds="[42,1397][678,1495]" clickable="true" content-desc="Men til oʻrganmoqchiman"/>
</hierarchy>"""

# The Play Store "Update available" nag (client photo, 2026-08-06 — a
# native system dialog, never captured as an XML dump, so this is a
# best-effort reconstruction from the screenshot: title, body text, an
# unlabeled close icon, and "Learn more" / "Update" buttons that both
# lead out of the app).
PLAY_STORE_UPDATE_XML = """<hierarchy>
  <node class="android.widget.TextView" bounds="[64,300][500,360]" clickable="false" content-desc="Update available"/>
  <node class="android.widget.TextView" bounds="[64,420][1200,460]" clickable="false" content-desc="To use this app, download the latest version."/>
  <node class="android.widget.ImageView" bounds="[1320,340][1390,410]" clickable="true" content-desc=""/>
  <node class="android.widget.Button" bounds="[520,980][940,1060]" clickable="true" content-desc="Learn more"/>
  <node class="android.widget.Button" bounds="[970,980][1370,1060]" clickable="true" content-desc="Update"/>
</hierarchy>"""

# The "Daily Reward" streak-claim bottom sheet (client photo, 2026-08-06
# — also never captured as an XML dump), covering the Profile screen.
DAILY_REWARD_XML = """<hierarchy>
  <node class="android.view.View" bounds="[0,0][1920,2560]" clickable="true" content-desc=""/>
  <node class="android.view.View" bounds="[860,1220][1060,1240]" clickable="false" content-desc=""/>
  <node class="android.widget.TextView" bounds="[420,1330][1500,1400]" clickable="false" content-desc="Daily Reward"/>
  <node class="android.widget.TextView" bounds="[420,1420][1500,1500]" clickable="false" content-desc="Come back every day to collect rewards and keep your learning streak alive."/>
  <node class="android.widget.Button" bounds="[460,2160][1460,2260]" clickable="true" content-desc="CLAIM REWARD"/>
</hierarchy>"""

# The Retry-only pass-stats variant (stuck_screen_20260804_175138, at
# 93% accuracy): no Lessons, no next — Retry is fenced off (it redoes
# the finished quiz), so the only way out is the Android back button.
RETRY_ONLY_FINISH_XML = """<hierarchy>
  <node class="android.view.View" bounds="[42,532][678,588]" clickable="false" content-desc="Test completed"/>
  <node class="android.view.View" bounds="[42,602][678,644]" clickable="false" content-desc="You’ve earned 5 point(s) in this quiz"/>
  <node class="android.view.View" bounds="[536,795][622,844]" clickable="false" content-desc="93%"/>
  <node class="android.widget.ImageView" bounds="[42,942][678,1026]" clickable="true" content-desc="See your answers"/>
  <node class="android.widget.Button" bounds="[42,1306][678,1390]" clickable="true" content-desc="Retry"/>
</hierarchy>"""

# The pass-stats variant seen 2026-08-04 (stuck_screen_20260804_161732):
# unlike TEST_COMPLETED_XML, its next-lesson Button renders disabled and
# unlabeled, leaving "Lessons" as the only enabled way forward.
TEST_COMPLETED_NO_NEXT_XML = """<hierarchy>
  <node class="android.view.View" bounds="[42,532][678,588]" clickable="false" content-desc="Test completed"/>
  <node class="android.view.View" bounds="[42,602][678,644]" clickable="false" content-desc="You’ve earned 5 point(s) in this quiz"/>
  <node class="android.widget.ImageView" bounds="[42,942][678,1026]" clickable="true" content-desc="See your answers"/>
  <node class="android.widget.Button" bounds="[42,1306][678,1390]" clickable="true" content-desc="Lessons"/>
  <node class="android.widget.Button" bounds="[42,1411][678,1495]" clickable="false" content-desc=""/>
</hierarchy>"""


class TestPassStatsLessonsFallback(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        import main as main_mod
        self._main_time = main_mod.time
        main_mod.time = FakeTime()

    def tearDown(self):
        import main as main_mod
        main_mod.time = self._main_time
        os.chdir(self._cwd)

    def test_find_forward_button_falls_back_to_lessons(self):
        # With the next-lesson Button disabled and unlabeled, the screen
        # sat unrecognized for 10s and cost an app restart per quiz
        # (2026-08-04 16:17). "Lessons" is its only enabled way forward.
        import navigation
        nodes = qh.parse_screen(TEST_COMPLETED_NO_NEXT_XML)
        self.assertEqual(navigation.find_forward_button(nodes), "Lessons")

    def test_lessons_never_beats_an_enabled_next(self):
        # The 2026-08-02 variant still has a live "Next lesson" — that
        # one must keep winning over the Lessons fallback.
        import navigation
        nodes = qh.parse_screen(TEST_COMPLETED_XML)
        self.assertEqual(navigation.find_forward_button(nodes), "Next lesson")

    def test_lessons_alone_is_not_a_forward_button(self):
        # Outside a finish screen (e.g. the lessons list's own header)
        # "Lessons" must stay untapped — it navigates, not forwards.
        import navigation
        nodes = qh.parse_screen(LESSONS_TOP_XML)
        self.assertIsNone(navigation.find_forward_button(nodes))

    def test_rejoin_lesson_sequence_reopens_the_list(self):
        import navigation
        driver = TapDriver(LESSONS_TOP_XML)
        called = []
        saved = navigation.open_next_in_sequence
        navigation.open_next_in_sequence = lambda d: called.append(1) or True
        try:
            self.assertTrue(navigation.rejoin_lesson_sequence(driver))
        finally:
            navigation.open_next_in_sequence = saved
        self.assertEqual(called, [1])

    def test_rejoin_leaves_other_screens_alone(self):
        # One "Test/Dars N" title alone is a lesson PAGE, not the list;
        # the stats screen has none at all. Neither may re-fling.
        import navigation
        saved = navigation.open_next_in_sequence
        navigation.open_next_in_sequence = (
            lambda d: self.fail("must not reopen the sequence")
        )
        try:
            self.assertFalse(
                navigation.rejoin_lesson_sequence(TapDriver(TEST_COMPLETED_NO_NEXT_XML))
            )
            self.assertFalse(
                navigation.rejoin_lesson_sequence(TapDriver(VIDEO_LESSON_XML))
            )
        finally:
            navigation.open_next_in_sequence = saved

    def test_answer_loop_rejoins_from_a_lessons_list(self):
        # Tapping the stats screen's "Lessons" lands the ANSWER loop on
        # the lessons list — it must rejoin the sequence there instead
        # of paying an app restart.
        import main as main_mod
        driver = TapDriver(LESSONS_TOP_XML)
        rejoined = []

        def fake_rejoin(d):
            if "Dars 68" not in d.xml:
                return False
            rejoined.append(1)
            d.xml = UNKNOWN_AD_XML
            return True

        saved = main_mod.rejoin_lesson_sequence
        main_mod.rejoin_lesson_sequence = fake_rejoin
        try:
            with self.assertRaises(main_mod.StuckScreenError):
                main_mod.auto_answer_loop(driver)
        finally:
            main_mod.rejoin_lesson_sequence = saved
        self.assertEqual(rejoined, [1])

    def test_answer_loop_backs_out_of_a_retry_only_finish(self):
        # The Retry-only pass-stats screen: one back press to the
        # lessons list (never Retry), then the rejoin machinery — and
        # only ONE press, so a back that changes nothing still ends in
        # the recovering restart.
        import main as main_mod

        class BackDriver(TapDriver):
            def back(self):
                super().back()
                self.xml = LESSONS_TOP_XML

        driver = BackDriver(RETRY_ONLY_FINISH_XML)
        rejoined = []

        def fake_rejoin(d):
            if "Dars 68" not in d.xml:
                return False
            rejoined.append(1)
            d.xml = UNKNOWN_AD_XML
            return True

        saved = main_mod.rejoin_lesson_sequence
        main_mod.rejoin_lesson_sequence = fake_rejoin
        try:
            with self.assertRaises(main_mod.StuckScreenError):
                main_mod.auto_answer_loop(driver)
        finally:
            main_mod.rejoin_lesson_sequence = saved
        self.assertEqual(driver.back_presses, 1)
        self.assertEqual(rejoined, [1])

    def test_stats_screen_lessons_is_tapped_by_position_when_find_fails(self):
        # Both 2026-08-04 stats strandings: the tree showed "Lessons"
        # while find_element kept missing it (looping entry animation).
        # The tree knows where the button is — tap the spot.
        import main as main_mod

        class AnimatedStatsDriver(TapDriver):
            def tap(self, positions, duration=None):
                super().tap(positions, duration)
                if "Test completed" in self.xml:
                    self.xml = LESSONS_TOP_XML

        driver = AnimatedStatsDriver(TEST_COMPLETED_NO_NEXT_XML)
        rejoined = []

        def fake_rejoin(d):
            if "Dars 68" not in d.xml:
                return False
            rejoined.append(1)
            d.xml = UNKNOWN_AD_XML
            return True

        saved = main_mod.rejoin_lesson_sequence
        main_mod.rejoin_lesson_sequence = fake_rejoin
        try:
            with self.assertRaises(main_mod.StuckScreenError):
                main_mod.auto_answer_loop(driver)
        finally:
            main_mod.rejoin_lesson_sequence = saved
        self.assertEqual(rejoined, [1])
        # center of the Lessons button's bounds [42,1306][678,1390]
        self.assertIn((360, 1348), driver.taps)

    def test_split_pill_paywall_is_recognized_and_dismissed(self):
        # The subscriber pill split into one-glyph descs must still read
        # as a promo, and the unlabeled top-left X must be blind-tapped.
        import navigation
        self.assertTrue(qh.looks_like_promo(
            ["3", "5", " ", "1", "3", "0", "+", " subscribers"]
        ))
        driver = TapDriver(PAYWALL_SPLIT_PILL_XML)
        self.assertTrue(navigation.dismiss_popup(driver))
        # center of the unlabeled close ImageView [42,100][112,170]
        self.assertEqual(driver.taps, [(77, 135)])

    def test_payment_sheet_is_backed_out_of_and_its_button_fenced(self):
        # The payment CTA must never be a tap target, and the sheet
        # closes like any bottom sheet — Android back, success claimed
        # only once the sheet is really gone.
        import navigation

        self.assertEqual(
            navigation.candidate_buttons(qh.parse_screen(PAYMENT_SHEET_XML)),
            [],
        )

        class BackDriver(TapDriver):
            def back(self):
                super().back()
                self.xml = LESSONS_TOP_XML

        driver = BackDriver(PAYMENT_SHEET_XML)
        self.assertTrue(navigation.dismiss_popup(driver))
        self.assertEqual(driver.back_presses, 1)
        self.assertEqual(driver.taps, [])

    def test_payment_sheet_that_refuses_to_close_reports_failure(self):
        # A sheet still up after back must read as NOT dismissed, so the
        # idle timer keeps running into the recovering restart.
        import navigation
        driver = TapDriver(PAYMENT_SHEET_XML)
        self.assertFalse(navigation.dismiss_popup(driver))

    def test_language_cross_sell_sheet_is_backed_out_of_and_its_button_fenced(self):
        # Reproduces stuck_screen_20260804_184527: the runner had no
        # dismisser for this sheet and idled past the 10s limit — the
        # app restart that followed is what read, on the client's phone,
        # as the matching quiz being frozen. Its CTA must never be a tap
        # target, and the sheet closes like any bottom sheet — Android
        # back, success claimed only once the sheet is really gone.
        import navigation

        self.assertEqual(
            navigation.candidate_buttons(qh.parse_screen(LANGUAGE_CROSS_SELL_XML)),
            [],
        )

        class BackDriver(TapDriver):
            def back(self):
                super().back()
                self.xml = LESSONS_TOP_XML

        driver = BackDriver(LANGUAGE_CROSS_SELL_XML)
        self.assertTrue(navigation.dismiss_popup(driver))
        self.assertEqual(driver.back_presses, 1)
        self.assertEqual(driver.taps, [])

    def test_language_cross_sell_sheet_that_refuses_to_close_reports_failure(self):
        # A sheet still up after back must read as NOT dismissed, so the
        # idle timer keeps running into the recovering restart.
        import navigation
        driver = TapDriver(LANGUAGE_CROSS_SELL_XML)
        self.assertFalse(navigation.dismiss_popup(driver))
        self.assertEqual(driver.back_presses, 1)

    def test_play_store_update_nag_is_backed_out_of_and_its_buttons_fenced(self):
        # Client photo, 2026-08-06: neither "Update" (walks out to the
        # Play Store) nor "Learn more" may ever be tapped, and back is
        # used directly rather than the unlabeled close icon.
        import navigation

        self.assertEqual(
            navigation.candidate_buttons(qh.parse_screen(PLAY_STORE_UPDATE_XML)),
            [],
        )

        class BackDriver(TapDriver):
            def back(self):
                super().back()
                self.xml = LESSONS_TOP_XML

        driver = BackDriver(PLAY_STORE_UPDATE_XML)
        self.assertTrue(navigation.dismiss_popup(driver))
        self.assertEqual(driver.back_presses, 1)
        self.assertEqual(driver.taps, [])

    def test_play_store_update_nag_that_refuses_to_close_reports_failure(self):
        import navigation
        driver = TapDriver(PLAY_STORE_UPDATE_XML)
        self.assertFalse(navigation.dismiss_popup(driver))
        self.assertEqual(driver.back_presses, 1)

    def test_daily_reward_sheet_is_backed_out_of_and_its_button_fenced(self):
        # Client photo, 2026-08-06: covers the Profile screen with no
        # existing dismisser (no Scrim/Day/promo marker matches it), so
        # it would otherwise sit unrecognized until the idle restart.
        # CLAIM REWARD is an unproven CTA and must never be a tap target.
        import navigation

        self.assertEqual(
            navigation.candidate_buttons(qh.parse_screen(DAILY_REWARD_XML)),
            [],
        )

        class BackDriver(TapDriver):
            def back(self):
                super().back()
                self.xml = LESSONS_TOP_XML

        driver = BackDriver(DAILY_REWARD_XML)
        self.assertTrue(navigation.dismiss_popup(driver))
        self.assertEqual(driver.back_presses, 1)
        self.assertEqual(driver.taps, [])

    def test_daily_reward_sheet_that_refuses_to_close_reports_failure(self):
        import navigation
        driver = TapDriver(DAILY_REWARD_XML)
        self.assertFalse(navigation.dismiss_popup(driver))
        self.assertEqual(driver.back_presses, 1)

    def test_discount_countdown_is_dismissed_via_its_own_label(self):
        # The interstitial names its close surface "Dismiss" — tap it by
        # desc, never the "Chegirmadan foydalanish" CTA.
        import navigation

        self.assertTrue(qh.looks_like_promo(["Masus taklif - 30% chegirma"]))
        self.assertEqual(
            navigation.candidate_buttons(qh.parse_screen(DISCOUNT_COUNTDOWN_XML)),
            [],
        )
        # a vocabulary question about the word itself is NOT a promo
        self.assertFalse(qh.looks_like_promo(["Discount -", "Chegirma", "Narx"]))

        class ClickableDriver(TapDriver):
            def __init__(self, xml):
                super().__init__(xml)
                self.clicks = []

            def find_element(self, by, value):
                self.clicks.append(value)
                return FakeElement("dismiss surface")

        driver = ClickableDriver(DISCOUNT_COUNTDOWN_XML)
        self.assertTrue(navigation.dismiss_popup(driver))
        self.assertTrue(any("Dismiss" in c for c in driver.clicks), driver.clicks)

    def test_discount_countdown_is_recognized_in_english_too(self):
        # Client request, 2026-08-06: make sure an English render of this
        # same interstitial (never actually seen — the app has only shown
        # it in Uzbek so far) still reads as a promo, and a vocabulary
        # question about "discount" itself still doesn't.
        self.assertTrue(qh.looks_like_promo(["Special offer - 30% discount"]))
        self.assertFalse(qh.looks_like_promo(["Discount -", "Sale", "Price"]))

    def test_back_out_never_fires_twice_in_one_episode(self):
        # A finish screen where back does NOTHING: exactly one press,
        # then the usual stuck restart with the tree saved.
        import main as main_mod
        driver = TapDriver(RETRY_ONLY_FINISH_XML)
        with self.assertRaises(main_mod.StuckScreenError):
            main_mod.auto_answer_loop(driver)
        self.assertEqual(driver.back_presses, 1)
        self.assertEqual(driver.taps, [])


if __name__ == "__main__":
    unittest.main()
