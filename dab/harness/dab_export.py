"""Export a run's results to DAB leaderboard submission format and score it.

Submission format per the README: a JSON list of {dataset, query, run, answer},
5 runs per query, every query present. Empty answers are submitted as "" rather
than omitted (Sarvam's convention) so coverage stays exactly 270/270.

Also emits the official stratified Pass@1 (mean over datasets of each dataset's
mean per-query pass rate) computed with DAB's own validators, plus the integrity
summary the rubric asks about: leak-audit counts over every record including
ensemble members, and where the traces live.
"""
import collections
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
MODE = sys.argv[1] if len(sys.argv) > 1 else "final5"
SRC = HERE / f"dab_{MODE}_results.json"
OUT = HERE / f"submission_{MODE}.json"

R = json.load(open(SRC, encoding="utf-8"))
sub = [{"dataset": r["dataset"], "query": r["query"], "run": r["run"],
        "answer": r["answer"]} for r in sorted(R, key=lambda x: (x["dataset"], x["query"], x["run"]))]
json.dump(sub, open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)

byq = collections.defaultdict(list)
for r in R:
    byq[(r["dataset"], r["query"])].append(r["passed"])
byds = collections.defaultdict(list)
for (ds, q), ps in byq.items():
    byds[ds].append(sum(ps) / len(ps))
macro = sum(sum(v) / len(v) for v in byds.values()) / len(byds)

print(f"{MODE}: {len(R)} records -> {OUT.name}")
print(f"stratified Pass@1 = {macro:.4f}")
for ds in sorted(byds):
    print(f"  {ds:20} {sum(byds[ds])/len(byds[ds]):.3f}")
print(f"leaked records: {sum(1 for r in R if r.get('leaks'))}")
print(f"empty answers : {sum(1 for r in R if not r['answer'].strip())}")
print(f"errors        : {sum(1 for r in R if r.get('error'))}")
print(f"traces        : dab_{MODE}_traces/ ({len(list((HERE/f'dab_{MODE}_traces').glob('*.json')))} files)")
