"""Longitudinal PSA state module -- drop-in for the CHIMERA agent.

    tl = build_timeline(case_json, task=1, revealed={"psa_trend", "laboratory_results"})
    block = to_prompt(tl, level="E2")

Nothing here reads the case file directly: callers pass in the already
revealed sections, so the module cannot widen the information budget.
"""

from __future__ import annotations

import datetime as dt

from .extract import EXTRACTORS, parse_date
from .isolate import apply_cutoff, apply_reveal_scope, mark_unfittable
from .reconcile import reconcile
from .estimate import estimate
from .render import to_prompt, token_estimate
from .schema import Timeline, to_years

__all__ = ["build_timeline", "infer_as_of", "to_prompt", "token_estimate",
           "Timeline", "to_years"]


def infer_as_of(case: dict) -> tuple[str, str]:
    """Best available decision time, plus how it was obtained.

    This is a demo convenience, NOT a validated cutoff. In the real
    pipeline the task definition supplies it; the return value is marked
    unverified so downstream code cannot mistake one for the other.
    """
    dates = []
    rpt = case.get("radiology_report") or ""
    if rpt:
        d = parse_date(rpt)
        if d:
            dates.append(d[0])
    for note in case.get("previous_notes") or []:
        d = parse_date(str(note.get("date", ""))) if isinstance(note, dict) else None
        if d:
            dates.append(d[0])
    for sec in ("psa_trend", "laboratory_results"):
        for row in case.get(sec) or []:
            if isinstance(row, dict):
                d = parse_date(str(row.get("date", "")))
                if d:
                    dates.append(d[0])
    if not dates:
        return dt.date.today().isoformat(), "fallback_today"
    return max(dates).isoformat(), "inferred_from_latest_document_UNVERIFIED"


def build_timeline(case: dict, *, task: int, revealed: set[str] | None = None,
                   as_of: str | None = None, cutoff_verified: bool = False) -> Timeline:
    case_id = str(case.get("case_id") or case.get("id") or "unknown")
    sections_present = {s for s in EXTRACTORS if case.get(s)}
    revealed = sections_present if revealed is None else (set(revealed) & sections_present)

    all_obs = []
    for sec in revealed:
        all_obs.extend(EXTRACTORS[sec](case[sec]))

    warnings: list[str] = []
    if as_of is None:
        as_of, how = infer_as_of(case)
        warnings.append(f"decision time {as_of} ({how})")
    else:
        cutoff_verified = True
    as_of_t = to_years(dt.date.fromisoformat(as_of))

    obs, after = apply_cutoff(all_obs, as_of_t)
    obs = mark_unfittable(obs)
    obs, conflicts, dedup = reconcile(obs)
    est = estimate(obs, as_of_t, conflicts)

    tl = Timeline(case_id=case_id, task=task, as_of=as_of,
                  revealed_sections=sorted(revealed),
                  observations=obs, excluded=after, conflicts=conflicts,
                  dedup=dedup,
                  estimate=est, warnings=warnings)
    if not cutoff_verified:
        tl.warnings.append("historical cutoff not verified against task metadata")
    return tl
