# fabric-rlm on AgenticDataBench

Reproduction material for running [fabric-rlm](https://github.com/pawarbi/fabric-rlm-core)
against [AgenticDataBench](https://github.com/AgenticDataBench/AgenticDataBench)
([arXiv 2607.01647](https://arxiv.org/abs/2607.01647)), the Tsinghua / Ant Group
benchmark of realistic data-analysis tasks.

All 246 public tasks, graded by the benchmark's own comparators. No LLM judge.

## Results

Scores are the benchmark's own `total_score`, which awards partial credit per
matching column, so a task can score 0.6 by getting three of five columns right.

| harness | model | public-246 |
|---|---|---|
| CodeX | Kimi-K2.5 | 49.39% |
| **fabric-rlm** | **MiniMax M3** | **47.98%** |
| Claude Code | Claude 4.6 | 47.77% |
| Smolagents | Qwen3.5 | 47.46% |
| DA-Agent | Kimi-K2.5 | 47.12% |
| Smolagents | Claude 4.6 | 46.97% |
| **fabric-rlm** | **Kimi-K2.5** | **46.89%** |
| DA-Agent | Claude 4.6 | 46.32% |
| Claude Code | Kimi-K2.5 | 45.15% |
| Smolagents | Kimi-K2.5 | 44.21% |
| DA-Agent | Qwen3.5 | 43.74% |
| Claude Code | Qwen3.5 | 40.54% |
| CodeX | Qwen3.5 | 39.22% |
| CodeX | Claude 4.6 | 31.83% |

**Comparability.** The paper's published figures cover 344 tasks (these 246
public ones plus 98 withheld private ones), so they are not directly comparable
to a public-only number. Every row above is recomputed from the authors' own
per-task results in `testbed/results/*.json`, restricted to the same 246 ids, by
`harness/compare_published.py`. The restriction moves some configurations
noticeably (DA-Agent + Kimi goes 44.83% to 47.12%).

Model spend was about $7 for the M3 run and about $14 for Kimi, against a
published range of roughly $18 to $530.

## What made the difference

The first run of the harness scored 43.49%. The gain came from four mechanical
changes, none of which touch the library:

| configuration | M3 | Kimi |
|---|---|---|
| plain `RLM.task` | 43.49% | 42.11% |
| plus the four fixes below | **47.98%** | **46.89%** |
| paired delta | +4.49 pts, 95% CI [+0.26, +8.71] | +4.77 pts, 95% CI [+0.28, +9.26] |

1. **`output_validator`** rejecting a SUBMIT whose required file is missing,
   empty or unparseable, which the runtime turns into a repair turn.
2. **`reserve_finalize_turns=2`**, warning the model when the turn budget is
   nearly spent so it produces something rather than ending empty.
3. **Worker timeout raised to 1800s**, because a single `WorkerTimeout` ends the
   whole run and discards every prior turn.
4. **Pre-installing libraries the generated code reaches for** (statsmodels,
   geopandas, dbfread, lightgbm, pyshp). The sandbox cannot `pip install`, and
   `ModuleNotFoundError` hit 37 tasks.

The mechanism is unglamorous: 32% of all zero scores were tasks that wrote **no
file at all**. Completion went from 89% to 96% of tasks producing their required
outputs. The same stack, designed from M3's failures, transferred to Kimi almost
unchanged (+4.49 against +4.77), which suggests it addresses a harness weakness
rather than a model quirk.

## What was tried and rejected

Each measured against the same baseline on a stratified 40-task sample, with the
decision rule fixed in advance (`PREREG_STACK.md`).

| change | score effect | token cost |
|---|---|---|
| inject schema previews of every input | -0.04 [-0.18, +0.10] | +32% |
| enable skill autoloading and the router | -0.07 [-0.17, +0.03] | +171% |
| blind double-solve with reconciliation | -0.04 [-0.14, +0.07] | 2.85x solves |

None improved the score and all cost more. The ensemble additionally failed its
own precondition: two blind attempts produced comparable files only 15% of the
time, so agreement carried no signal and nearly every task paid for a third
reconciliation run. That technique earns its keep when the answer is a short
determinate value, not an eight-column table of computed floats.

An earlier `output_contract` skill was also tested and withheld; see
`PREREG_OUTPUT_CONTRACT.md`. Its apparent effect was inside the noise floor.

## Noise

Four identical-configuration runs on the same ten tasks scored 0.551, 0.450,
0.590 and 0.490. Per-task standard deviation is 0.144, and pinning temperature
to 0 did not reduce it. At n=246 the standard error of the mean is about 0.019,
so treat differences under roughly four points as unresolved. This is why the
40-task arms above return "no evidence" rather than a direction.

## Grading

`harness/grade_pilot.py` evaluates each task's own `eval_func` against
`da_agent.evaluators.metrics`, so the scores come from the benchmark's code.

It exists because the shipped `evaluate.py` cannot run on Windows: it
substitutes file paths into the eval string as an `re.sub` replacement template
(backslashes become escapes), and the substituted path then sits inside a Python
string literal that is `eval()`d (`\U` becomes a unicode escape). Ours
substitutes `repr(path)` instead. Equivalence was verified task-by-task against
the stock evaluator on both arms with `harness/verify_grader_equivalence.py`.

`EVALUATOR_AUDIT.md` records what else the audit found, including that the
`ignore_order=True` path applies tolerance by grid-snapping and therefore
rejects half of the values that sit inside the stated tolerance.

## Reproducing

```bash
git clone --depth 1 https://github.com/AgenticDataBench/AgenticDataBench.git
pip install fabric-rlm jsonlines tqdm fuzzywuzzy python-Levenshtein
pip install statsmodels geopandas dbfread lightgbm pyshp

python harness/fetch_all.py <testbed>            # datasets from HuggingFace
python harness/run_pilot.py --testbed <testbed> --tasks <ids> \
    --outdir <out> --lm openrouter/minimax/minimax-m3 \
    --max-turns 25 --timeout 1800 --temperature 0 --validate-outputs
python harness/grade_pilot.py --testbed <testbed> --outdir <out> --tasks <ids>
python harness/summarize.py <testbed> <out>
```

`harness/run_full.sh` shards the full set for parallelism; `harness/resume.sh`
re-runs only what is missing, which matters because a long run will lose tasks
to timeouts.

A Fabric notebook version is in the library repo at
[`examples/notebooks/agenticdatabench_fabric.ipynb`](https://github.com/pawarbi/fabric-rlm-core/blob/main/examples/notebooks/agenticdatabench_fabric.ipynb).

## Files

- `results/*.json` — grader output per run
- `results/full_stack_*_per_task.jsonl` — per task: score, whether the required
  files were written, turns, and token counts
- `harness/` — runner, grader, sharding, dataset fetcher, analysis and audit scripts
- `PREREG_STACK.md`, `PREREG_OUTPUT_CONTRACT.md` — decision rules fixed before running
- `EVALUATOR_AUDIT.md` — audit of the benchmark's own evaluator

## Caveats

Single seed per configuration. Public 246 only, so not a leaderboard-comparable
figure. Kimi's run has two tasks that never produced output after four retry
passes, making 46.89% very slightly conservative. Costs are list-price estimates
from observed token counts; roughly 87% of prompt tokens were cache hits, so
naive token-count multipliers overstate dollar differences.
