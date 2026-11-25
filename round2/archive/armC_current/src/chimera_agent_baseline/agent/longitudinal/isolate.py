"""Temporal isolation: decide which observations the agent is allowed to use.

The rule the whole module rests on: an observation may only enter the
timeline if the agent has already revealed the section that contains it
*and* the observation does not sit after the decision time.  Both checks
are recorded, not assumed -- every rejection carries a reason string that
travels with the timeline into the audit record.
"""

from __future__ import annotations

from dataclasses import replace

from .schema import FIT_HOLD, Observation


def apply_reveal_scope(all_obs: list[Observation], revealed: set[str]) -> tuple[list[Observation], list[dict]]:
    """Drop observations whose source section was never revealed."""
    kept, rejected = [], []
    for o in all_obs:
        if o.section in revealed:
            kept.append(o)
        else:
            rejected.append({
                "section": o.section, "locator": o.locator, "value": o.value,
                "date": o.date, "reason": "SECTION_NOT_REVEALED",
            })
    return kept, rejected


def apply_cutoff(obs: list[Observation], as_of_t: float) -> tuple[list[Observation], list[dict]]:
    """Split observations around the decision time.

    Month-precision rows sitting in the same calendar month as the cutoff are
    kept but flagged: their true day is unknown, so exclusion would be as
    arbitrary as inclusion.
    """
    kept, rejected = [], []
    for o in obs:
        if o.t <= as_of_t:
            if o.precision == "month" and abs(as_of_t - o.t) < 1 / 24:  # same month
                note = (o.notes + " | " if o.notes else "") + "month-precision row on the cutoff month"
                o = replace(o, notes=note)
            kept.append(o)
        else:
            rejected.append({
                "section": o.section, "locator": o.locator, "value": o.value,
                "date": o.date, "reason": "AFTER_CUTOFF",
            })
    return kept, rejected


def mark_unfittable(obs: list[Observation]) -> list[Observation]:
    """Quoted prose values stay visible but never enter the fit.

    Rationale: a value transcribed inside a narrative may be rounded,
    re-stated from memory, or copied from an earlier record we already
    count. Letting it steer the slope would double-count one measurement.
    It still enters as a *display* row so the model sees the discrepancy.
    """
    out = []
    for o in obs:
        if o.nature == "quoted":
            o = replace(o, fit=FIT_HOLD)
        out.append(o)
    return out
