"""Serialize an RLM trajectory to JSON for inspection and reproduction.

Written because the AIDABench runs were producing scores with no record of how
the model got there. A benchmark number nobody can inspect is a claim, not a
result -- and several "capability gaps" in this work turned out to be grader
bugs that only became visible by reading individual turns.

Keeps everything needed to audit a task: the exact prompt, every code block the
model ran, what the sandbox printed back, and the per-turn token and timing
counters. Trims only oversized stdout, so a task that dumps a whole sheet does
not produce a 50 MB trace.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

MAX_FIELD_CHARS = 20_000


def _trim(value: Any) -> Any:
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS] + f"\n...[trimmed {len(value) - MAX_FIELD_CHARS} chars]"
    return value


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _trim(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if dataclasses.is_dataclass(value):
        return {f.name: _jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    return _trim(repr(value))


def save_trace(
    path: Path,
    *,
    task_id: str,
    task_text: str,
    result: Any,
    config: dict[str, Any],
) -> None:
    """Write one task's full trajectory. Never raises -- a trace is not the run."""

    try:
        turns = []
        for t in getattr(result, "trajectory", None).turns:  # type: ignore[union-attr]
            turns.append({f.name: _jsonable(getattr(t, f.name)) for f in dataclasses.fields(t)})
        payload = getattr(result, "payload", None)
        doc = {
            "id": task_id,
            "config": _jsonable(config),
            "task_prompt": _trim(task_text),
            "submitted": bool(getattr(result, "submitted", False)),
            "failure_reason": getattr(result, "failure_reason", None),
            "payload": _jsonable(payload),
            "totals": {
                "prompt_tokens": getattr(result, "total_prompt_tokens", None),
                "completion_tokens": getattr(result, "total_completion_tokens", None),
                "cached_tokens": getattr(result, "total_cached_tokens", None),
                "reasoning_tokens": getattr(result, "total_reasoning_tokens", None),
                "lm_seconds": getattr(result, "total_lm_seconds", None),
                "worker_seconds": getattr(result, "total_worker_seconds", None),
            },
            "n_turns": len(turns),
            "turns": turns,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - tracing must never break a benchmark
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"id": task_id, "trace_error": f"{type(exc).__name__}: {exc}"}),
                encoding="utf-8",
            )
        except Exception:
            pass
