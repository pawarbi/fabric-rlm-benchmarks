# Pre-registration: Cartographer-lite probe (written before launch)

Baseline: luna5 (0.6957 official). Probe set: the 10 queries on 3 datasets where
LabRat's map beat us hardest and the mechanism matches (joins/grain/dimensions):
music_brainz 1-3, googlelocal 1-4, yelp 2,3,7.

luna5 same-query rates: mb 0.60ds, gl 0.70ds, yelp q2 1/5, q3 4/5, q7 3/5.
Pooled baseline on the 10 queries: 30/50 records = 0.60.

Change: CARTO=1 appends the mechanical data map to the task. Everything else
identical to luna5 (v1 ensemble, hints, EFFORT=max, TIMEOUT=1200->1800 NO --
keep 1200 to match luna5 exactly). RUNS=3 per query = 30 records, ~$5.

Decision rule, fixed now:
  pooled >= +0.10 vs the same queries' luna5 rate -> build all 12 maps, full run
  +0.05..0.10 -> extend probe to 6 more queries before deciding
  < +0.05 -> drop; the map does not transfer through our harness

Known bias: these 10 queries were CHOSEN because maps should help them, so a win
here overstates the full-benchmark effect; the full-run projection discounts to
the affected-dataset share (~25-35% of the delta seen here).

# Extension: delivery A/B (written before ctx3 launched)

Arm C (ctx3): SAME mechanical content as carto3, delivered as a sandbox file the
model queries on demand (data_context prototype). CARTO off, CTX on. All else
identical to carto3 and luna5.

Gates against the SAME 10 queries:
  ctx3 >= carto3 +0.10 AND >= luna5 +0.10 -> delivery thesis CONFIRMED; design
    data_context as a fabric-rlm feature with this measurement as its basis.
  ctx3 ~ carto3 ~ luna5 -> delivery is not the differentiator on this loop;
    the LabRat delta is elsewhere (toolbelt/ledger); close the direction.
Note: mechanism check required from traces -- at least half the ctx3 records
must actually LOAD the context (json.load visible in code) or the arm is void.
