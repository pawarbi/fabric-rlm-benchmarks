# fabric-rlm on DataAgentBench

Reproduction material for running fabric-rlm on
[DataAgentBench](https://github.com/ucbepic/DataAgentBench): harness source,
run configs, all results including ablations and negative results, and full
per-trial traces.

## Headline results

Both runs use the library unchanged from PyPI, the official validators
unmodified, hints enabled, five trials per query, and the ensemble described
below. No benchmark-tuned prompt.

| Backbone | Stratified Pass@1 | Cost | Records |
|---|---|---|---|
| GPT-5.6 Luna (effort max) | 0.6957 | ~$16 | runs/fabric-rlm-luna_results.json |
| MiniMax M3 | 0.5902 | ~$16 | runs/submission_final5.json |

Traces for every trial: `traces/` (task text, per-turn code, stdout, tokens,
ensemble verdicts). Zero integrity flags across ~1,400 audited solver runs.

## Harness

`harness/dab_run.py` and `harness/dab_load.py` are the whole thing, about 600
lines. Per trial: consolidate the dataset's sanctioned stores into one DuckDB
file (SQLite and DuckDB attached zero-copy, three PostgreSQL dump dialects and
MongoDB BSON materialized), solve the task twice in blind fresh contexts,
compare answers structurally in code, and reconcile disagreements in a third
fresh context that must re-derive from the database.

Integrity machinery, both mandatory: isolation (the model never sees a
benchmark path; stores are hardlinked into bare directories and attach SQL is
rewritten) and a mechanical per-trace audit that fails any record touching
ground truth, validators, or the benchmark tree. History behind both is in
DAB_CAMPAIGN.md; early un-sealed runs were voided and rerun.

## Ablations and negative results

Measured, pre-registered (PREREG_*.md), all results files in `runs/`:

- Blind double-solve plus reconciler: +0.076 stratified Pass@1 at 2.9x tokens
  (RUNS=3 A/B, no hints; dab_v3off vs dab_v3on).
- Prompt rules (result shape and grain guidance): unmeasurable at single runs,
  three measurements with three signs. Excluded from claims.
- Chain-of-Verification gate on agreements: net negative, overrode correct
  answers on its own buggy checks (dab_lunav2, stopped early per prereg).
- Mechanical data maps (grain, verified join keys, dimension values), pasted
  into the prompt and delivered as a queryable file: both null to negative
  (dab_carto3, dab_ctx3). Facts without owner semantics reshuffle
  interpretations rather than fixing them.
- Single-run scores ran about +0.08 optimistic versus RUNS=3 on the same
  config. Nothing here is quoted from a single run.

The distilled finding: on a benchmark the answer-generating semantics are
hidden, so authored context cannot exist and mechanical context does not help.
On owned data the same mechanism works: in a controlled test with invented org
semantics (fiscal calendar, exclusion codes, decoy join keys), an authored
context file moved lore-dependent questions from 0/12 to 12/12 while cutting
tokens 40 percent. That test is the ctxtest material in this directory's
campaign notes.

## Reproduce

DAB data comes from the benchmark's own `download.sh`. Then:

    MODE=run1 MODEL=openrouter/openai/gpt-5.6-luna EFFORT=max VERIFY=1 \
    PROMPT=base RUNS=5 USE_HINTS=1 WORKERS=5 MAX_TURNS=40 TIMEOUT=1200 \
    FABRIC_RLM_TRUNCATION_HINT=on python -u harness/dab_run.py

Results are keyed and resumable. Grade with `harness/dab_export.py run1`.
