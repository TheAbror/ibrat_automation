# Score-floor gate for the accuracy governor

Date: 2026-08-03
Status: approved

## Problem

The accuracy governor (`should_miss` in `main.py`) starts dropping known
answers after a 20-answer warmup, whenever a correct answer would push run
accuracy above `ACCURACY_HIGH` (0.95). Misses are therefore spread across
the whole run, including its earliest stretch — where a banked-correct
count that low cannot yet guarantee any final score.

The course size is known (~88 quizzes × ~10 questions ≈ 880). The run
should never gamble the final score: intentional misses must begin only
once enough correct answers are banked that the final score cannot drop
below 88% even if every remaining question were answered wrong.

## Design

In `main.py`:

- Replace `GOVERNOR_WARMUP = 20` with:
  - `TOTAL_QUESTIONS = 880` — the course size the floor is computed
    against.
  - `SCORE_FLOOR = 0.88` — worst-case final score that must stay secured.
- `should_miss(correct, incorrect)`:
  1. Return `False` while `correct < math.ceil(SCORE_FLOOR *
     TOTAL_QUESTIONS)` (= 775).
  2. Otherwise keep the existing cap check:
     `(correct + 1) / (correct + incorrect + 1) > ACCURACY_HIGH`.
- Rewrite the comment block above the constants to describe the
  floor-then-band behavior.

No other call sites change; the throttle wiring in `auto_answer_loop`
stays as is.

## Behavior

- Every known answer is played straight until 775 correct answers are
  banked; from that point even an all-wrong tail yields ≥ 88%
  (775 / 880).
- After the floor is secured, the existing cap steers the tail so the
  final score settles ≈ 95%, inside the 93–96% band.
- If a run ends before 775 correct (short course, early crash), no
  intentional miss ever happens — the score is simply as good as the
  runner can make it. That is the safe direction for a floor.
- `RUN_STATS` already persists across in-run app restarts, so the floor
  counts the whole run. A fresh process starts from zero, same as today.

## Tests

Update `test_should_miss_keeps_accuracy_inside_the_band` in
`test_watcher.py`:

- Small counts (e.g. `(40, 0)`) no longer trigger misses.
- `(774, 0)` → `False` (floor not yet secured).
- `(775, 0)` → `True` (floor secured, 100% > cap).
- `(775, 70)` → `False` (≈ 91.7%, under cap).
