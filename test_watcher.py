"""Unit tests for the watcher's polling path.

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

import question_handler
import watcher


class FakeElement:
    def __init__(self, desc, stale=False, click_fails=0):
        self._desc = desc
        self._stale = stale
        self._click_fails = click_fails
        self.clicked = False

    def get_attribute(self, name):
        if self._stale:
            raise StaleElementReferenceException("element gone from DOM")
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


class TestStaleHandling(unittest.TestCase):
    def test_get_question_text_returns_none_when_stale(self):
        driver = FakeDriver([FakeElement("q", stale=True)])
        self.assertIsNone(question_handler.get_question_text(driver))

    def test_get_question_text_normal(self):
        driver = FakeDriver([FakeElement("What is ___?")])
        self.assertEqual(question_handler.get_question_text(driver), "What is ___?")

    def test_tap_next_retries_after_stale_click(self):
        el = FakeElement("Next", click_fails=1)
        driver = FakeDriver([el])
        self.assertTrue(watcher.tap_next(driver))
        self.assertTrue(el.clicked)


class TestDetectQuestionType(unittest.TestCase):
    def test_underscore_blank_is_multiple_choice(self):
        self.assertEqual(
            question_handler.detect_question_type(
                "He's reading ___ interesting book.", ["a", "an", "the"]
            ),
            "multiple_choice",
        )

    def test_pipe_blank_is_multiple_choice(self):
        self.assertEqual(
            question_handler.detect_question_type(
                "We're going to |_| restaurant for dinner tonight.",
                ["an", "a", "the"],
            ),
            "multiple_choice",
        )

    def test_moslashtiring_is_matching(self):
        self.assertEqual(
            question_handler.detect_question_type(
                "Moslashtiring.",
                ["The", "letter he wrote", "An", "apple", "A", "cat"],
            ),
            "matching",
        )

    def test_many_chips_is_fill_the_blank(self):
        self.assertEqual(
            question_handler.detect_question_type(
                "U (qiz) sen tavsiya qilgan kitobni o‘qiyapti.",
                ["She", "is", "reading", "the", "book", "you", "recommended."],
            ),
            "fill_the_blank",
        )

    def test_few_options_without_blank_falls_back_to_multiple_choice(self):
        self.assertEqual(
            question_handler.detect_question_type(
                "Choose the correct sentence", ["A cat", "An cat"]
            ),
            "multiple_choice",
        )


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

UNKNOWN_SHEET_XML = """<hierarchy>
  <node class="android.view.View" content-desc="Some new title!"/>
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

    @staticmethod
    def fresh_state():
        return {
            "question": None, "options": [],
            "correct": 0, "incorrect": 0, "other": 0,
        }

    def test_question_screen_updates_state(self):
        driver = XmlDriver(QUESTION_XML)
        state = self.fresh_state()
        results = []
        watcher.poll_once(driver, state, results)
        self.assertEqual(state["question"], "He's reading ___ interesting book.")
        self.assertEqual(state["options"], ["a", "an", "the"])
        self.assertEqual(results, [])

    def test_feedback_sheet_logs_type_and_result(self):
        driver = XmlDriver(QUESTION_XML)
        state = self.fresh_state()
        results = []
        watcher.poll_once(driver, state, results)  # remember the question

        class NextEl(FakeElement):
            def click(inner):
                driver.xml = QUESTION_XML

        driver.next_el = NextEl("Next")
        driver.xml = FEEDBACK_XML
        watcher.poll_once(driver, state, results)

        self.assertEqual(len(results), 1)
        entry = results[0]
        self.assertEqual(entry["type"], "multiple_choice")
        self.assertEqual(entry["result"], "correct")
        self.assertEqual(entry["question"], "He's reading ___ interesting book.")
        self.assertEqual(entry["options"], ["a", "an", "the"])
        self.assertEqual(entry["correct_answer"], ["an"])
        self.assertEqual(state["correct"], 1)

    def test_incorrect_sheet_has_no_correct_answer_field(self):
        driver = XmlDriver(QUESTION_XML)
        state = self.fresh_state()
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

    def test_unrecognized_sheet_still_logged_and_skipped(self):
        driver = XmlDriver(UNKNOWN_SHEET_XML)
        state = self.fresh_state()
        state["question"] = "Q1"
        results = []

        class NextEl(FakeElement):
            def click(inner):
                driver.xml = QUESTION_XML

        driver.next_el = NextEl("Next")
        watcher.poll_once(driver, state, results)
        self.assertEqual(results[0]["result"], "other")
        self.assertEqual(state["other"], 1)


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
