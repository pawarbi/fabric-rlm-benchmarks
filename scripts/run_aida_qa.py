"""Run fabric-rlm against AIDABench's QA_en split.

Differs from the file_generation runner in one way that simplifies everything:
the deliverable is a text answer, not a file. No output artifact, no eval_area,
no sheet resolution -- the whole class of grader bugs that plagued file
generation does not exist here.

All 226 tasks are "Numerical Statistics": compute something over one or more
workbooks and report the figures. 47 carry `rubrics` naming the points that must
be covered; the rest are graded against `reference` alone.

Grading is AIDABench's own eval_QA.py, run separately by aida_qa_eval.py.

    OPENROUTER_API_KEY=... python _local/scripts/run_aida_qa.py --limit 226
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
import traceback
from pathlib import Path

import dspy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# The split is translated from Chinese; file names still carry CJK characters
# and a Windows cp1252 console will kill an hour-old run on one progress line.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from fabric_rlm import File, RLM, SkillLoader  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_aida import PREVIEW_ROWS, preview_inputs, split_files  # noqa: E402
from aida_trace import save_trace  # noqa: E402

BENCH = ROOT / "_local" / "bench" / "aida"
SPLIT = BENCH / "data" / "QA_en"

TASK_TMPL = """{question}

Working directory: {work}
The input files listed below are already in that directory.

## Input files (first {n} rows of each, for orientation)

{preview}

Row counts above are the full file; only the first rows are shown. Read the
files yourself for anything beyond this.

Report the figures the question asks for. Show the values, not a description of
how to compute them. If the question asks for several statistics, give every one
of them, clearly labelled.

