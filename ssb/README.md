# SpreadsheetBench

Harness for running fabric-rlm against SpreadsheetBench Verified-400, plus the
paired A/B analysis used for the arm comparisons below.

## Layout

    harness/run_ssb.py          arm-selectable, sharded runner
    harness/analyze.py          skip-aware McNemar / Wilson for a paired A/B
    harness/analyze_deepseek.py the same, for a model swap rather than an arm swap
    PREREG_CARDS.md             pre-registration for the skills_as_cards A/B
    PREREG_LUNA.md              pre-registration for the GPT-5.6-Luna pilot

## Running

    python run_ssb.py --data <spreadsheetbench_verified_400> --outdir out \
        --arm cards --shard 0 --nshards 6 \
        --model minimax/minimax-m3 --timeout 900

Six shards is the usual split. `--only-ids` restricts to a task list, which is
how the paired pilots were run against the same ids as an earlier arm.

## Results

All paired on `question_id`, cards arm unless stated.

| comparison | result |
| --- | --- |
| regression check vs published 82.25% | **83.80%**, no regression |
| body vs cards (M3, n=395) | 83.80% vs 83.29%, McNemar p=0.897, **-39.3% prompt tokens** -> ADOPT |
| M3 vs DeepSeek V4 Flash high (n=395) | 83.29% vs 83.04%, p=1.0000, **cost 0.59x** |
| GPT-5.6 Luna at max reasoning | stopped at n=17: -5.9 points, 3.9x tokens, 6.2x cost |

## Things that will bite you

**The published 82.25% did not come from the repro notebook.** The results file
that produced it carries a different git commit than the notebook declares.
Anyone rerunning the notebook gets a slightly different number than the README
advertises.

**Five of the 400 tasks ship no workbook in this extract.** They are recorded as
skipped, not failed. Counting them as failures understates both arms and compares
against a different denominator than the published figure. `analyze.py` drops
them; do the same in anything new.

**A dead API key looks exactly like a model regression.** An expired key once
produced 37 rows recorded as `passed: False` and moved both arms by about four
points before anyone noticed. `run_ssb.py` now halts on `AuthenticationError` /
`RateLimitError` rather than recording the failures as wrong answers. Keep that.

**Run-to-run noise is larger than most effects you will chase.** Two runs of an
identical configuration differed by 0.101. A "skill effect" of -0.055 was
published internally before a same-config control showed it was noise, and a
mechanism had already been invented to explain it. Pre-register the decision rule
and run the control before believing any delta.

**A small pilot will overstate an effect, monotonically.** DeepSeek's paired
delta against M3 by sample size: +11.8 (n=17), +13.9 (n=36), +5.0 (n=60),
**-0.25 (n=395)**. A screen answers "is this worth an hour", never "is this
better".

**`parse_pos` in the repro notebook mishandled a trailing unmatched quote**,
collapsing multi-range answer positions into one unparseable range. Fixed in
pawarbi/fabric-rlm-core#24. Note openpyxl has never accepted multi-area strings,
so do not pin openpyxl over this. And `SSB_130-9`'s position parses to one part
correctly: its sheet is literally named `b2b, sez, de`.

## Cost

A full 400-task arm is about $2.60 and 20 minutes over six shards on M3. DeepSeek
V4 Flash at high effort costs less but took roughly three hours for the same
work, so budget on wall clock rather than price.
