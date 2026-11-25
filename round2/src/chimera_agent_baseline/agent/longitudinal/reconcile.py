"""Reconcile observations that fall in the same time window.

Three outcomes, never two:

  SAME_LIKELY  equal values within tolerance -> collapse to one row
  DISTINCT     values incompatible            -> keep both, raise a conflict
  UNKNOWN      one side is prose-quoted       -> keep both, no collapse

Collapsing matters: in the sample case the trend table and the lab panel
both report 21.0 ng/mL about a month apart. Counting those as two rising
points would invent evidence that nobody measured.
"""

from __future__ import annotations

from dataclasses import replace

from .schema import DISTINCT, FIT_HOLD, QUOTED, SAME_LIKELY, UNKNOWN, Observation

TOL_ABS = 0.2      # ng/mL
TOL_REL = 0.02     # 2 %
WINDOW_DAYS = 45
MULTI_DATE_WINDOW_DAYS = 150


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(TOL_ABS, TOL_REL * max(abs(a), abs(b)))


def _days_apart(o1: Observation, o2: Observation) -> float:
    return abs(o1.t - o2.t) * 365.25


def _cluster_by_value(group: list[Observation]) -> list[list[Observation]]:
    clusters: list[list[Observation]] = []
    for o in group:
        for c in clusters:
            if _close(c[0].value, o.value):
                c.append(o)
                break
        else:
            clusters.append([o])
    return clusters


def reconcile(obs: list[Observation]) -> tuple[list[Observation], list[dict], list[dict]]:
    """Collapse duplicate expressions, flag incompatible ones.

    Returns (rows, conflicts, dedup). Rows that lose a collapse stay in the
    output with fit=HOLD so the model can still see that the same number
    appeared in two places -- the point is to stop double counting, not to
    hide evidence.  A same-value/different-date collapse goes to `dedup`, not
    `conflicts`: it is ordinary double expression (a month-resolution trend
    table against a day-resolution lab), and counting it as a conflict would
    flag almost every case and drown the real disagreements.
    """
    obs = sorted(obs, key=lambda o: o.t)
    groups: list[list[Observation]] = []
    for o in obs:
        if groups and _days_apart(groups[-1][-1], o) <= WINDOW_DAYS:
            groups[-1].append(o)
        else:
            groups.append([o])

    out: list[Observation] = []
    conflicts: list[dict] = []
    dedup: list[dict] = []

    for g in groups:
        clusters = _cluster_by_value(g)

        # --- one value, several expressions: collapse ---------------------
        for c in clusters:
            c.sort(key=lambda o: (-o.weight, o.precision != "day"))
            keep = c[0]
            if len(c) == 1:
                out.append(keep)
                continue
            same_date = all(x.date == keep.date for x in c)
            identity = SAME_LIKELY if same_date else "SAME_LIKELY_DIFFERENT_DATE"
            others = ", ".join(f"{x.section}@{x.date_raw}" for x in c[1:])
            out.append(replace(keep, identity=identity,
                               notes=(keep.notes + " | " if keep.notes else "")
                               + f"collapsed with {others}"))
            for x in c[1:]:
                out.append(replace(x, identity=identity, fit=FIT_HOLD))
            if not same_date:
                gaps = [round(_days_apart(keep, x)) for x in c[1:]]
                dedup.append({
                    "type": "date_disagreement_same_value",
                    "value": keep.value,
                    "entries": [{"section": x.section, "date": x.date, "locator": x.locator}
                                for x in c],
                    "reading": ("one number, two reported dates; treat as a single "
                                f"measurement whose date is uncertain by +-{max(gaps)} days"),
                })

        # --- several values in one window: conflict ------------------------
        if len(clusters) > 1:
            reps = [c[0] for c in clusters]
            vals = [r.value for r in reps]
            quoted = any(o.nature == QUOTED for o in g)
            identity = UNKNOWN if quoted else DISTINCT
            out = [replace(o, identity=identity) if o in reps else o for o in out]
            conflicts.append({
                "type": "value_disagreement",
                "window_days": round(max(_days_apart(a, b) for a in g for b in g)),
                "entries": [{"section": r.section, "date": r.date, "value": r.value,
                             "locator": r.locator} for r in reps],
                "spread_ng_ml": round(max(vals) - min(vals), 2),
                "sample_identity": identity,
                "reading": ("two different values inside one window. Sample identity is "
                            "unknown, so the two must not be averaged; the higher one is "
                            "not automatically the true one"),
            })

    # --- same number, different months, same patient ----------------------
    # A value that reappears a few months later in another section may be one
    # measurement quoted twice (the note restating a draw it did not perform).
    # Either way its date is uncertain, and the slope moves depending on which
    # date is true -- so it is recorded rather than resolved.
    for i, a in enumerate(obs):
        for b in obs[i + 1:]:
            if (b.t - a.t) * 365.25 > MULTI_DATE_WINDOW_DAYS:
                break
            if (a.section != b.section and a.date != b.date
                    and _close(a.value, b.value)
                    and (b.t - a.t) * 365.25 > WINDOW_DAYS):
                conflicts.append({
                    "type": "same_value_multi_dated",
                    "value": a.value,
                    "entries": [{"section": a.section, "date": a.date, "locator": a.locator},
                                {"section": b.section, "date": b.date, "locator": b.locator}],
                    "gap_days": round((b.t - a.t) * 365.25),
                    "reading": ("the same number appears under two dates months apart. It may be "
                                "one draw restated in prose, in which case the earlier date is "
                                "wrong, or two draws that happened to match. The trend near this "
                                "window depends on which reading is taken"),
                })

    return sorted(out, key=lambda o: o.t), conflicts, dedup
