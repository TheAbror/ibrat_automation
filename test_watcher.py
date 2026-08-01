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


if __name__ == "__main__":
    unittest.main()
