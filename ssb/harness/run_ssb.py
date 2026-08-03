"""SpreadsheetBench Verified-400 runner, sharded, with a selectable arm.

Task text, workbook context, sanity validator and cell-exact grading are taken
verbatim from examples/notebooks/ssb400_minimax_m3_fabric_repro.ipynb, the run
that produced the published 82.25%. The only thing that varies between arms is
how the excel_modify skill is delivered.
"""
from __future__ import annotations

import argparse, hashlib, json, os, pathlib, re, shutil, time, traceback

import dspy, openpyxl
from fabric_rlm import File, RLM, add_excel_workbook_context
from fabric_rlm.skill_loader import SkillLoader

ap = argparse.ArgumentParser()
ap.add_argument("--data", required=True)          # spreadsheetbench_verified_400 dir
ap.add_argument("--outdir", required=True)
ap.add_argument("--arm", choices=["body", "cards"], required=True)
ap.add_argument("--shard", type=int, default=0)
ap.add_argument("--nshards", type=int, default=1)
ap.add_argument("--model", default="openrouter/minimax/minimax-m3")
ap.add_argument("--max-turns", type=int, default=14)
ap.add_argument("--timeout", type=int, default=300)
ap.add_argument("--temperature", type=float, default=1.0)  # published run used 1.0
ap.add_argument("--max-tokens", type=int, default=16000)
ap.add_argument("--reasoning-effort", default=None)
ap.add_argument("--only-ids", default=None,
                help="JSON file of question_ids to restrict the run to")
ap.add_argument("--abort-if-tokens-over", type=int, default=0,
                help="stop this shard if mean tokens/task exceeds this")
args = ap.parse_args()

DS = pathlib.Path(args.data)
OUT = pathlib.Path(args.outdir)
WORK = OUT / "work"; SUB = OUT / "submitted"; TR = OUT / "traces"
for p in (WORK, SUB, TR):
    p.mkdir(parents=True, exist_ok=True)


def rows(ds):
    out = []
    for r in json.loads((ds / "dataset.json").read_text(encoding="utf-8")):
        sid = str(r["id"])
        out.append({**r, "question_id": f"SSB_{sid}", "spreadsheet_id": sid,
                    "init_file": f"1_{sid}_init.xlsx", "golden_file": f"1_{sid}_golden.xlsx"})
    return out


def parse_pos(pos):
    s = re.sub(r"!'", "'!", pos.strip())
    # An unmatched trailing quote must be dropped, not balanced by prepending
    # one: prepending flips the quote state so every later comma reads as
    # quoted and multi-range positions never split. Affects SSB_170-13, whose
    # three ranges collapsed into one unparseable string.
    if s.count("'") % 2 and s.endswith("'"):
        s = s[:-1]
    elif s.count("'") % 2:
        s = "'" + s
    parts, cur, q = [], "", False
    for ch in s:
        if ch == "'":
            q = not q; continue
        if ch == "," and not q:
            if cur.strip():
                parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    out = []
    for p in parts:
        sh, rng = (p.split("!", 1) if "!" in p else (None, p))
        m = re.match(r"^([A-Z]+)(\d+):(\d+)$", rng.strip())
        out.append((sh.strip() if sh else None,
                    f"{m.group(1)}{m.group(2)}:{m.group(1)}{m.group(3)}" if m else rng.strip()))
    return out


def coord_values(rng):
    if hasattr(rng, "value"):
        return [(rng.coordinate, rng.value)]
    return [(c.coordinate, c.value) for row in rng
            for c in (row if not hasattr(row, "value") else [row])]


def flat(rng):
    return [v for _, v in coord_values(rng)]


def eq(a, b):
    return ((a is None and b is None)
            or (isinstance(a, (int, float)) and isinstance(b, (int, float))
                and abs(float(a) - float(b)) <= 1e-6)
            or str(a).strip() == str(b).strip())


def grade(out_xlsx, gold_xlsx, sheet, pos):
    wa = openpyxl.load_workbook(out_xlsx, data_only=True)
    wg = openpyxl.load_workbook(gold_xlsx, data_only=True)
    hit = total = 0
    bad_ranges = []
    for sh, rng in parse_pos(pos):
        s = sh or sheet or wa.sheetnames[0]
        sa = s if s in wa.sheetnames else wa.sheetnames[0]
        sg = s if s in wg.sheetnames else wg.sheetnames[0]
        try:
            av, gv = flat(wa[sa][rng]), flat(wg[sg][rng])
        except ValueError:
            # openpyxl 3.1.5 rejects multi-area strings that older versions
            # accepted. Count the range as unmatched instead of erroring the
            # whole task, and record it so it is visible rather than silent.
            bad_ranges.append(f"{s}!{rng}")
            continue
        hit += sum(eq(a, g) for a, g in zip(av, gv))
        total += len(gv)
    return (hit == total and not bad_ranges), hit, total


