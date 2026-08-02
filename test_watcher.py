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

    def test_split_matching_cards_by_column(self):
        cards = ["The", "letter he wrote", "An", "apple", "A", "cat"]
        lefts, rights = qh.split_matching_cards(cards)
        self.assertEqual(lefts, ["The", "An", "A"])
        self.assertEqual(rights, ["letter he wrote", "apple", "cat"])

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

    def test_pair_attempt_order_tries_known_partner_first(self):
        remaining = ["to school", "along the road", "for a stroll"]
        known = {"Drive": "along the road"}
        self.assertEqual(
            qh.pair_attempt_order("Drive", remaining, known),
            ["along the road", "to school", "for a stroll"],
        )
        # unknown left card, or known partner already used: original order
        self.assertEqual(
            qh.pair_attempt_order("Go", remaining, known), remaining
        )
        self.assertEqual(
            qh.pair_attempt_order("Drive", ["to school"], known), ["to school"]
        )

    def test_xpath_literal_handles_apostrophes(self):
        self.assertEqual(qh.xpath_literal("the"), "'the'")
        self.assertEqual(qh.xpath_literal("He's"), '"He\'s"')
        self.assertTrue(qh.xpath_literal("He's \"x\"").startswith("concat("))


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


class TestRescueStuckScreen(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        import navigation
        self._sleep = navigation.time.sleep
        navigation.time.sleep = lambda s: None

    def tearDown(self):
        import navigation
        navigation.time.sleep = self._sleep
        os.chdir(self._cwd)

    def test_saves_tree_and_reports_a_frozen_screen(self):
        import main as main_mod
        driver = TapDriver(CHEST_TAP_MERGED_XML)
        self.assertFalse(main_mod.rescue_stuck_screen(driver))
        with open(main_mod.STUCK_SCREEN_FILE, encoding="utf-8") as f:
            self.assertEqual(f.read(), CHEST_TAP_MERGED_XML)
        self.assertEqual(len(driver.taps), 3, "all three chest heights tried")

    def test_rescued_when_a_center_tap_moves_the_screen(self):
        import main as main_mod

        class UnstickDriver(TapDriver):
            def tap(self, positions, duration=None):
                super().tap(positions, duration)
                self.xml = HOME_SCREEN_XML

        driver = UnstickDriver(CHEST_TAP_TEXT_ATTR_XML)
        self.assertTrue(main_mod.rescue_stuck_screen(driver))
        self.assertEqual(driver.taps, [(360, 880)])

    def test_auto_loop_tries_rescue_before_the_idle_exit(self):
        import main as main_mod
        import navigation
        self._main_time = main_mod.time
        main_mod.time = FakeTime()
        self._nav_time = navigation.time
        navigation.time = FakeTime()
        calls = []

        def fake_rescue(driver):
            calls.append(1)
            return False

        saved = main_mod.rescue_stuck_screen
        main_mod.rescue_stuck_screen = fake_rescue
        try:
            main_mod.auto_answer_loop(TapDriver(CHEST_TAP_MERGED_XML))
        finally:
            main_mod.rescue_stuck_screen = saved
            main_mod.time = self._main_time
            navigation.time = self._nav_time
        self.assertEqual(len(calls), 1, "rescue attempted once, then the loop gave up")


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

        outcomes = [main_mod.AppLostError("com.android.launcher3"), None]

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
        self.assertEqual(len(sessions), 2, "a fresh app launch after the crash")
        self.assertEqual(outcomes, [], "answering resumed on the new session")

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


if __name__ == "__main__":
    unittest.main()
