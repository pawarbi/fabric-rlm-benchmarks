# Draft submission email

The benchmark takes leaderboard submissions by email, not pull request. From
their README: "kindly follow our submission guidelines and email your results to
agenticdatabench@163.com". The guidelines site (agenticdatabench.github.io) did
not resolve when checked, so this follows the README and offers to supply
whatever format they prefer.

**To:** agenticdatabench@163.com
**Subject:** Leaderboard submission: fabric-rlm harness, 47.98% on the public 246

---

Hello,

We would like to submit a result for AgenticDataBench using a harness that is
not yet on your leaderboard.

**fabric-rlm** is an open-source runtime that implements Recursive Language
Models: the model writes Python, the code runs in a real CPython subprocess with
the full data stack available, and the model iterates on its own outputs until it
submits. It was built for Microsoft Fabric notebooks, so it reads and writes a
Lakehouse directly, but it runs anywhere CPython does.

Results on the 246 public tasks, graded by your own comparators via
`da_agent.evaluators.metrics`:

| harness | model | score |
|---|---|---|
| fabric-rlm | MiniMax M3 | 47.98% |
| fabric-rlm | Kimi-K2.5 | 46.89% |

Model spend for those two runs was about $8 and $14 respectively, at list prices.

Two things may be of interest beyond the numbers.

First, what moved them. A plain run scored 43.49% (M3) and 42.11% (Kimi). The
gain came from mechanics rather than analysis: 32% of all zero-scoring tasks had
written no output file at all. Rejecting a submission whose required file is
missing, warning the model when its turn budget is nearly spent, and raising the
worker timeout lifted the share of tasks producing their required output from
89% to 96% (M3) and 89% to 99% (Kimi). The same changes transferred between the
two models almost unchanged (+4.5 and +4.8), which suggests they address a
harness weakness rather than a model quirk.

Second, two notes on the evaluation itself, offered as a contribution rather
than a complaint. The shipped `evaluate.py` cannot run on Windows: it
substitutes file paths into the eval string as a regex replacement template, and
the substituted path then sits inside a Python string literal that is eval'd, so
Windows backslashes raise on both. We verified our own grading loop reproduces
your evaluator's scores task-for-task before relying on it. Separately, in the
`ignore_order=True` path tolerance is applied by snapping values onto a shared
grid, so two values closer together than the tolerance land in different buckets
whenever a grid line falls between them; empirically about half of the offsets
strictly inside the stated tolerance are rejected. This affects 111 of the 140
`compare_csv` tasks in principle. Both are reproducible with the audit scripts
linked below.

Please note our figures cover the 246 public tasks only, not the 344-task set
your published numbers use, so they are not directly comparable as printed. For
the table above we recomputed every published configuration from your per-task
results restricted to the same 246 ids. We are happy to rerun in whatever form
you need for the leaderboard, including on the private set if you run it.

Everything is public and reproducible:

- Results, harness, per-task records, pre-registrations and the evaluator audit:
  https://github.com/pawarbi/fabric-rlm-benchmarks/tree/master/adb
- The library: https://github.com/pawarbi/fabric-rlm-core

Happy to answer any questions or provide trajectories.

Best regards,
Sandeep Pawar
