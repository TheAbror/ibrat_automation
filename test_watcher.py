"""Unit tests for the shared screen logic, watcher polling, and strategies.

Run with: python3 -m unittest test_watcher -v
"""
import os
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
        import navigation
        driver = TapDriver(PROMO_INTERSTITIAL_XML)
        self.assertTrue(navigation.dismiss_popup(driver))
        self.assertEqual(driver.back_presses, 1)
        self.assertEqual(driver.taps, [], "nothing to blind-tap on this screen")

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
        results = [
            {"question": "Q1", "type": "multiple_choice", "options": ["a", "b"],
             "result": "incorrect", "correct_answer": ["b"]},
            {"question": "Q2", "type": "multiple_choice", "options": ["x", "y"]},
            {"question": "Q1", "type": "multiple_choice", "options": ["a", "b"],
             "result": "correct", "correct_answer": ["b"]},
        ]
        watcher.save_results(results)
        self.assertEqual([e["question"] for e in results], ["Q1", "Q2"])
        self.assertEqual(results[0]["result"], "correct", "latest repeat wins")
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

    def test_dedupe_keeps_matching_entries_per_board(self):
        board_a = {"question": "Moslashtiring.", "type": "matching",
                   "options": ["Drive", "along the road", "Fly", "to Tashkent"],
                   "correct_answer": [["Drive", "along the road"]]}
        board_b = {"question": "Moslashtiring.", "type": "matching",
                   "options": ["Exam", "Imtihon", "Boring", "Zerikarli"],
                   "correct_answer": [["Exam", "Imtihon"]]}
        # board A re-served with shuffled cards: replaces the original A
        board_a2 = {"question": "Moslashtiring.", "type": "matching",
                    "options": ["Fly", "to Tashkent", "Drive", "along the road"],
                    "correct_answer": [["Fly", "to Tashkent"], ["Drive", "along the road"]]}
        deduped = qh.dedupe_results([board_a, board_b, board_a2])
        self.assertEqual(len(deduped), 2, "different boards both kept")
        self.assertIn(board_a2, deduped)
        self.assertNotIn(board_a, deduped)

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
        self.assertEqual(driver.coord_taps, [(360, 880)])

    def test_chest_tap_walks_nearby_heights_until_screen_changes(self):
        # If the first tap misses the chest (screen unchanged), nearby
        # heights on the center column are tried before giving up.
        import navigation
        driver = TapDriver(CHEST_TAP_XML)
        self.assertFalse(navigation.tap_forward_button(driver))
        self.assertEqual(driver.taps, [(360, 880), (360, 752), (360, 992)])

    def test_full_flow_open_chest_then_tap_then_continue(self):
        import navigation
        driver = ChestFlowDriver()
        for _ in range(3):
            self.assertTrue(navigation.tap_forward_button(driver))
        self.assertTrue(any("Open chest" in v for v in driver.clicked), driver.clicked)
        self.assertEqual(driver.coord_taps, [(360, 880)])
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
            self.assertEqual(driver.taps[0], (360, 880), xml)

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
  <node class="android.view.View" bounds="[100,600][620,760]" clickable="false" content-desc="Ibrat Pro — 50% chegirma!"/>
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
        self._miss = main_mod.MISS_EVERY
        main_mod.time = FakeTime()
        self._nav_sleep = navigation.time.sleep
        navigation.time.sleep = lambda s: None

    def tearDown(self):
        import main as main_mod
        import navigation
        main_mod.time = self._main_time
        main_mod.MISS_EVERY = self._miss
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

    def test_loop_misses_every_nth_known_answer(self):
        import main as main_mod
        watcher.save_results([{
            "question": "Clever -",
            "type": "word_translation",
            "result": "incorrect",
            "options": ["Nohaq ", "Qizg’anchiq", "Aqlli ", "Mehribon"],
            "correct_answer": ["Aqlli"],
        }])
        main_mod.MISS_EVERY = 1  # throttle every known answer
        driver = WordTranslationFlowDriver()
        with self.assertRaises(main_mod.StuckScreenError):
            main_mod.auto_answer_loop(driver)
        self.assertIn("Nohaq ", driver.clicked[0],
                      "known answer must be deliberately missed")
        self.assertTrue(any("Continue" in v for v in driver.clicked))


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


if __name__ == "__main__":
    unittest.main()
