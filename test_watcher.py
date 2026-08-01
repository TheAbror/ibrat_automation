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

import locators as loc
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

    def test_get_visible_options_returns_none_when_stale(self):
        driver = FakeDriver([FakeElement("A"), FakeElement("B", stale=True)])
        self.assertIsNone(watcher.get_visible_options(driver))

    def test_get_visible_options_filters_nav_buttons(self):
        driver = FakeDriver(
            [FakeElement("A"), FakeElement("Next"), FakeElement("Continue"), FakeElement("The")]
        )
        self.assertEqual(watcher.get_visible_options(driver), ["A", "The"])

    def test_tap_next_retries_after_stale_click(self):
        el = FakeElement("Next", click_fails=1)
        driver = FakeDriver([el])
        self.assertTrue(watcher.tap_next(driver))
        self.assertTrue(el.clicked)


class ScriptedDriver:
    """Fake driver that answers find_elements per locator value."""

    def __init__(self, mapping):
        self.mapping = mapping

    def find_elements(self, by, value):
        return self.mapping.get(value, [])

    def find_element(self, by, value):
        els = self.mapping.get(value)
        if not els:
            raise NoSuchElementException(value)
        return els[0]


class TestPollOnce(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())

    def tearDown(self):
        os.chdir(self._cwd)

    def test_question_screen_updates_state(self):
        driver = ScriptedDriver({
            loc.QUESTION_TEXT[1]: [FakeElement("___ giraffes are tall.")],
            loc.ALL_BUTTONS[1]: [FakeElement("A"), FakeElement("The")],
        })
        state = {"question": None, "options": [], "correct": 0, "incorrect": 0}
        results = []
        watcher.poll_once(driver, state, results)
        self.assertEqual(state["question"], "___ giraffes are tall.")
        self.assertEqual(state["options"], ["A", "The"])
        self.assertEqual(results, [])

    def test_feedback_sheet_logs_result_and_taps_next(self):
        mapping = {loc.NICELY_DONE[1]: [FakeElement("done")]}

        class NextEl(FakeElement):
            def click(inner):
                mapping.pop(loc.NICELY_DONE[1], None)

        mapping[loc.NEXT_BUTTON[1]] = [NextEl("Next")]
        driver = ScriptedDriver(mapping)
        state = {"question": "Q1", "options": ["A"], "correct": 0, "incorrect": 0}
        results = []
        watcher.poll_once(driver, state, results)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["result"], "correct")
        self.assertEqual(results[0]["question"], "Q1")
        self.assertEqual(state["correct"], 1)


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

            def find_elements(self, by, value):
                raise WebDriverException("socket hang up")

            def quit(self):
                self.quit_called = True

        class StopDriver:
            def find_elements(self, by, value):
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
            def find_elements(self, by, value):
                raise WebDriverException("socket hang up")

            def quit(self):
                pass

        made = []
        with self.assertRaises(WebDriverException):
            watcher.run(lambda: made.append(1) or DeadDriver())
        self.assertEqual(len(made), 2, "initial connect + one reconnect, then give up")


if __name__ == "__main__":
    unittest.main()
