"""Run DataAgentBench queries through fabric-rlm and grade with DAB's own validators.

Never a proxy metric. The previous attempt on this benchmark built a stricter
local grader (it checked column aliases) which fed a GEPA optimiser wrong
feedback; that is worse than no optimiser. Grading here is DAB's per-query
validate.py, unmodified.

Prompt inputs are limited to what SUBMISSION_RUBRIC.md section 1 sanctions: the
question text, db_description.txt, and harness boilerplate about how to open the
database and what shape the answer takes. Nothing derived from ground_truth.csv
or validate.py ever reaches the model.

Usage:
  MODE=smoke  python dab_run.py agnews:1 bookreview:1 yelp:1
  MODE=full   python dab_run.py                  # every query, RUNS runs each
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import re
import shutil
import sys
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

warnings.filterwarnings("ignore")

HERE = pathlib.Path(__file__).parent
DAB = pathlib.Path(r"C:/Users/sandeeppawar/Downloads/DataAgentBench")
HUBS = HERE / "dab_hubs"
os.environ["OPENROUTER_API_KEY"] = (HERE / ".orkey").read_text().strip()

MODE = os.environ.get("MODE", "smoke")
RUNS = int(os.environ.get("RUNS", "1"))
WORKERS = int(os.environ.get("WORKERS", "3"))
MAX_TURNS = int(os.environ.get("MAX_TURNS", "40"))
TIMEOUT = float(os.environ.get("TIMEOUT", "900"))
# db_description_withhint.txt is HINTS ONLY and the official runner appends it to
# the plain description (run_agent.py: db_description += "\n\n" + hints). Using it
# flags the submission as "hints used = Yes"; all three leaderboard leaders did.
USE_HINTS = os.environ.get("USE_HINTS", "0") == "1"
MODEL = os.environ.get("MODEL", "openrouter/minimax/minimax-m3")
# Optional reasoning-effort override for reasoning-line models (e.g. gpt-5.6-luna
# at EFFORT=max). Empty means the model's default.
EFFORT = os.environ.get("EFFORT", "")
OUT = HERE / f"dab_{MODE}_results.json"
TRACES = HERE / f"dab_{MODE}_traces"

sys.path.insert(0, str(DAB))
from dab_load import build_hub  # noqa: E402
from fabric_rlm import RLM, File  # noqa: E402
import dspy  # noqa: E402

# Any A/B that varies only a runtime parameter is silently invalid under the
# default dspy cache: identical prompts replay a previous generation.
dspy.configure_cache(enable_disk_cache=False, enable_memory_cache=False)

# Generic answer hygiene. Deliberately says nothing about any specific query,
# value or threshold: it describes how to present an answer, not what it is.
# PROMPT=base selects the original six rules (the base54 arm); PROMPT=treat
# (default) adds the shape/grain verification block. Both texts stay in this
# file so either arm can be reproduced exactly.
PROMPT_ARM = os.environ.get("PROMPT", "treat")

BASE_RULES = """
How to present the answer:

1. State the answer directly in the first sentence. Do not open with method or caveats.
2. Put each value IMMEDIATELY after the thing it belongs to, in the same short
   phrase: "Pennsylvania: 3.48" rather than "Pennsylvania has the most, and the
   average rating for those businesses is 3.48". Graders look for a value close to
   its label.
3. Give every number in decimal form (write 4.0, not 4), and repeat it unrounded
   if you rounded it.
4. If the question asks which single item, name only that item. Do not list the
   other candidates you considered or rejected.
5. If the question asks for a set or a list, include every member.
6. Never return an empty answer. If you could not finish, give your best figure
   and say it is uncertain.
"""

TREAT_RULES = """
Shape and grain, before you write anything:

A. Decide whether the question wants ONE value or ONE ROW PER GROUP. A superlative
   inside a grouping ("the highest per region", "the longest in each category") does
   NOT collapse to a single global winner. Answer at the grain asked for.
B. Count the rows you are about to report against the number of distinct qualifying
   groups. If those two numbers differ, you have either dropped members or fanned
   the join out. Find out which before answering.
