"""Grade a fabric-rlm QA run with AIDABench's own eval_QA.py, unmodified.

Their QA grader is a single chat completion per task (NumericalEvaluator), not
the 30-round agent used for file generation -- so this costs cents rather than
the ~$125 a full official file-generation evaluation would.

One unavoidable deviation: their configured grader model is `qwq-32b`, which is
no longer served by OpenRouter. A substitute has to stand in, so these numbers
are on their harness and prompt but not their exact judge. Run at least two
different substitute models and compare before quoting anything -- if the
graders disagree materially, the number is about the grader, not the run.

Our results.jsonl already carries every field their evaluator reads (id,
question, reference, answer, model_response, rubrics), so it is fed in directly
with no adapter.

    OPENROUTER_API_KEY=... python _local/scripts/aida_qa_eval.py --run qa-m3 \
        --grader qwen/qwen3-235b-a22b-2507
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_ALT = os.environ.get("AIDA_EVAL_ROOT")
BENCH = Path(_ALT) if _ALT else ROOT / "_local" / "bench" / "aida"
REPO = BENCH / "AIDABench"
RUNS = ROOT / "_local" / "bench" / "aida" / "runs"

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--grader", default="qwen/qwen3-235b-a22b-2507")
    ap.add_argument("--base-url", default="https://openrouter.ai/api/v1")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--tag", default="", help="suffix for the output dir, e.g. the grader name")
    args = ap.parse_args()

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("OPENROUTER_API_KEY not set", file=sys.stderr)
        return 2
    if not REPO.is_dir():
        print(f"AIDABench repo not found at {REPO}", file=sys.stderr)
        return 2

    run_root = RUNS / args.run
    src = run_root / "results.jsonl"
    if not src.is_file():
        print(f"no results at {src}", file=sys.stderr)
        return 2

    # Drop any partial/corrupt line and any row with no answer -- an empty
    # answer is a run failure, and grading it just charges for a certain zero.
    rows, skipped = [], 0
    for line in src.open(encoding="utf-8"):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not (r.get("answer") or "").strip():
            skipped += 1
            continue
        rows.append(r)

    tag = args.tag or args.grader.split("/")[-1]
    out = run_root / f"qa_eval_{tag}"
    out.mkdir(parents=True, exist_ok=True)
    inp = run_root / f"qa_eval_input_{tag}.jsonl"
    with inp.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)} answers to grade ({skipped} skipped: empty or unparseable)")
    print(f"grader: {args.grader}\n")

    cmd = [
        sys.executable, "-u",
        str(REPO / "evaluation" / "run.py"),
        "--dataset", "QA_en",
        "--model_name", f"fabric-rlm-{args.run}",
        "--input_path", str(inp),
        "--output_path", str(out / "result.jsonl"),
        "--api_key", key,
        "--base_url", args.base_url,
        "--max_workers", str(args.workers),
    ]
    # config.py demands every evaluator's env at import; only NUMERICAL_* is used here.
    env = dict(
        os.environ,
        PYTHONPATH=str(REPO),
        PYTHONIOENCODING="utf-8",
        NUMERICAL_EVAL_API_URL=args.base_url,
        NUMERICAL_EVAL_API_KEY=key,
        NUMERICAL_EVAL_MODEL_NAME=args.grader,
        FILE_GENERATION_EVAL_API_URL=args.base_url,
        FILE_GENERATION_EVAL_API_KEY=key,
        FILE_GENERATION_EVAL_MODEL_NAME="claude-sonnet-4-5-20250929",
        CHART_EVAL_API_URL=args.base_url,
        CHART_EVAL_API_KEY=key,
        CHART_EVAL_MODEL_NAME="google/gemini-3-pro-preview",
    )
    proc = subprocess.run(cmd, cwd=str(REPO), env=env)

    # Their runner writes per-task JSON into a directory named result.jsonl.
    res = out / "result.jsonl"
    files = sorted(res.glob("*.json")) if res.is_dir() else []
    scored = []
    for f in files:
        try:
            scored.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    got = [s for s in scored if s.get("score") is not None]
    if got:
        n_pass = sum(1 for s in got if s.get("score"))
        print("\n" + "=" * 58)
        print(f"AIDABench QA_en, run {args.run}, grader {args.grader}")
        print(f"  graded : {len(got)} of {len(rows)} submitted answers")
        print(f"  score  : {n_pass}/{len(got)} = {100*n_pass/len(got):.1f}%")
        print(f"  over all {len(rows) + skipped} tasks: {100*n_pass/(len(rows)+skipped):.1f}%")
        print("\n  paper's Sonnet 4.5 QA pass@1: 68.58%")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
