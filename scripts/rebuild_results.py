"""Rebuild a run's results.jsonl from its traces and saved outputs.

Concurrent writes to results.jsonl corrupt it: two processes appending to the
same handle interleave mid-line, and every affected row is lost. That happened
twice in this project, each time because a relaunch raced a process that had
not actually exited.

Traces do not have this problem -- one file per task, written once -- so a
corrupted results file is recoverable. This regrades each task from its saved
work directory and rewrites results.jsonl from the trace metadata, with no
model calls.

    python _local/scripts/rebuild_results.py --run fg-final
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_aida import BENCH, SPLIT, grade, split_files  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    args = ap.parse_args()

    run_root = BENCH / "runs" / args.run
    traces = sorted((run_root / "traces").glob("*.json"))
    if not traces:
        print(f"no traces under {run_root/'traces'}", file=sys.stderr)
        return 1

    meta = {
        str(r["id"]): r
        for r in (
            json.loads(line)
            for line in (SPLIT / "file_generation_en.jsonl").open(encoding="utf-8")
        )
    }

    rows, missing = [], 0
    for tf in traces:
        try:
            t = json.loads(tf.read_text(encoding="utf-8"))
        except Exception:
            missing += 1
            continue
        qid = t.get("id")
        rec = meta.get(qid)
        if rec is None:
            missing += 1
            continue
        work = run_root / "work" / qid
        rdir = SPLIT / "reference" / qid
        outs, refs = split_files(rec["output_file"]), split_files(rec["reference_file"])

        passed, matched, total, gerr = True, 0, 0, None
        for i, o in enumerate(outs):
            produced = work / o
            ref = rdir / (refs[i] if i < len(refs) else refs[0])
            if not ref.is_file():
                continue
            if not produced.is_file():
                passed = False
                gerr = gerr or f"output file not created: {o}"
                continue
            ok, m, tot, e = grade(produced, ref, rec["eval_area"], work, rdir)
            passed = passed and ok
            matched += m
            total += tot
            gerr = gerr or e
        if total == 0 and gerr is None:
            passed, gerr = False, "no gradable output/reference pair"

        cfg = t.get("config") or {}
        tot_ = t.get("totals") or {}
        rows.append({
            "id": qid,
            "passed": bool(passed),
            "cells_matched": matched,
            "cells_total": total,
            "grade_err": gerr,
            "run_err": t.get("failure_reason"),
            "n_inputs": len(split_files(rec["input_file"])),
            "n_turns": t.get("n_turns") or 0,
            "prompt_tokens": tot_.get("prompt_tokens") or 0,
            "completion_tokens": tot_.get("completion_tokens") or 0,
            "elapsed_seconds": round(float(tot_.get("lm_seconds") or 0)
                                     + float(tot_.get("worker_seconds") or 0), 2),
            "model": cfg.get("model", ""),
            "skill": cfg.get("skill", ""),
            "rebuilt_from_trace": True,
        })

    old = run_root / "results.jsonl"
    if old.is_file():
        old.rename(run_root / "results.jsonl.corrupt")
    with old.open("w", encoding="utf-8") as fh:
        for r in sorted(rows, key=lambda r: r["id"]):
            fh.write(json.dumps(r) + "\n")

    n = sum(1 for r in rows if r["passed"])
    print(f"rebuilt {len(rows)} rows from {len(traces)} traces ({missing} unusable)")
    print(f"  cell-exact: {n}/{len(rows)} = {100*n/max(len(rows),1):.1f}%")
    print(f"  -> {old}  (previous file kept as results.jsonl.corrupt)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
