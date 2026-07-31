# Pre-registration: DAB answer-contract and grain rules

Written after the baseline and before the treatment run.

## Baseline, established

    model    openrouter/minimax/minimax-m3
    config   no hints, MAX_TURNS=40, schema pre-listed, skills=[data_exploration]
    grading  DAB's own validate.py, unmodified
    result   MACRO 0.4136, micro 26/54, 0 errors, 0 empty, 0 turn-cap hits
             12.8M tokens, mean 12.9 turns

Reference points, same validators, official macro metric:

    rank 1 Sentinel      0.8450     react_gemini-3-pro      0.4546
    rank 2 Alkera        0.8328     react_gpt-5-mini        0.3647
    rank 3 Sarvam        0.8208     react_gpt-5.2           0.2991
    claude-opus-4-6      0.5468     react_gemini-2.5-flash  0.1041

## The ceiling this benchmark actually has

The top three, built independently, post near-identical per-dataset profiles. Four
datasets sit at exactly the same fraction for all three: DEPS_DEV_V1 1/2,
GITHUB_REPOS 2/4, PANCANCER_ATLAS 2/3, agnews about 2/4. Roughly 6 queries appear
unsolvable as graded and nobody has beaten them. So ~0.85 is the practical maximum,
and the whole rank-1-to-rank-3 spread comes from crmarenapro and agnews.

Seven datasets are provably reachable at 1.00, since all three top systems get them.
That is where the available points are.

## Failure composition of the baseline, from reading all 28

    10 (18.5%)  incomplete enumeration, across 8 of 12 datasets
     7 (13.0%)  wrong value
     5 ( 9.3%)  wrong entity, exact id (all crmarenapro)
     3 ( 5.6%)  wrong entity, fuzzy
     3 ( 5.6%)  proximity: value present but too far from its label

## Change under test

Two additions to the task prompt, both generic and naming no dataset, value or key:

  * Shape and grain: answer at the grain asked; a per-group superlative does not
    collapse to a global winner; count reported rows against distinct qualifying
    groups; sanity-check join cardinality; re-run the final query before stating it.
  * Answer contract: complete enumeration, semicolon-separated, sorted, no
    truncation, no "top results" subset; value adjacent to its label.

Provenance matters here. The enumeration contract comes from Alkera's published
METHODS.md, and the verification layer from Sentinel's disclosed verify_step, both
written before this baseline existed. Our failure analysis independently found
enumeration to be the largest bucket. That is convergent evidence, not a rule
reverse-engineered from our own errors.

## Endpoint and honesty constraint

Primary: MACRO on the same 54 queries versus 0.4136.

That number is OPTIMISTIC by construction: the rules were written knowing which
failures occurred on these queries. It measures whether the fix addresses the
diagnosed mechanism, not whether it generalises.

HELD OUT: the 5 unscored datasets (civic_unstructured, cve, imdb, krama,
usaspending) are 50 further queries with validators and ground truth that have never
been looked at. Baseline and treatment both get run there. The held-out delta is the
number worth believing; if the two disagree, the held-out one wins.

Earlier in this project a rule written from failures and measured on the same set
gave +7 that did not replicate (held-out net -4, pooled +2/284, p=0.885). This
design exists to avoid repeating that.

## Decision rule, fixed now

  * Held-out delta positive and same sign as the 54-query delta -> keep the rules.
  * Held-out flat or negative -> discard, whatever the 54-query number says.
  * Report both numbers always. Never quote the 54-query delta alone.

## Model

minimax-m3 throughout this comparison, so prompt effects are not confounded with
model changes. GLM-5.2 gets tried only after the mechanical fixes are settled, as a
separate single-variable step, since Sarvam reached 0.8208 with it as a single model
and it therefore isolates "model capability" cleanly.

## Not being changed yet, and why

  * Hints: sanctioned and used by all top submissions, and would likely fix the two
    agnews failures where the agent concluded no category column exists. Held back
    so it stays a separate variable.
  * Blind second solver / review subagents: real technique in two top submissions,
    but expensive and a bigger change. Later.
  * Result materialisation to avoid context blowups: one baseline run burned 918k
    tokens. Cost issue, not accuracy. Later.
