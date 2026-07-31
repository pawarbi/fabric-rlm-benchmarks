# DataAgentBench campaign record

Everything needed to resume, reproduce, or continue this work if the session dies.
State as of 2026-07-30, mid final run. All paths below are inside this scratchpad
directory unless absolute.

## Goal

Push the fabric-rlm harness as far as possible on DataAgentBench with
minimax-m3 (user's directive: model stays M3; GLM-5.2 etc. only after harness is
maxed; user reviews any model change). Target reference: leaderboard rank 1 is
0.8450 stratified Pass@1 (Fable-5 + Opus-4.7 fallback, hints, tuned prompt).

## Where things live

    C:/Users/sandeeppawar/Downloads/DataAgentBench   benchmark repo, HEAD 9ed8bdde3,
                                                     all 36 data files sha256-verified
    dab_run.py          the runner (prompt arms, verify ensemble, isolation, audit)
    dab_load.py         hub builder (4 store kinds, 3 pg dump dialects)
    dab_export.py       results -> submission JSON + official Pass@1 + integrity
    dab_hubs/           one DuckDB hub + attach.sql per dataset (17 datasets)
    dab_iso/            isolated per-dataset copies served to the model
    dab_<MODE>_results.json / dab_<MODE>_traces/    per-run outputs
    .orkey              OpenRouter key (never commit)
    PREREG_DAB.md       pre-registration for the prompt-rules A/B

## How to run

    MODE=<name> PROMPT=base|treat VERIFY=0|1 RUNS=n WORKERS=n MAX_TURNS=40 \
    TIMEOUT=1200 USE_HINTS=0|1 FABRIC_RLM_TRUNCATION_HINT=on \
    PYTHONIOENCODING=utf-8 python -u dab_run.py [dataset:query ...]

Results are keyed dataset:query:run and resumable: re-running the same MODE skips
completed keys. To redo a subset, delete those records from the results JSON.
Grading is DAB's own per-query validate.py, never a proxy.

## FINAL RESULT (2026-07-30)

    stratified Pass@1 = 0.5902   (170/270 records, 0 leaks, 0 errors, 5 empty)
    submission_final5.json + dab_final5_traces/ (270) are the deliverables.
    Decomposition: harness-correct base ~0.45, verify +0.076, hints ~+0.07 net
    (PANCANCER +0.60, music_brainz -0.23 from the dedup-vs-aggregate hint trap),
    RUNS=5 the rest. Guesstimate 0.63 [0.59-0.66]; landed at band bottom.
    One wedge incident mid-run (timeout does not bound dead LM connections;
    chipped as task_e08ed872), recovered by kill+resume with zero loss.

    Next levers, in order: uniform TIMEOUT=1800 rerun of 5 empty records'
    queries (+0.01-0.02), per-dataset hint override for music_brainz (+0.01),
    Sentinel-style deterministic assertions, then the GLM-5.2 user decision.

## The final run (complete; command kept for reproduction)

    MODE=final5 VERIFY=1 PROMPT=base RUNS=5 USE_HINTS=1 WORKERS=5 TIMEOUT=1200
    -> dab_final5_results.json (270 records target), dab_final5_traces/

If it crashed: rerun the exact command above; it resumes from the JSON. Then:
    python dab_export.py final5
which writes submission_final5.json and prints stratified Pass@1, per-dataset
rates, and integrity counts.