def task_text(r, path):
    sheet = r.get("answer_sheet") or "(use the only sheet in the workbook)"
    return f"""You must MODIFY an Excel (.xlsx) workbook in place using openpyxl.
WORKBOOK PATH: {path}
TARGET SHEET: {sheet}
TARGET CELL RANGE: {r['answer_position']}
INSTRUCTION:
{r['instruction']}
REQUIRED: inspect the workbook, compute values in Python, write literal values into exactly the target cells/ranges, save the same path, reload with data_only=True, verify no formulas/errors/placeholders/prose/code remain in target cells unless a blank is the intended output, then SUBMIT(answer='done')."""


def sanity(r, path):
    errors = {"#N/A", "#VALUE!", "#REF!", "#DIV/0!", "#NAME?", "#NULL!", "#NUM!"}
    bad = ("Sub ", "End Sub", "Power Query", "VBA", "Macro:", "let Source",
           "Application.", "ws.Range", "ws.Rows")

    def v(payload, context):
        assert payload.get("answer") == "done", "answer must be done"
        wf = openpyxl.load_workbook(path, data_only=False)
        wv = openpyxl.load_workbook(path, data_only=True)
        for sh, rng in parse_pos(r["answer_position"]):
            s = sh or r.get("answer_sheet") or wf.sheetnames[0]
            s = s if s in wf.sheetnames else wf.sheetnames[0]
            for (coord, fv), (_, dv) in zip(coord_values(wf[s][rng]),
                                            coord_values(wv[s][rng])):
                assert fv not in errors and dv not in errors, f"{s}!{coord} has Excel error"
                if isinstance(fv, str):
                    assert not fv.startswith("="), f"{s}!{coord} still contains formula"
                    assert fv not in ("-", "TBD", "N/A", "see notes"), f"{s}!{coord} has placeholder"
                    for marker in bad:
                        assert marker not in fv, f"{s}!{coord} contains code/prose marker {marker!r}"
    return v


lm_kwargs = dict(api_key=os.environ["OPENROUTER_API_KEY"],
                 api_base="https://openrouter.ai/api/v1",
                 max_tokens=args.max_tokens)
extra = {"usage": {"include": True}}
if "minimax" in args.model:
    # pin the provider so the published run is reproducible
    extra["provider"] = {"order": ["minimax/fp8"], "allow_fallbacks": False}
if "gpt-5" not in args.model:
    # every route used here accepts temperature except the gpt-5.x family on
    # OpenRouter; matching the published run's 1.0 keeps arms comparable
    lm_kwargs["temperature"] = args.temperature
if args.reasoning_effort:
    # litellm's model map does not know this route accepts reasoning_effort and
    # blocks it, so allow-list it explicitly. Without this drop_params would
    # discard it silently and the run would look like it used the effort asked
    # for. gpt-5.x on OpenRouter also rejects temperature, hence drop_params.
    lm_kwargs["reasoning_effort"] = args.reasoning_effort
    lm_kwargs["allowed_openai_params"] = ["reasoning_effort"]
    lm_kwargs["drop_params"] = True
lm = dspy.LM(args.model, extra_body=extra, **lm_kwargs)

loader = SkillLoader()
skill_path = pathlib.Path(__import__("fabric_rlm").__file__).parent / "skills" / "excel_modify.md"
skill_sha = hashlib.sha256(skill_path.read_bytes()).hexdigest()

spr = DS / "spreadsheet"
all_rows = rows(DS)
if args.only_ids:
    keep = set(json.load(open(args.only_ids, encoding="utf-8")))
    all_rows = [r for r in all_rows if r["question_id"] in keep]
mine = all_rows[args.shard::args.nshards]
results_path = OUT / f"results_{args.arm}_shard{args.shard}.jsonl"

done = set()
if results_path.exists():
    for line in open(results_path, encoding="utf-8"):
        try:
            done.add(json.loads(line)["question_id"])
        except Exception:
            pass

