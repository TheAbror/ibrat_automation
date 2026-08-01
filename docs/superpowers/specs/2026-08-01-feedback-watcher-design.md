# Feedback Watcher — Design

**Date:** 2026-08-01
**Status:** Approved (approach 1, JSON output)

## Purpose

While the user answers test questions manually in the Ibrat app, a watcher
script detects each correct/incorrect feedback bottom sheet, records the
result, and auto-taps Next so the user never has to dismiss it. The recorded
data doubles as source material for filling `answer_bank.json`.

## Approach (chosen: poll-and-remember)

The watcher polls the screen in a loop:

- **No feedback sheet visible** → refresh the remembered question text and
  the list of visible option/chip texts.
- **Feedback sheet visible** ("Nicely done!" / "Incorrect Answer!") → pair
  the *remembered* question with the result, append an entry to
  `results.json`, tap Next, then wait until the sheet is gone before
  resuming. Pairing with the remembered question is robust even if the
  bottom sheet hides the question in the accessibility tree.

Rejected alternative: read the question only at the moment the sheet
appears — risks logging results with no question text.

## Components

- `watcher.py` (new) — the only new file. Reuses `config.py`, `locators.py`,
  and `check_answer_feedback` / `get_question_text` from
  `question_handler.py`.
- `results.json` (output) — JSON array, one entry per skipped sheet:
  `{"time", "result", "question", "options"}`. Loaded at start if it
  exists, so repeated runs append rather than overwrite.

## Key decisions

- **Attach, don't launch:** the Appium session is created *without*
  `appPackage`/`appActivity`, so it attaches to whatever screen is
  currently open instead of relaunching the app and kicking the user out
  of the test. The user navigates to the question screen themselves, then
  starts the watcher.
- **No duplicate logging:** after logging a sheet, the watcher waits until
  the sheet disappears before watching again — even if the Next tap failed
  and the user dismisses it manually.
- **Graceful end:** if Next isn't found (e.g. test summary screen), print a
  note and keep watching. Ctrl+C stops the watcher and prints a
  correct/incorrect tally.

## Testing

Static: py_compile + import check. Behavioral: requires the physical
device — user runs a manual pass through a test and inspects
`results.json`.
