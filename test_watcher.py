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
    def test_build_answer_map_takes_correct_entries_only(self):
        results = [
            {"question": "Q1", "result": "correct", "correct_answer": ["an"]},
            {"question": "Q2", "result": "incorrect"},
            {"question": "Q3", "result": "correct"},  # old format, no answer
        ]
        self.assertEqual(qh.build_answer_map(results), {"Q1": "an"})

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

    def test_chip_sequence_falls_back_to_first_line_order(self):
        chips = ["She", "is", "reading"]
        self.assertEqual(qh.chip_sequence("Q1", chips, {}), chips)

    def test_chip_sequence_ignores_known_answer_with_missing_chip(self):
        known = {"Q1": "She is sleeping."}
        chips = ["She", "is", "reading"]
        self.assertEqual(qh.chip_sequence("Q1", chips, known), chips)

    def test_matching_tries_direct_neighbour_first(self):
        cards = ["The", "letter he wrote", "An", "apple", "A", "cat"]
        attempts = qh.matching_attempt_pairs(cards)
        self.assertEqual(attempts[0], ("The", "letter he wrote"))
        self.assertEqual(attempts[3], ("An", "apple"))
        self.assertEqual(len(attempts), 9)  # 3 lefts x 3 rights
        self.assertEqual(len(set(attempts)), 9)  # every combination once

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

    def test_incorrect_sheet_has_no_correct_answer_field(self):
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
        self.assertNotIn("correct_answer", entry)
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
