"""Slim the run trajectories down to what someone would actually inspect.

Full trajectories are 344 MB (M3) and 490 MB (Kimi), mostly namespace snapshots
and stdout dumps. What answers "why did this task score what it did" is the code
the model ran, the errors it hit, and whether it submitted. That compresses to a
few MB and can live in the repo.
"""
import gzip, json, sys
from pathlib import Path

SRC = Path(sys.argv[1])
DEST = Path(sys.argv[2])
MAX_CODE = 4000        # a turn's code, truncated
MAX_TEXT = 600         # stdout / stderr / error, truncated

DEST.parent.mkdir(parents=True, exist_ok=True)


def clip(v, n):
    if not v:
        return ""
    s = str(v)
    return s if len(s) <= n else s[:n] + f"... [{len(s) - n} more chars]"


kept = tasks = 0
with gzip.open(DEST, "wt", encoding="utf-8") as out:
    for tdir in sorted(p for p in SRC.iterdir() if p.is_dir()):
        traj = tdir / "trajectory.jsonl"
        if not traj.exists():
            continue
        tasks += 1
        for line in open(traj, encoding="utf-8"):
            rec = json.loads(line)
            if "turn" not in rec:
                continue
            kept += 1
            out.write(json.dumps({
                "task": tdir.name,
                "turn": rec.get("turn"),
                "turn_type": rec.get("turn_type"),
                "submitted": rec.get("submitted"),
                "code": clip(rec.get("code"), MAX_CODE),
                "stdout": clip(rec.get("stdout"), MAX_TEXT),
                "stderr": clip(rec.get("stderr"), MAX_TEXT),
                "error": clip(rec.get("error"), MAX_TEXT),
                "validation_errors": rec.get("validation_errors") or [],
                "prompt_tokens": rec.get("prompt_tokens"),
                "completion_tokens": rec.get("completion_tokens"),
                "cached_tokens": rec.get("cached_tokens"),
                "duration_s": round(rec.get("duration_s") or 0, 2),
            }, ensure_ascii=False) + "\n")

size = DEST.stat().st_size
print(f"{DEST.name}: {tasks} tasks, {kept} turns, {size/1e6:.1f} MB gzipped")