C. After a join, sanity-check the row count against both inputs. An unexplained
   multiple means the join key is wrong, even if the query ran without error.
D. Re-run the exact query behind your final answer and check the result matches what
   you are about to state.

How to present the answer:

1. State the answer directly in the first sentence. No method, no caveats first.
2. Put each value IMMEDIATELY after the thing it belongs to, within a few words, in
   the same clause: "Pennsylvania: 3.48". Not "Pennsylvania has the most, and the
   average rating for those businesses is 3.48". A label and its number more than a
   handful of words apart will not be read as belonging together.
3. Give every number in decimal form (write 4.0, not 4) and unrounded as well if you
   rounded it.
4. If the question asks for a set or a list, give EVERY member, separated by
   semicolons, sorted sensibly. Never truncate, never write "and N others", never
   give a sample or a "top results" subset. An incomplete list scores zero even when
   every item in it is correct.
5. If the question asks which single item, name only that item. Do not list the
   other candidates you considered or rejected.
6. Never return an empty answer. If you could not finish, give your best figure and
   say it is uncertain.
"""

ANSWER_RULES = {"base": BASE_RULES, "treat": TREAT_RULES}[PROMPT_ARM]

# VERIFY=1 adds the one technique every submission above 0.74 shares: an
# independent second derivation. Sentinel runs deterministic verify_step checks,
# Sarvam a "blind second solver (original question only, no inherited context)",
# Spacedock a verify stage that "independently re-derives every answer". Ours is
# the blind-second-solver form: solve twice with no shared context, and if the
# two disagree, a reconciler (fresh context, given both answers AND the db)
# investigates the disagreement and decides. Targets single-run flakiness, which
# the v1-vs-v3 sign flip showed is the dominant error source at RUNS=1.
VERIFY = os.environ.get("VERIFY", "0") == "1"
# COVE=1 adds a deterministic check gate (Chain-of-Verification, Dhuliawala 2023;
# the shape of Sentinel's verify_step). Data that justified it: luna5 had 16
# agree-on-WRONG records -- both blind solves confident in the same wrong answer --
# which no amount of same-model consistency can catch. Checks are facts, not
# opinions: the gate only overrides an agreement when a concrete check query
# fails and the corrected answer differs.
COVE = os.environ.get("COVE", "0") == "1"

COVE_RULES = """
You are auditing an answer two independent analysts agreed on. Do not re-solve
from scratch. Instead:
1. Derive 2-4 CHECK QUERIES from the answer: facts that MUST hold if it is
   right (membership: each claimed value exists as stated; superiority: nothing
   beats a claimed maximum; completeness for a negative: enumerate candidates and
   show each fails; totals: the parts sum to the claim).
2. RUN each check against the database and print the result next to what the
   answer implies it should be.
3. If every check confirms, SUBMIT the answer UNCHANGED.
4. If a check fails, state which fact broke, derive the corrected answer, run the
   same checks on the correction, and submit it only if they pass.
Never override on judgment or style. Only a failed check, shown in output, may
change the answer.
"""

# Method diversity for the second blind solve: same question, different attack.
# Identically-prompted solves share failure modes, which is where the agree-on-
# wrong records come from; a decomposition-first path decorrelates them.
DIVERSE_NOTE = """

