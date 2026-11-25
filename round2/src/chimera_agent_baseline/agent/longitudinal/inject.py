"""Bridge from the agent's tool results to the longitudinal module.

Kept out of graph.py so the injection can be tested without a language
model, and so the main chain holds only a call site.

Payload shapes that actually occur: the MCP tools return
``json.dumps({case_id, field: value})``, but LangChain wraps that in a
content-block list -- ``[{"type": "text", "text": "<json>"}]``. Both are
handled here, plus bare dicts and lists of dicts.
"""

from __future__ import annotations

import json
from typing import Any, Iterator, Mapping

from . import build_timeline, to_prompt

SECTIONS = ("psa_trend", "laboratory_results", "previous_notes", "radiology_report")


def _from_text(text: str) -> Iterator[dict]:
    try:
        parsed = json.loads(text)
    except Exception:
        return
    yield from _iter_payloads(parsed)


def _iter_payloads(raw: Any) -> Iterator[dict]:
    """Recursively unwrap whatever the tool layer hands back.

    Seen in practice: ``json.dumps(payload)`` wrapped as
    ``[{"type": "text", "text": "<json>"}]``. Also handled: bare dicts,
    bare strings, and arbitrary nesting of the above.
    """
    if isinstance(raw, str):
        yield from _from_text(raw)
    elif isinstance(raw, dict):
        if isinstance(raw.get("text"), str) and raw.get("type"):
            yield from _from_text(raw["text"])
        else:
            yield raw
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            yield from _iter_payloads(item)


def collect_case(tool_results: Mapping[str, Any], case_id: str) -> dict | None:
    """Rebuild a minimal case dict from the sections the agent revealed.

    Anything unparsable, or carrying no longitudinal section, is ignored --
    the block is built only from what was actually paid for.
    """
    case: dict = {"case_id": case_id}
    for raw in tool_results.values():
        for payload in _iter_payloads(raw):
            for key in SECTIONS:
                if key in payload:
                    case[key] = payload[key]
    return case if len(case) > 1 else None


def build_block(tool_results: Mapping[str, Any], *, case_id: str, task: int,
                level: str = "E2", as_of: str | None = None) -> str:
    """Return the injection block, or '' when there is nothing to inject."""
    case = collect_case(tool_results, case_id)
    if case is None:
        return ""
    timeline = build_timeline(case, task=task, as_of=as_of)
    return to_prompt(timeline, level)