Call SUBMIT(answer="<your findings>") when done."""



def acquire_run_lock(run_root: Path) -> Path | None:
    """Refuse to start if another process is already writing this run.

    Two processes appending to the same results.jsonl interleave mid-line and
    every affected row is lost. This happened twice here, each time because a
    relaunch raced a process that had not actually exited. A stale lock from a
    crash is reported with its pid so it can be cleared deliberately.
    """

    lock = run_root / ".run.lock"
    if lock.exists():
        try:
            owner = lock.read_text(encoding="utf-8").strip()
        except Exception:
            owner = "unknown"
        print(f"ERROR: {run_root.name} is locked by pid {owner}.", file=sys.stderr)
        print("  Another run is writing here. Wait for it, or if it is dead:", file=sys.stderr)
        print(f"    rm {lock}", file=sys.stderr)
        return None
    run_root.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()), encoding="utf-8")
    return lock


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=226)
    ap.add_argument("--model", default="openrouter/minimax/minimax-m3")
    ap.add_argument("--skill", default="data_exploration")
    ap.add_argument("--max-turns", type=int, default=14)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--max-tokens", type=int, default=16000)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--provider-order", default="minimax/fp8")
    ap.add_argument("--run-name", default="qa-m3")
    ap.add_argument("--ids", default="")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY not set", file=sys.stderr)
        return 2

    rows = [json.loads(l) for l in (SPLIT / "QA_en.jsonl").open(encoding="utf-8")]
    ready, skipped = [], []
    for r in rows:
        idir = SPLIT / "input" / r["id"]
        names = split_files(r["input_file"])
        if not idir.is_dir() or not all((idir / n).is_file() for n in names):
            skipped.append(r["id"])
            continue
        ready.append(r)
    print(f"{len(ready)} of {len(rows)} tasks runnable")
    if skipped:
        print(f"  excluded (missing_files): {len(skipped)} -> {', '.join(skipped[:12])}")

    if args.ids:
        want = {i.strip() for i in args.ids.split(",") if i.strip()}
        ready = [r for r in ready if r["id"] in want]

    run_root = BENCH / "runs" / args.run_name
    work_root = run_root / "work"
    work_root.mkdir(parents=True, exist_ok=True)
    lock = acquire_run_lock(run_root)
    if lock is None:
        return 2
    results_path = run_root / "results.jsonl"

    done: set[str] = set()
    if args.resume and results_path.is_file():
        for line in results_path.open(encoding="utf-8"):
            try:
                done.add(json.loads(line)["id"])
            except Exception:
                continue
        ready = [r for r in ready if r["id"] not in done]
        print(f"resuming: {len(done)} done, {len(ready)} remaining")

    todo = ready[: args.limit]
    if not todo:
        print("nothing to run")
        return 0

    lm = dspy.LM(
        args.model,
        api_key=os.environ["OPENROUTER_API_KEY"],
        api_base="https://openrouter.ai/api/v1",
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        extra_body={"usage": {"include": True}}
        | (
            {"provider": {"order": args.provider_order.split(","), "allow_fallbacks": False}}
            if args.provider_order
            else {}
        ),
    )
    local_skills = ROOT / "_local" / "skills"
    loader = SkillLoader(skill_dir=str(local_skills)) if local_skills.is_dir() else SkillLoader()

    with results_path.open("a" if args.resume else "w", encoding="utf-8") as out:
        for i, rec in enumerate(todo, 1):
            qid = rec["id"]
            try:
                names = split_files(rec["input_file"])
                work = work_root / qid
                # ignore_errors: on Windows a stale handle from another
                # process makes rmtree raise, which would fail the task for a
                # reason that has nothing to do with the model.
                if work.exists():
                    shutil.rmtree(work, ignore_errors=True)
                work.mkdir(parents=True, exist_ok=True)
                for n in names:
                    shutil.copy2(SPLIT / "input" / qid / n, work / n)

                task = TASK_TMPL.format(
                    question=rec["question"].strip(),
                    work=str(work),
                    n=PREVIEW_ROWS,
                    preview=preview_inputs(work, names),
                )
                inputs = {
                    re.sub(r"\W+", "_", Path(n).stem)[:40] or f"file{j}": File(str(work / n))
                    for j, n in enumerate(names)
                }
                t0 = time.perf_counter()
                rlm = RLM.from_task(
                    task=task,
                    inputs=inputs,
                    outputs=["answer"],
                    lm=lm,
                    skill_loader=loader,
                    skills=[args.skill],
                    max_turns=args.max_turns,
                    timeout=args.timeout,
                )
                result = rlm.run()
                elapsed = time.perf_counter() - t0
                save_trace(
                    run_root / "traces" / f"{qid}.json",
                    task_id=qid, task_text=task, result=result,
                    config={"model": args.model, "skill": args.skill,
                            "max_turns": args.max_turns, "temperature": args.temperature,
                            "split": "QA_en"},
                )
                payload = result.payload or {}
                answer = payload.get("answer")
                answer = "" if answer is None else str(answer)
                row = {
                    "id": qid,
                    "answer": answer,
                    "model_response": answer,
                    "reference": rec.get("reference"),
                    "rubrics": rec.get("rubrics"),
                    "question": rec["question"],
                    "n_inputs": len(names),
                    "n_turns": len(result.trajectory.turns),
                    "prompt_tokens": result.total_prompt_tokens or 0,
                    "completion_tokens": result.total_completion_tokens or 0,
                    "elapsed_seconds": round(elapsed, 2),
                    "run_err": None,
                    "model": args.model,
                }
                print(
                    f"  [{i}/{len(todo)}] {qid:<8} {len(answer):5d} chars  "
                    f"{len(names)} input(s)  {row['n_turns']} turns  {elapsed:.0f}s"
                    + ("  (EMPTY ANSWER)" if not answer.strip() else "")
                )
            except Exception as exc:
                traceback.print_exc(limit=3)
                row = {
                    "id": qid, "answer": "", "model_response": "",
                    "reference": rec.get("reference"), "rubrics": rec.get("rubrics"),
                    "question": rec.get("question"), "n_inputs": 0, "n_turns": 0,
                    "prompt_tokens": 0, "completion_tokens": 0, "elapsed_seconds": 0.0,
                    "run_err": f"harness: {type(exc).__name__}: {exc}"[:300],
                    "model": args.model,
                }
                print(f"  [{i}/{len(todo)}] {qid:<8} HARNESS ERROR {type(exc).__name__}")
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()

    # Tolerate unparseable lines: a corrupted row must not take down the
    # summary of an otherwise complete run. (It did, once -- two processes
    # wrote to this file concurrently and the final tally crashed on the
    # interleaved line, losing the run's result line entirely.)
    n_ans = 0
    for line in results_path.open(encoding="utf-8"):
        if not line.strip():
            continue
        try:
            if (json.loads(line).get("answer") or "").strip():
                n_ans += 1
        except json.JSONDecodeError:
            continue
    print(f"\n{n_ans} answers produced  ->  {results_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
