"""Summarize a full AgenticDataBench run: overall, by domain, by grader."""
import json, re, sys
from collections import defaultdict
from pathlib import Path

TESTBED = Path(sys.argv[1])
OUTDIR = Path(sys.argv[2])

tasks = {json.loads(l)["id"]: json.loads(l)
         for l in open(TESTBED / "tasks" / "dev.jsonl", encoding="utf-8")}
grades = {r["id"]: r["score"]
          for r in json.load(open(OUTDIR / "grades.json", encoding="utf-8"))["results"]}

as_list = lambda v: v if isinstance(v, list) else [v]

by_dom = defaultdict(list)
by_grader = defaultdict(list)
produced = missing = 0
for tid, score in grades.items():
    t = tasks[tid]
    by_dom[t["domain"].split("/")[0]].append(score)
    for f in as_list(t["eval_func"]):
        by_grader[f.split("(")[0]].append(score)
    outs = as_list(t["output_file_name"])
    if all((OUTDIR / tid / o).exists() for o in outs):
        produced += 1
    else:
        missing += 1

n = len(grades)
mean = sum(grades.values()) / n
solved = sum(1 for s in grades.values() if s >= 0.999)
zero = sum(1 for s in grades.values() if s <= 0.001)

print(f"tasks graded          : {n} / {len(tasks)}")
print(f"produced all outputs  : {produced}   (no output: {missing})")
print(f"MEAN SCORE            : {mean:.4f}")
print(f"fully solved (=1.0)   : {solved} ({100*solved/n:.1f}%)")
print(f"scored zero           : {zero} ({100*zero/n:.1f}%)")
print(f"partial credit        : {n-solved-zero} ({100*(n-solved-zero)/n:.1f}%)")

print("\nby domain:")
for d, v in sorted(by_dom.items(), key=lambda kv: -sum(kv[1])/len(kv[1])):
    print(f"  {d:18s} n={len(v):3d}  mean={sum(v)/len(v):.3f}")

print("\nby grader (tasks may use several):")
for g, v in sorted(by_grader.items(), key=lambda kv: -len(kv[1])):
    print(f"  {g:24s} n={len(v):3d}  mean={sum(v)/len(v):.3f}")