Method for this attempt: before any SQL, decompose the question into its atomic
conditions in comments (entities, filters, metric, grain, tie-breaks), quote the
exact question wording that justifies each condition, then build the query from
the decomposition."""

RECONCILE_RULES = """
Two analysts answered this question independently from the same database and
disagreed. Re-derive the answer yourself from the database, then decide. Their
answers are evidence about where to look, not authorities to average: check the
exact point where they diverge (a filter, a join, a definition, a period).
You MUST run at least one query against the database and print its result before
submitting; a verdict with no executed query in your output is invalid.
State the final answer in the first sentence, then one short sentence on why
the losing answer was wrong.
"""


def load_validator(ds: str, q: int):
    p = DAB / f"query_{ds}" / f"query{q}" / "validate.py"
    spec = importlib.util.spec_from_file_location(f"v_{ds}_{q}", str(p))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.validate


# The 12 datasets / 54 queries the leaderboard scores. SUBMISSION_RUBRIC.md still
# says "270 trials total" (54 x 5), so the 5 newer datasets (civic_unstructured,
# cve, imdb, krama, usaspending -- 50 more queries) are not in the scored set yet.
SCORED = ["DEPS_DEV_V1", "GITHUB_REPOS", "PANCANCER_ATLAS", "PATENTS", "agnews",
          "bookreview", "crmarenapro", "googlelocal", "music_brainz_20k",
          "stockindex", "stockmarket", "yelp"]
ALL_DATASETS = os.environ.get("DATASETS", "").split(",") if os.environ.get("DATASETS") else SCORED


def all_queries() -> list[tuple[str, int]]:
    out = []
    for ds in ALL_DATASETS:
        d = DAB / f"query_{ds}"
        for qd in sorted(d.glob("query[0-9]*"), key=lambda p: int(re.sub(r"\D", "", p.name))):
            out.append((ds, int(re.sub(r"\D", "", qd.name))))
    return out


_SCHEMA_CACHE: dict[str, str] = {}
_SCHEMA_LOCK = threading.Lock()


def schema_dump(hub: pathlib.Path, attach_sql: str) -> str:
    """store.table(col type, ...) for every sanctioned store.

    The official scaffold gives its agent a ListDBTool that loads and lists every
    store, so this is the same capability pre-computed.

    Cached per dataset and opened READ_ONLY. Doing this per query re-opened the
    same hub from three worker threads: DuckDB takes an exclusive lock on a
    read-write handle, and in-process connections to one path share a catalog, so
    replaying ATTACH raised "database already exists". That cost 24 of 54 runs.
    """
    # The lock spans the whole computation, not just the cache check. Two threads
    # that both miss the cache would both open the same path, and in-process
    # DuckDB connections to one file share a catalog, so the second ATTACH throws.
    # Separate processes are fine, which is why the RLM subprocesses never hit this.
    import duckdb
    with _SCHEMA_LOCK:
        if hub.stem in _SCHEMA_CACHE:
            return _SCHEMA_CACHE[hub.stem]
        con = duckdb.connect(str(hub), read_only=True)
        return _schema_dump_locked(con, hub, attach_sql)


def _schema_dump_locked(con, hub: pathlib.Path, attach_sql: str) -> str:
    """Compact, complete schema description.

    Tables are grouped by identical column signature. stockmarket holds 2,754
    per-ticker tables with the same columns; listing each one individually made
    that task prompt 400k characters (~115k tokens), resent EVERY turn -- one
    3-turn query cost 538k tokens. A signature group prints its shared columns
    once, a few example names, and the exact count, which loses nothing the
    model needs: membership is still enumerable via duckdb_tables().
    """
    try:
        con.execute(attach_sql)
        rows = con.execute("""
            select database_name, schema_name, table_name
            from duckdb_tables()
            where database_name not in ('system','temp')
            order by 1,2,3""").fetchall()
        groups: dict[tuple, list[tuple[str, int]]] = {}
        for db, sch, tab in rows:
            ref = f"{db}.{tab}" if sch == "main" else f"{sch}.{tab}"
            cols = con.execute(f'describe select * from "{db}"."{sch}"."{tab}"').fetchall()
            n = con.execute(f'select count(*) from "{db}"."{sch}"."{tab}"').fetchone()[0]
            sig = (db, sch, tuple((c[0], c[1]) for c in cols))
            groups.setdefault(sig, []).append((ref, n))
        out = []
        for (db, sch, cols), members in sorted(groups.items(), key=lambda kv: kv[1][0][0]):
            spec = ", ".join(f"{c[0]} {c[1]}" for c in cols)
            if len(members) <= 4:
                for ref, n in members:
                    out.append(f"  {ref}  [{n:,} rows]\n      {spec}")
            else:
                names = [m[0] for m in members]
                total = sum(m[1] for m in members)
                ex = ", ".join(names[:3])
                out.append(
                    f"  {len(members):,} tables in {db} sharing one structure "
                    f"(e.g. {ex}, ...)  [{total:,} rows total]\n      {spec}\n"
                    f"      full list: SELECT table_name FROM duckdb_tables() "
                    f"WHERE database_name='{db}'")
        text = "\n".join(out)
        _SCHEMA_CACHE[hub.stem] = text        # caller already holds _SCHEMA_LOCK
        return text
    finally:
        con.close()


def build_task(ds: str, q: int, attach_sql: str, hub: pathlib.Path) -> str:
    root = DAB / f"query_{ds}"
    question = json.load(open(root / f"query{q}" / "query.json", encoding="utf-8"))
    if isinstance(question, dict):
        question = question.get("query") or question.get("question") or str(question)
    desc = (root / "db_description.txt").read_text(encoding="utf-8", errors="replace").strip()
    if USE_HINTS:
        hp = root / "db_description_withhint.txt"
        if hp.exists():
            desc += "\n\n" + hp.read_text(encoding="utf-8", errors="replace").strip()
    schema = schema_dump(hub, attach_sql)
    # CARTO=1 appends the deterministic data map (grain, verified joins,
    # dimension values) built by dab_carto.py. Mechanical, GT-firewalled:
    # reads only the hub, never the query folders.
    if os.environ.get("CARTO", "0") == "1":
        cp = HERE / "dab_carto" / f"{ds}.txt"
        if cp.exists():
            schema += "\n\n" + cp.read_text(encoding="utf-8")
    return (
        f"Question: {question}\n\n"
        "You have one DuckDB file at `db.path` holding every data store for this "
        "task. Open it and replay the attachment statements below first, which wire "
        "up the stores kept in their original engines:\n\n"
        "```python\n"
        "import duckdb\n"
        "# read_only=True matters: several tasks share this file, and a read-write\n"
        "# handle takes an exclusive lock that makes the others fail to open it.\n"
        "con = duckdb.connect(db.path, read_only=True)\n"
        f"con.execute('''{attach_sql}''')\n"
        "print(con.execute(\"select database_name, schema_name, table_name from \"\n"
        "                  \"duckdb_tables() where database_name not in ('system','temp') \"\n"
        "                  \"order by 1,2,3\").fetchall())\n"
        "```\n\n"
        "Refer to any table as `store_name.table_name`; that form resolves for every "
        "store regardless of which engine it came from. Profile before you aggregate: "
        "look at real values, check join keys on both sides for format mismatches "
        "(leading zeros, case, whitespace, type), and confirm a filter did not drop "
        "every row.\n\n"
        f"Tables available (pre-listed so you need not discover them):\n{schema}\n\n"
        f"Database description provided with this task:\n{desc}\n"
        f"{ANSWER_RULES}\n"
        "SUBMIT with a single field `answer`."
    )


# Concurrent workers must not race to build the same hub file.
HUB_LOCK = threading.Lock()

# ---------------------------------------------------------------- leak control
# The hub used to be handed to the model as a path inside the DataAgentBench tree.
# The subprocess has unrestricted filesystem access, so os.walk from db.path found
# the sibling query<N>/ folders and the model opened validate.py and query.json and
# read the answers. Confirmed in dab_treat54_traces/agnews_q4_r1.json t33
# ("gt = \"Africa\"") and t19 ("GROUND_TRUTH = 0.14414..."), which is precisely the
# disqualifying case in SUBMISSION_RUBRIC.md 2.1.
#
# Two independent defences, because a prompt rule is not a control:
#   1. isolate_hub(): serve the hub from a scratch directory that contains nothing
#      but that one file, so walking upward finds no benchmark artefacts.
#   2. audit_trace(): mechanical post-run check. Any run that touched a validator,
#      a ground-truth file or a sibling query folder is marked contaminated and
#      scored zero regardless of what the validator said.
ISO_ROOT = HERE / "dab_iso"
_ISO_CACHE: dict[str, pathlib.Path] = {}
_ISO_LOCK = threading.Lock()

LEAK_PATTERNS = (
    ("ground_truth", re.compile(r"ground_?truth", re.I)),
    ("validate.py", re.compile(r"validate\s*\.\s*py|from\s+validate\s+import|import\s+validate\b", re.I)),
    ("query.json", re.compile(r"quer(?:y|ies)\s*\.\s*json", re.I)),
    ("withhint", re.compile(r"withhint", re.I)),
    ("dab tree walk", re.compile(r"(os\.walk|listdir|glob|scandir|rglob)[^\n]{0,80}(DataAgentBench|query_)", re.I)),
    ("sibling query dir", re.compile(r"query[\\/]?\d+[\\/]", re.I)),
)


def isolate_hub(hub: pathlib.Path, attach_sql: str) -> tuple[pathlib.Path, str]:
    """Serve the hub AND every attached store from a directory holding nothing else.

    Isolating only the hub was pointless: the ATTACH statements go into the task
    prompt verbatim, so the model read
    "C:/.../DataAgentBench/query_agnews/query_dataset/metadata.db" straight out of
    its own instructions and walked up from there to the query folders. The stores
    are hardlinked (same volume, instant, no extra space -- PATENTS is 5.4 GB and
    imdb's people.sqlite 2.6 GB) and the SQL is rewritten to the local names, so no
    benchmark path reaches the model at all.
    """
    with _ISO_LOCK:
        key = hub.stem
        if key in _ISO_CACHE and _ISO_CACHE[key][0].exists():
            return _ISO_CACHE[key]
        d = ISO_ROOT / key
        d.mkdir(parents=True, exist_ok=True)
        dest = d / hub.name
        # Refresh when the source hub is newer: crmarenapro was rebuilt after its
        # missing store was found, and an existence-only check would have silently
        # kept serving the incomplete copy.
        if not dest.exists() or dest.stat().st_mtime < hub.stat().st_mtime:
            shutil.copy2(hub, dest)          # small; must be writable/independent
        new_sql = attach_sql
        for m in re.finditer(r"ATTACH\s+'([^']+)'", attach_sql):
            src = pathlib.Path(m.group(1))
            local = d / src.name
            if not local.exists():
                try:
                    os.link(src, local)      # hardlink: instant, no copy
                except OSError:
                    shutil.copy2(src, local)  # cross-volume fallback
            new_sql = new_sql.replace(m.group(1), local.as_posix())
        assert "DataAgentBench" not in new_sql, "benchmark path survived isolation"
        _ISO_CACHE[key] = (dest, new_sql)
        return dest, new_sql


def audit_trace(turns: list) -> list[str]:
    """Names of leak patterns that appear in any turn's code. Empty means clean."""
    blob = "\n".join((t.code or "") for t in turns)
    return [name for name, rx in LEAK_PATTERNS if rx.search(blob)]

results, lock = {}, threading.Lock()
if OUT.exists():
    results = {r["key"]: r for r in json.load(open(OUT, encoding="utf-8"))}
TRACES.mkdir(exist_ok=True)


def one(job):
    ds, q, run = job
    key = f"{ds}:{q}:{run}"
    if key in results:
        return
    t0 = time.time()
    with HUB_LOCK:
        hub, _ = build_hub(ds, HUBS)
    raw_sql = (HUBS / f"{ds}.attach.sql").read_text(encoding="utf-8")
    # Everything the model sees must live outside the benchmark tree.
    iso_hub, attach_sql = isolate_hub(hub, raw_sql)
    rec = {"key": key, "dataset": ds, "query": q, "run": run}

    # CTX=1: data_context prototype. SAME content as the pasted map, delivered
    # instead as a sandbox-queryable file the model consults on demand. The A/B
    # against CARTO=1 isolates delivery, holding content constant.
    ctx_path = HERE / "dab_ctx" / f"{ds}.json"
    use_ctx = os.environ.get("CTX", "0") == "1" and ctx_path.exists()

    def solve(task_text):
        ins = {"db": File(str(iso_hub))}
        if use_ctx:
            ins["data_context"] = File(str(ctx_path))
        r = RLM.task(
            task=task_text,
            inputs=ins,
            outputs=["answer"],
            lm={"model": MODEL, "api_key": os.environ["OPENROUTER_API_KEY"],
                "api_base": "https://openrouter.ai/api/v1", "max_tokens": 32000,
                # Pin to caching providers: GMICloud advertises 60% off but re-bills
                # the full prefix every turn (measured 6.5x cost on call 2). Minimax
                # cached 99% of the same conversation. And bound each REQUEST so a
                # dead connection errors instead of freezing a worker for the night
                # (the task-level timeout does not cover a blocked HTTP read).
                "timeout": 600,
                **({"reasoning_effort": EFFORT} if EFFORT else {}),
                # Provider pinning applies to the minimax route; harmless elsewhere.
                **({"extra_body": {"provider": {"order": ["Minimax", "Novita"],
                                                "allow_fallbacks": True}}}
                   if "minimax" in MODEL else {})},
            skills=["data_exploration"],
            max_turns=MAX_TURNS, timeout=TIMEOUT,
        ).run()
        a = (r.payload or {}).get("answer", "") or ""
        lk = audit_trace(r.trajectory.turns if r.trajectory else [])
        return r, (a if isinstance(a, str) else str(a)), lk

    def answers_agree(a: str, b: str) -> bool:
        # Structural agreement only. A false "disagree" costs one reconciler run,
        # never correctness, so this deliberately errs toward disagreement.
        norm = lambda s: re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()
        na, nb = norm(a), norm(b)
        if na == nb:
            return True
        nums = lambda s: sorted(re.findall(r"-?\d+(?:\.\d+)?", s))
        if nums(a) != nums(b):
            return False
        # Numbers identical from here. Semicolon lists agree only on the same
        # item set: a missing member is THE enumeration failure mode, and a
        # substring or overlap test would wave it through.
        if ";" in a or ";" in b:
            items = lambda s: {norm(x) for x in s.split(";") if norm(x)}
            return items(a) == items(b)
        # short-vs-verbose phrasings of the same single answer
        if na and nb and (na in nb or nb in na):
            return True
        toks = lambda s: {w for w in norm(s).split() if len(w) > 3}
        ta, tb = toks(a), toks(b)
        return len(ta & tb) >= 0.6 * max(min(len(ta), len(tb)), 1)

    try:
        base_task = build_task(ds, q, attach_sql, hub)
        if use_ctx:
            base_task += (
                "\n\nA data_context is available at `data_context.path`: a JSON of "
                "measured facts about these stores (per-table grain, column "
                "uniqueness, observed values, and VERIFIED join keys under the "
                "'joins' topic). Load it and consult the relevant topic BEFORE "
                "choosing join keys or aggregation grain:\n"
                "  import json\n"
                "  CTX = json.load(open(data_context.path))\n"
                "  print(CTX['overview']); print(CTX['joins'])\n"
                "Its facts are measured from the data; trust them over guesses.")
        final_task = base_task            # updated when a later stage's answer wins
        res, ans, leaks = solve(base_task)
        extra_tokens = 0
        if VERIFY:
            res2, ans2, leaks2 = solve(base_task + DIVERSE_NOTE)   # blind + method-diverse
            leaks = sorted(set(leaks) | set(leaks2))
            extra_tokens += (res2.total_prompt_tokens or 0) + (res2.total_completion_tokens or 0)
            rec["verify_a"], rec["verify_b"] = ans[:300], ans2[:300]
            if answers_agree(ans, ans2):
                rec["verify"] = "agree"
                if not ans.strip() and ans2.strip():
                    res, ans = res2, ans2              # both "agreed" on empty
                if COVE and ans.strip():
                    # Audit the agreement with executable checks. Conservative by
                    # construction: the auditor is told to submit UNCHANGED unless
                    # a check query fails in its printed output.
                    ctask = base_task + COVE_RULES + f"\nThe agreed answer to audit:\n{ans}\n"
                    res4, ans4, leaks4 = solve(ctask)
                    leaks = sorted(set(leaks) | set(leaks4))
                    extra_tokens += (res4.total_prompt_tokens or 0) + (res4.total_completion_tokens or 0)
                    if ans4.strip() and not answers_agree(ans, ans4):
                        rec["verify"] = "cove_override"
                        res, ans, final_task = res4, ans4, ctask
                    else:
                        rec["verify"] = "agree_checked"
            else:
                rec["verify"] = "reconciled"
                rtask = (base_task + RECONCILE_RULES + f"""
Analyst 1 answered: {ans}

Analyst 2 answered: {ans2}
""")
                res3, ans3, leaks3 = solve(rtask)
                leaks = sorted(set(leaks) | set(leaks3))
                extra_tokens += (res3.total_prompt_tokens or 0) + (res3.total_completion_tokens or 0)
                if ans3.strip():
                    res, ans, final_task = res3, ans3, rtask
                elif not ans.strip() and ans2.strip():
                    res, ans = res2, ans2
        rec["leaks"] = leaks
        rec["ensemble_tokens"] = extra_tokens
        rec.update(answer=str(ans), turns=res.n_turns,
                   tokens=(res.total_prompt_tokens or 0) + (res.total_completion_tokens or 0),
                   error=None)
        # Traces are mandatory for a leaderboard submission and must match the
        # answer. TurnRecord exposes stdout/stderr, not "output".
        (TRACES / f"{ds}_q{q}_r{run}.json").write_text(json.dumps(
            {"key": key, "model": MODEL, "task": final_task, "stage": rec.get("verify") or "single",
             "answer": str(ans), "n_turns": res.n_turns,
             "failure_reason": getattr(res, "failure_reason", None),
             "turns": [{"turn": t.turn, "turn_type": t.turn_type, "code": t.code,
                        "stdout": t.stdout, "stderr": t.stderr, "error": t.error,
                        "submitted": t.submitted, "duration_s": t.duration_s,
                        "prompt_tokens": t.prompt_tokens,
                        "completion_tokens": t.completion_tokens}
                       for t in (res.trajectory.turns if res.trajectory else [])]},
            ensure_ascii=False, default=str, indent=1), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        rec.update(answer="", turns=0, tokens=0,
                   error=f"{type(exc).__name__}: {str(exc)[:200]}")
    try:
        ok, why = load_validator(ds, q)(rec["answer"]) if rec["answer"].strip() else (False, "empty")
    except Exception as exc:  # noqa: BLE001
        ok, why = False, f"validator raised {type(exc).__name__}: {str(exc)[:80]}"
    # A contaminated run scores zero whatever the validator said.
    if rec.get("leaks"):
        ok, why = False, "CONTAMINATED: " + ", ".join(rec["leaks"])
    rec.update(passed=bool(ok), reason=str(why)[:220], seconds=round(time.time() - t0, 1))
    with lock:
        results[key] = rec
        json.dump(sorted(results.values(), key=lambda r: r["key"]),
                  open(OUT, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(f"[{len(results):3}] {key:26} {rec['seconds']:6.1f}s {rec['turns']:3}t "
          f"{rec['tokens']:>9,}tok {'PASS' if ok else 'fail'}  {rec['reason'][:60]}"
          f"{'  ERR' if rec['error'] else ''}", flush=True)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        pairs = [(a.split(":")[0], int(a.split(":")[1])) for a in args]
    else:
        pairs = all_queries()
    jobs = [(ds, q, r) for ds, q in pairs for r in range(1, RUNS + 1)]
    jobs = [j for j in jobs if f"{j[0]}:{j[1]}:{j[2]}" not in results]
    print(f"MODE={MODE} model={MODEL} jobs={len(jobs)} turns={MAX_TURNS}", flush=True)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for f in as_completed([ex.submit(one, j) for j in jobs]):
            f.result()
    done = list(results.values())
    print(f"\npassed {sum(1 for r in done if r['passed'])}/{len(done)}"
          f"   tokens {sum(r['tokens'] for r in done):,}")
