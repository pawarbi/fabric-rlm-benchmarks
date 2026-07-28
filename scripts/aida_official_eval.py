"""Score a fabric-rlm AIDABench run with AIDABench's OWN evaluator, unmodified.

Their evaluator is an agent (ClaudeSubprocessAgent, up to 30 rounds) that
executes Python to inspect both workbooks directly. My judge_aida.py sends a
60-row preview in one shot, so its numbers are measured on a materially weaker
instrument than the one that produced their published table.

This runs theirs instead. Nothing is added to fabric-rlm -- their repo is cloned
under _local/bench/aida/AIDABench and driven as an external tool.

Two adaptations, neither touching their code:

* Their loader takes a JSONL directly, so we emit one containing the dataset
  rows for exactly the tasks we ran.
* Reference paths are made absolute, which makes their data_root resolution
  (which expects data/file_generation/<dataset>/reference/...) a no-op rather
  than something we have to mirror on disk.

Their agent uses the Anthropic SDK, not an OpenAI-compatible client. OpenRouter
serves the Anthropic Messages API at https://openrouter.ai/api (note: no /v1 --
the SDK appends it) and accepts their exact model string, so the same key works.

    OPENROUTER_API_KEY=... python _local/scripts/aida_official_eval.py --run aida-ctrl
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Their evaluator agent refuses to read files under a OneDrive path ("System
# security restrictions prevent access to files in OneDrive directory"), and
# this repo lives under one. AIDA_EVAL_ROOT points at a plain-filesystem copy;
# without it the agent fails every task it cannot open and the score is noise.
_ALT = os.environ.get("AIDA_EVAL_ROOT")
BENCH = Path(_ALT) if _ALT else ROOT / "_local" / "bench" / "aida"
SPLIT = BENCH / "data" / "file_generation_en"
REPO = BENCH / "AIDABench"

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="run name under _local/bench/aida/runs")
    ap.add_argument("--evaluator-model", default="claude-sonnet-4-5-20250929")
    ap.add_argument("--base-url", default="https://openrouter.ai/api")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="0 = all tasks in the run")
    ap.add_argument("--sample", type=int, default=0,
                    help="evaluate a random N-task sample instead of all (for calibration)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("OPENROUTER_API_KEY not set", file=sys.stderr)
        return 2
    if not REPO.is_dir():
        print(f"AIDABench repo not cloned at {REPO}", file=sys.stderr)
        return 2

    run_root = BENCH / "runs" / args.run
    results = run_root / "results.jsonl"
    if not results.is_file():
        print(f"no results at {results}", file=sys.stderr)
        return 2

    ran = [json.loads(line)["id"] for line in results.open(encoding="utf-8")]
    meta = {
        str(r["id"]): r
        for r in (
            json.loads(line)
            for line in (SPLIT / "file_generation_en.jsonl").open(encoding="utf-8")
        )
    }

    if args.sample:
        import random
        random.Random(args.seed).shuffle(ran)
        ran = ran[: args.sample]
        print(f"random sample of {len(ran)} tasks (seed {args.seed})")

    rows = []
    for qid in ran:
        rec = meta.get(qid)
        if rec is None:
            continue
        row = dict(rec)
        # Absolute reference paths short-circuit their data_root lookup.
        first_ref = str(rec["reference_file"]).split("\n")[0].strip()
        row["reference_file"] = str(SPLIT / "reference" / qid / first_ref)
        rows.append(row)
        if args.limit and len(rows) >= args.limit:
            break

    inp = run_root / "official_eval_input.jsonl"
    with inp.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{len(rows)} tasks -> {inp}")

    out = run_root / "official_eval"
    out.mkdir(exist_ok=True)
    cmd = [
        sys.executable,
        "-u",
        str(REPO / "evaluation" / "run.py"),
        "--dataset", "file_generation_en",
        "--model_name", f"fabric-rlm-{args.run}",
        "--input_path", str(inp),
        "--generated_files_dir", str(run_root / "work"),
        "--output_path", str(out / "result.jsonl"),
        "--data_root", str(BENCH / "data"),
        "--api_key", key,
        "--base_url", args.base_url,
        "--evaluator_model", args.evaluator_model,
        "--max_workers", str(args.workers),
    ]
    print(f"running their evaluator: {args.evaluator_model} via {args.base_url}\n")
    # Their evaluation.config eagerly requires every evaluator's env vars at
    # import time, including the chart and numerical ones this run never uses.
    # Supply them all rather than patch their code -- the point is to run it
    # unmodified. FILE_GENERATION_* are the only ones that do any work here.
    env = dict(
        os.environ,
        PYTHONPATH=str(REPO),
        PYTHONIOENCODING="utf-8",
        FILE_GENERATION_EVAL_API_URL=args.base_url,
        FILE_GENERATION_EVAL_API_KEY=key,
        FILE_GENERATION_EVAL_MODEL_NAME=args.evaluator_model,
        CHART_EVAL_API_URL=args.base_url,
        CHART_EVAL_API_KEY=key,
        CHART_EVAL_MODEL_NAME="google/gemini-3-pro-preview",
        NUMERICAL_EVAL_API_URL=args.base_url,
        NUMERICAL_EVAL_API_KEY=key,
        NUMERICAL_EVAL_MODEL_NAME="qwen/qwq-32b",
    )
    proc = subprocess.run(cmd, cwd=str(REPO), env=env)

    summary = out / "summary.json"
    if summary.is_file():
        data = json.loads(summary.read_text(encoding="utf-8"))
        print("\n" + "=" * 58)
        print(f"OFFICIAL AIDABench evaluator, run {args.run}")
        for k, v in data.items():
            print(f"  {k}: {v}")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