for i, r in enumerate(mine, 1):
    qid, sid = r["question_id"], r["spreadsheet_id"]
    if qid in done:
        continue
    src, gold = spr / sid / r["init_file"], spr / sid / r["golden_file"]
    # Five tasks in this extract ship no workbook at all. Copying outside the
    # try block let a missing file kill the whole shard and every task after
    # it, which silently truncated four of six shards.
    if not src.exists() or not gold.exists():
        with results_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"question_id": qid, "spreadsheet_id": sid,
                                "arm": args.arm, "skipped": "missing dataset files",
                                "passed": None}) + "\n")
        print(f"[{args.arm} s{args.shard} {i}/{len(mine)}] {qid} SKIPPED (no workbook)",
              flush=True)
        continue
    wdir = WORK / args.arm / qid
    wdir.mkdir(parents=True, exist_ok=True)
    work = wdir / "work.xlsx"
    try:
        shutil.copyfile(src, work)
    except Exception as exc:
        with results_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"question_id": qid, "arm": args.arm,
                                "passed": False, "error": f"copy failed: {exc}"}) + "\n")
        continue

    since = len(getattr(lm, "history", []))
    rec = {"question_id": qid, "spreadsheet_id": sid, "arm": args.arm,
           "instruction_type": r.get("instruction_type"),
           "answer_position": r["answer_position"], "model": args.model,
           "skill_sha256": skill_sha}
    try:
        kwargs = dict(skills=["excel_modify"], skill_loader=loader)
        if args.arm == "cards":
            kwargs["skills_as_cards"] = True
        rlm = RLM.from_task(
            task=add_excel_workbook_context(
                task_text(r, work), str(work),
                target_position=r["answer_position"],
                default_sheet=r.get("answer_sheet") or None),
            inputs={"workbook": File(str(work))}, outputs=["answer"], lm=lm,
            max_turns=args.max_turns, timeout=args.timeout,
            output_validator_context=sanity(r, work), **kwargs)
        t0 = time.perf_counter()
        out = rlm.run()
        elapsed = time.perf_counter() - t0
        ok, hit, total = grade(work, gold, r.get("answer_sheet") or "", r["answer_position"])
        hist = getattr(lm, "history", [])[since:]
        cost = sum(float((h.get("usage") or {}).get("cost") or h.get("cost") or 0)
                   for h in hist if isinstance(h, dict))
        pt = sum(int((h.get("usage") or {}).get("prompt_tokens") or 0)
                 for h in hist if isinstance(h, dict))
        ct = sum(int((h.get("usage") or {}).get("completion_tokens") or 0)
                 for h in hist if isinstance(h, dict))
        shutil.copyfile(work, SUB / f"{args.arm}_{qid}.xlsx")
        turns = list(getattr(out.trajectory, "turns", []) or []) if out.trajectory else []
        rec.update({"passed": ok, "cells_matched": hit, "cells_total": total,
                    "submitted": out.submitted, "failure_reason": out.failure_reason,
                    "elapsed_seconds": round(elapsed, 2), "n_turns": len(turns),
                    "cost_usd": cost, "prompt_tokens": pt, "completion_tokens": ct})
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        # A dead or rate-limited key is not a model result. Recording it as
        # passed=False turned one key expiry into 37 phantom task failures and
        # silently moved both arms by four points. Stop the shard instead so
        # the task is retried when credentials work.
        if any(t in msg for t in ("AuthenticationError", "RateLimitError",
                                  "PermissionDeniedError", "insufficient_quota")):
            print(f"CREDENTIAL FAILURE on {qid}: {msg[:120]} -- "
                  f"stopping shard {args.shard} without recording a result",
                  flush=True)
            break
        rec.update({"passed": False, "error": msg,
                    "traceback": traceback.format_exc()[:1500]})
    with results_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    print(f"[{args.arm} s{args.shard} {i}/{len(mine)}] {qid} pass={rec.get('passed')} "
          f"cells={rec.get('cells_matched', 0)}/{rec.get('cells_total', 0)} "
          f"turns={rec.get('n_turns')}", flush=True)

    if args.abort_if_tokens_over:
        seen = [json.loads(l) for l in open(results_path, encoding="utf-8")]
        toks = [(r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0)
                for r in seen]
        toks = [t for t in toks if t]
        if len(toks) >= 5:
            mean = sum(toks) / len(toks)
            if mean > args.abort_if_tokens_over:
                print(f"ABORT s{args.shard}: mean {mean:,.0f} tokens/task exceeds "
                      f"budget {args.abort_if_tokens_over:,} after {len(toks)} tasks",
                      flush=True)
                break

print(f"SHARD {args.shard} ARM {args.arm} DONE")
