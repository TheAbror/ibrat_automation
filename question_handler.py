"""Shared screen-reading and answering logic for watcher.py and main.py."""
import xml.etree.ElementTree as ET

from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException

import locators as loc

CORRECT_MARKERS = ("Nicely done",)
INCORRECT_MARKERS = ("Incorrect Answer",)
NEXT_LABELS = ("Next",)
# "null" is the report (!) icon-button, which exposes the literal string
# "null" as its label on every question screen — not a real answer option.
OPTION_IGNORE = NEXT_LABELS + ("Continue", "null")


def parse_screen(xml):
    """One atomic snapshot of the UI tree as [(class, content-desc), ...]."""
    return [
        (el.get("class") or "", el.get("content-desc") or "")
        for el in ET.fromstring(xml).iter()
    ]


def classify_sheet(descs):
    """Return 'correct' / 'incorrect' / 'other' if a feedback sheet is up, else None."""
    for d in descs:
        if any(m in d for m in CORRECT_MARKERS):
            return "correct"
    for d in descs:
        if any(m in d for m in INCORRECT_MARKERS):
            return "incorrect"
    if any(d in NEXT_LABELS for d in descs):
        return "other"
    return None


def detect_question_type(question, options):
    """Classify a question screen: multiple_choice, fill_the_blank, or matching.

    - multiple_choice: the sentence contains a "___" or "|_|" blank and the
      options are a few words to choose from.
    - matching: the title contains "moslashtiring" ("Moslashtiring.",
      "So'zlarni moslashtiring.", ...) — pair cards.
    - fill_the_blank: the sentence is built from many word chips (no blank
      in the prompt).
    """
    q = (question or "").strip().lower().rstrip(".")
    if "moslashtiring" in q or q.startswith("match"):
        return "matching"
    if "___" in q or "|_|" in q:
        return "multiple_choice"
    if len(options) >= 5:
        return "fill_the_blank"
    return "multiple_choice"


def tap_next(driver):
    for _ in range(2):
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable(loc.NEXT_BUTTON)
            )
            btn.click()
            return True
        except StaleElementReferenceException:
            continue
        except TimeoutException:
            return False
    return False


# --- Answer strategies (pure logic, no driver) ---

def pick_revealed_answer(entry):
    """The real answer among the texts the feedback sheet revealed.

    multiple_choice sheets show the sentence with the blank filled in
    (sometimes with the WRONG word) plus the answer word — so the answer
    is the element that matches one of the question's options, and when
    the diff echoes it twice, the most frequent match. fill_the_blank
    sheets show the full correct sentence — the longest element — with
    stray chip texts as noise. Returns None when nothing trustworthy.
    """
    answers = entry.get("correct_answer") or []
    if not answers:
        return None
    if entry.get("type") == "multiple_choice":
        options = entry.get("options") or []
        matches = [
            a for a in answers
            if any(a.strip().lower() == o.strip().lower() for o in options)
        ]
        if matches:
            return max(matches, key=matches.count)
        return None
    return max(answers, key=len)


def build_answer_map(results):
    """question -> known correct answer text.

    The feedback sheet reveals the correct answer whether the attempt was
    right or wrong, so every entry with a captured answer counts.
    """
    known = {}
    for entry in results:
        answer = pick_revealed_answer(entry)
        if answer is not None:
            known[entry["question"]] = answer
    return known


def choose_mc_option(question, options, known, attempted):
    """Pick a multiple-choice option to tap.

    Known answer wins. Otherwise option A — and on a repeat of the same
    question, the next option not yet tried this run, so a wrong first
    guess can't loop forever.
    """
    if not options:
        return None
    answer = known.get(question)
    if answer:
        for o in options:
            if o.strip().lower() == answer.strip().lower():
                return o
    tried = attempted.setdefault(question, [])
    for o in options:
        if o not in tried:
            tried.append(o)
            return o
    return options[0]


def chip_sequence(question, options, known):
    """Order in which to tap the word chips of a fill_the_blank question.

    If the correct sentence is known and every word of it is available as a
    chip, tap in sentence order. Otherwise tap just the first chip and
    submit — the feedback sheet then reveals the correct sentence, which
    gets saved for the next encounter.
    """
    answer = known.get(question)
    if answer:
        # Chip labels can carry trailing spaces ("They ", "go ") — match
        # stripped, but return the original labels so taps find them.
        pool = list(options)
        sequence = []
        for word in answer.split():
            chip = next((c for c in pool if c.strip() == word), None)
            if chip is None:
                break
            pool.remove(chip)
            sequence.append(chip)
        else:
            return sequence
    return list(options[:1])


def split_matching_cards(cards):
    """Split matching-screen cards into (lefts, rights).

    Cards come row by row: [L1, R1, L2, R2, ...] — left column at even
    indices, right column at odd indices.
    """
    return cards[0::2], cards[1::2]


def xpath_literal(s):
    """Quote arbitrary text (with ' or ") for use inside an XPath expression."""
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    parts = s.split("'")
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in parts) + ")"
