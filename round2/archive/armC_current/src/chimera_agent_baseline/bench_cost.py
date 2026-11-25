"""Benchmark cost recorder for the A-vs-C paired comparison.

Append-only JSONL of model usage. It has no control-flow effect: every call is
guarded, every failure is swallowed, and when ``CHIMERA_BENCH_LOG`` is unset the
module does nothing at all. It exists so both arms report requests, tokens and
wall time from one identical instrument instead of from each arm's own logging.
"""

from __future__ import annotations

import json
import os
import time


def _path():
    return os.environ.get("CHIMERA_BENCH_LOG") or ""


def record(kind: str, **fields) -> None:
    path = _path()
    if not path:
        return
    row = {"ts": time.time(), "kind": kind, **fields}
    try:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001 - measurement must never break a run
        pass


def usage_from_response(response) -> dict:
    """Pull OpenAI ``usage`` out of a dict or SDK BaseModel response."""
    usage = None
    if isinstance(response, dict):
        usage = response.get("usage")
    else:
        usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    get = usage.get if isinstance(usage, dict) else (lambda key: getattr(usage, key, None))
    return {key: get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens") if get(key) is not None}


def mark(kind: str, **fields) -> None:
    """Alias kept for call sites that read better as a marker than a record."""
    record(kind, **fields)