At 71/270 the hard datasets showed: GITHUB 0.50 (= leaders' pinned level),
PANCANCER 0.60 (was 0.00 without hints), PATENTS 0.33, agnews 0.36, DEPS 0.30.

## Trustworthy results (sealed harness, complete data, zero leaks)

    RUNS=3, no hints, 54 scored queries, base prompt:
      control            MACRO 0.4456   (84/162)
      verify ensemble    MACRO 0.5216   (93/162)   delta +0.0761
    Reconciler on decisive pairs (exactly one solver right): 19/28 = 68%
      by type: numeric 15/23, list 2/3, text 2/2
    Both-solvers-wrong pairs rescued by reconciler re-derivation: 13/65 (lists 11/28)
    Agree-verdict precision 33/42 = 79%; verify costs ~2.9x control tokens.
    Flakiness: 17/54 queries flaky, 18 never-pass, 19 always-pass (control arm).
    Single-run numbers ran ~+0.08 optimistic vs RUNS=3; never quote RUNS=1.

Prompt rules (shape/grain + answer contract): unmeasurable at RUNS=1 (three
measurements, three signs); riding along unclaimed. PREREG_DAB.md governs.

## Verify ensemble design (in dab_run.py, VERIFY=1)

Two blind solves, no shared context. Structural agreement check: exact number
match required; semicolon lists need identical item sets (missing member =
disagree); substring/token-overlap for prose. On disagreement, a reconciler in
fresh context gets both answers + the db, instructed to find the divergence
point, not average. All ensemble members go through the leak audit; ensemble
tokens billed on the record. DO NOT add typed reconciliation (numeric tiebreak
replacement): designed, then cancelled when full-arm data showed the reconciler
at 65% on numeric decisive pairs. Early small-sample read (1-for-4) was noise.

## Integrity machinery (both defences required; history proves it)

1. isolate_hub() in dab_run.py: hub + every attached store hardlinked into
   dab_iso/<ds>/ and attach SQL rewritten, so NO benchmark path reaches the
   model. Asserts "DataAgentBench" absent from model-visible SQL.
2. audit_trace(): scans every turn's code for ground_truth / validate.py /
   query.json / withhint / tree-walks; any hit forces the record to fail with
   reason CONTAMINATED and sets rec["leaks"].

History: before these, the model walked from a prompt-visible path to sibling
query folders and read validate.py and gold ("GROUND_TRUTH = 0.1441...",
'gt = "Africa"'). Every pre-seal result was void. Rubric section 2.1 is the
standard; leaders run kernel sandboxes / PreToolUse hooks for the same reason.

## Loader facts (dab_load.py)

Store kinds: sqlite (ATTACH, zero copy), duckdb (ATTACH), postgres (parsed),
mongo BSON (streamed to JSONL -> read_json_auto). THREE pg dump dialects:
plain COPY, COPY WITH (FORMAT CSV) [imdb], INSERT INTO VALUES [crmarenapro
support.sql, cve kev.sql, usaspending contracts.sql]. A store yielding zero
tables raises (silent-empty was how crmarenapro's support store went missing
and voided two full runs). Completeness audit: every db_config store must
appear as a database/schema in the hub; all 17 datasets verified complete.
Schema dump groups tables by identical column signature (stockmarket: 2,754
tables -> ~1k chars instead of 400k chars/turn).

## Known truths for interpretation

- ~6 queries are unsolvable-as-graded (all 7 systems + our 4-model probe miss
  identically): DEPS:1, PANCANCER:1 among them. Ceiling ~0.85.
- Leaders' common technique = independent verification (Sentinel verify_step,
  Sarvam blind second solver, Spacedock verify stage). Ours implements it.
- Model probe on 5 hard queries (same prompt): M3 2/5, GLM-5.2 3/5, Kimi-K3
  2/5, Grok-4.5 2/5. GLM decision awaits user review AFTER harness is maxed.
- Monitor pattern for long ensembles: a verify record can take ~60 min
  legitimately (3 solves x 1200s); stall alerts under that are usually workers
  CPU-bound in local DuckDB on PATENTS (verify with process CPU deltas, not
  API spend, which reads zero during local compute).

## Clean-room constraint (user directive, 2026-07-31)

Do NOT read LabRat's source (github.com/esagduyu/labrat) or copy its
implementation. dab_carto.py was built solely from the public PR #72 disclosure
before the repo was found, and evolves only from our own trace evidence.
Public leaderboard PR text remains fair reference; source code does not.

## Session lessons already banked to memory

Read raw records before trusting aggregates; pre-register before drafting
fixes; tests must import the artifact, not restate it (the norm NameError);
integrity disclosures by competitors are requirements, not boilerplate.

## Remaining after final5

1. dab_export.py final5 -> number + submission artifacts.
2. Full analysis: hints attribution (vs 0.5216 no-hints), per-dataset gaps,
   reconciler stats at RUNS=5.
3. User decisions: submit to leaderboard? try GLM-5.2 on the maxed harness?
4. Backlog: crmarenapro entity-precision failures (found X expected Y);
   agnews still capped ~0.5 even for leaders; deterministic verify_step layer
   (Sentinel-style row-count/range assertions) as the next harness lever.
