# Pre-registration: skills as cards on SpreadsheetBench

Fixed before running.

## Why this benchmark

Every published fabric-rlm result loads skills explicitly (`skills=[...]`), never
by autoloading, so `skills_as_cards` is only meaningful on the explicit path.
SpreadsheetBench is the benchmark where a skill does the most work: `excel_modify`
is the single playbook driving the published 82.25%. If cards degrade guidance
anywhere, they degrade it here. AgenticDataBench cannot test this at all -- those
runs used no skills.

## Arms

Full Verified-400, six shards, MiniMax M3 pinned to the `minimax/fp8` provider,
temperature 0, `max_turns=14`, `timeout=300`. Task text, workbook structure
context, the `sanity` validator and cell-exact grading are taken verbatim from
the notebook that produced the published number.

- **body** — `skills=['excel_modify']`. Reproduces the published configuration
  on the current library.
- **cards** — the same plus `skills_as_cards=True`: the skill is advertised as a
  one-line card and the model calls `load_skill('excel_modify')` if it wants the
  body.

Only that one kwarg differs.

## Two questions, not one

1. **Regression check.** The published 82.25% was measured on fabric-rlm 0.2.8.
   The `body` arm runs the same configuration on 0.3.2 plus this session's
   changes. A large drop would mean the library regressed, independent of cards.
2. **Feature check.** `cards` against `body`, paired per question.

## Decision rule

Pass/fail per question, so a paired McNemar test on discordant pairs.

- **Adopt cards** if accuracy is not significantly worse (McNemar p > 0.05) and
  prompt tokens drop by at least 30%.
- **Reject** if accuracy is significantly worse.
- **Not measured** otherwise, and say so rather than reporting a direction.

For the regression check: flag if the `body` arm falls more than 5 points below
82.25%, which is roughly twice the +/-1.9 point standard error at n=400.

## Stated in advance

n=400, binary outcome, baseline 82.25%. The standard error on a single arm is
about 1.9 points. McNemar's power depends on discordant pairs, not n, so if the
two arms disagree on very few questions the honest answer is "no measurable
difference" rather than "equivalent".

Prompt-size effect measured separately and deterministically: for one task the
skill body is 19,606 prompt chars against 4,945 with cards, a 75% reduction
before any per-turn multiplier.

No post-hoc subgroup will be used to rescue a null result. The Sheet-Level split
(24% failure rate against 15% for Cell-Level) is noted in advance as the place
degradation would show first, and will be reported either way.
