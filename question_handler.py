"""Shared screen-reading and answering logic for watcher.py and main.py."""
import re
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
# "Retry" sits on the test-finish screen next to "Next ..."; counting it as
# an option turns that screen into a fake question whose option A restarts
# the whole test.
OPTION_IGNORE = NEXT_LABELS + ("Continue", "null", "Retry")

# Full-screen promo/upsell screens: the Pro subscription offer and the
# IELTS interstitial ("O'ychi o'yini ..." / IELTSGA GOO!). Both carry the
# "35 130+ subscribers" pill; the interstitial also has its own CTA texts.
# Their buttons must never count as answer options — option A would be a
# paywall CTA.
PROMO_MARKERS = ("+ subscribers", "IELTSGA", "VAQT TOPILAVERADI")


def looks_like_promo(descs):
    """True when the screen is a promo/upsell, judged by its marker texts."""
    return any(m in d for d in descs for m in PROMO_MARKERS)


def parse_screen(xml):
    """One atomic snapshot of the UI tree as [(class, label), ...].

    The label is the content-desc (how this Flutter app exposes nearly all
    its text), falling back to the text attribute for screens that render
    natively — the chest-reward screens strand the runner if their texts
    are only readable there.
    """
    return [
        (el.get("class") or "", el.get("content-desc") or el.get("text") or "")
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


def detect_question_type(question, options, descs=()):
    """Classify a question screen: multiple_choice, word_translation,
    fill_the_blank, or matching.

    - matching: the title contains "moslashtiring" ("Moslashtiring.",
      "So'zlarni moslashtiring.", ...) — pair cards.
    - fill_the_blank: the sentence is built from many word chips. The chip
      count decides BEFORE any "___"/"|_|" blank in the prompt: the
      sentence-building questions ("The phone rang, but I didn't hear
      it. ___." + 8 chips) carry a blank too, and treating them as
      multiple choice taps one chip, never presses Continue, and waits
      forever for a feedback sheet.
    - word_translation: the vocabulary screens ("Clever -" + 4 chips) sit
      under the chip floor, so only the Continue button on screen (passed
      in via descs) tells them apart from multiple_choice — the chip tap
      alone submits nothing, exactly the fill_the_blank trap again
      (2026-08-02, stuck_screen.xml).
    - multiple_choice: everything else — a few words to choose from, where
      tapping the option submits by itself.
    """
    q = (question or "").strip().lower().rstrip(".")
    if "moslashtiring" in q or q.startswith("match"):
        return "matching"
    if len(options) >= 5:
        return "fill_the_blank"
    if any(d.strip() == "Continue" for d in descs):
        return "word_translation"
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
    the diff echoes it twice, the most frequent match. word_translation
    sheets reveal the correct word the same way, but their diff can also
    echo the TAPPED chip (it moved into the answer area after the
    pre-sheet snapshot) — when two distinct options match, nothing says
    which is the answer, and guessing would repeat a wrong tap forever,
    so such an entry teaches nothing. fill_the_blank sheets show the
    full correct sentence — the longest element — with stray chip texts
    as noise. Returns None when nothing trustworthy.
    """
    answers = entry.get("correct_answer") or []
    if not answers:
        return None
    options = entry.get("options") or []
    matches = [
        a for a in answers
        if any(a.strip().lower() == o.strip().lower() for o in options)
    ]
    if entry.get("type") == "word_translation":
        if len({m.strip().lower() for m in matches}) == 1:
            return matches[0]
        return None
    if entry.get("type") == "multiple_choice":
        if matches:
            return max(matches, key=matches.count)
        return None
    return max(answers, key=len)


def _teaches(entry):
    """True when the entry carries something the runner can learn from."""
    if entry.get("type") == "matching":
        return bool(entry.get("correct_answer"))
    return pick_revealed_answer(entry) is not None


def dedupe_results(results):
    """One entry per question — the answer book, not the attempt log.

    Repeats of a question replace their earlier entry, EXCEPT that an
    entry with a usable answer is never replaced by one without: a later
    encounter with a noisy or empty reveal must not erase a learned
    answer. Matching entries all share one question text, so they are
    kept per board (question + card set) instead. Entries with no
    question text at all (a sheet met before any question) are dropped.
    """
    kept = {}
    for entry in results:
        question = entry.get("question")
        if not question:
            continue
        if entry.get("type") == "matching":
            key = (question, tuple(sorted(map(str, entry.get("options") or []))))
        else:
            key = question
        old = kept.get(key)
        if old is not None and _teaches(old) and not _teaches(entry):
            continue
        kept[key] = entry
    return list(kept.values())


def build_answer_map(results):
    """question -> known correct answer text.

    The feedback sheet reveals the correct answer whether the attempt was
    right or wrong, so every entry with a captured answer counts.
    """
    known = {}
    for entry in results:
        # Matching screens all share one question text, so they are looked
        # up per card via build_pair_map instead.
        if entry.get("type") == "matching":
            continue
        answer = pick_revealed_answer(entry)
        if answer is not None:
            known[entry["question"]] = answer
    return known


def build_pair_map(results):
    """left card -> right card, from pairs discovered on matching screens.

    Keyed by card because every matching screen shares the same question
    text — and a vocabulary pair stays valid wherever its cards reappear.
    """
    pairs = {}
    for entry in results:
        if entry.get("type") != "matching":
            continue
        for pair in entry.get("correct_answer") or []:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                pairs[pair[0]] = pair[1]
    return pairs


def parse_cards(xml):
    """Matching cards with geometry: label, tap center, and clickability.

    Card labels repeat on the category boards ("Singular"/"Plural" twice
    each), so cards must be told apart — and tapped — by position, never
    by text. Locked (already matched) pairs stay on screen with their
    labels but clickable="false".
    """
    cards = []
    for el in ET.fromstring(xml).iter():
        if (el.get("class") or "") != "android.widget.Button":
            continue
        label = el.get("content-desc") or el.get("text") or ""
        if not label or label in OPTION_IGNORE:
            continue
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", el.get("bounds") or "")
        if not m:
            continue
        x1, y1, x2, y2 = map(int, m.groups())
        cards.append({
            "label": label,
            "x": (x1 + x2) // 2,
            "y": (y1 + y2) // 2,
            "clickable": el.get("clickable") == "true",
        })
    return cards


def split_matching_columns(cards):
    """(lefts, rights) among the cards still in play, split by geometry.

    Left/right membership comes from each card's center against the
    board's midline, order within a column from top to bottom. Locked
    pairs are out of play — a tap on them is swallowed.
    """
    active = [c for c in cards if c["clickable"]]
    if not active:
        return [], []
    mid = (min(c["x"] for c in active) + max(c["x"] for c in active)) / 2
    lefts = sorted((c for c in active if c["x"] < mid), key=lambda c: c["y"])
    rights = sorted((c for c in active if c["x"] >= mid), key=lambda c: c["y"])
    return lefts, rights


def card_signature(cards):
    """Board state for progress detection.

    A locked pair can keep its labels AND its position (pairs lock in
    place when they were already adjacent), so only the clickable flip
    reveals the match — the label list alone is blind to it.
    """
    return [(c["label"], c["x"], c["y"], c["clickable"]) for c in cards]


def pair_attempt_order(left_label, rights, known_pairs):
    """Order to try right cards for a left card: every instance of the
    known partner label first (labels repeat), each group top to bottom."""
    known = known_pairs.get(left_label)
    if known is None:
        return list(rights)
    return ([r for r in rights if r["label"] == known]
            + [r for r in rights if r["label"] != known])


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


def xpath_literal(s):
    """Quote arbitrary text (with ' or ") for use inside an XPath expression."""
    if "'" not in s:
        return f"'{s}'"
    if '"' not in s:
        return f'"{s}"'
    parts = s.split("'")
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in parts) + ")"
