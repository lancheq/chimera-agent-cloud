"""Render the timeline for three consumers.

  to_json()      machine record for audit / downstream nodes
  to_prompt(E1)  time order only           -- isolates the sorting effect
  to_prompt(E2)  + source, precision, conflicts, direction
  to_prompt(E3)  + slope, CI, doubling time, level estimate

Keeping E1/E2/E3 as flags on one renderer is what makes the ablation run
on identical inputs with identical access; only the injected block differs.
"""

from __future__ import annotations

from .schema import FIT_HOLD, QUOTED, Timeline

FENCE = "=== LONGITUDINAL PSA EVIDENCE"

_CONSTRAINTS = (
    "Constraints on use:\n"
    "- Labels [raw]/[quoted]/[estimated] are not interchangeable. A quoted value "
    "restates a number found in prose; an estimated value is a model output, not a measurement.\n"
    "- A conflict means the two values may come from different samples. Do not average "
    "them and do not silently prefer the higher one.\n"
    "- PSA change alone does not establish tumour stage, grade or progression. Any stage "
    "claim needs imaging or pathology support.\n"
)


def _row(o, level: str) -> str:
    tag = "[raw]" if o.nature != QUOTED else "[quoted]"
    if level == "E1":
        return f"{o.date}  {o.value:g}"
    prec = "month-precision" if o.precision == "month" else "day-precision"
    head = f"{o.date}  {o.value:g} ng/mL  {tag} {o.section} ({prec})"
    if o.fit == FIT_HOLD:
        head += "  [not used in fit]"
    return head


def _conflict_lines(tl: Timeline) -> list[str]:
    lines = []
    for c in tl.conflicts:
        ents = " vs ".join(
            f"{e.get('value', c.get('value', ''))} @{e['date']} ({e['section']})" for e in c["entries"])
        lines.append(f"  ! {c['type']}: {ents} -- {c['reading']}")
    return lines


def to_prompt(tl: Timeline, level: str = "E2") -> str:
    if level not in ("E1", "E2", "E3"):
        raise ValueError(level)
    obs = [o for o in tl.sorted_obs()]
    est = tl.estimate or {}

    if level == "E1":
        rows = sorted(tl.observations, key=lambda o: (o.t, o.value))
        body = "\n".join(_row(o, "E1") for o in rows) or "  (no PSA observations available)"
        return f"{FENCE} (time order only)\n{body}\n"

    head = (f"{FENCE}  ({est.get('n_fit_points', 0)} usable points, "
            f"{len(tl.conflicts)} recorded issue(s), as of {tl.as_of})")
    body = "\n".join(_row(o, level) for o in obs) or "  (no PSA observations available)"

    trend = [f"Direction: {est.get('direction')} ({est.get('direction_basis')})."]
    if est.get("slope_ng_ml_per_year") is not None and level == "E3":
        lo, hi = est.get("slope_ci95", [None, None])
        trend.append(f"Slope: {est['slope_ng_ml_per_year']} ng/mL/yr, "
                     f"95% bootstrap CI [{lo}, {hi}] (Theil-Sen, n={est['n_fit_points']}).")
        if est.get("doubling_time_months"):
            trend.append(f"PSA doubling time ~{est['doubling_time_months']} months (illustrative).")
        lv = est.get("level_estimate")
        if lv:
            trend.append(f"Estimated level at decision time: {lv['value']} ng/mL "
                         f"[{lv['ci95'][0]}, {lv['ci95'][1]}] (estimated, not measured).")
        else:
            trend.append("No current-value estimate: last measurement is too far from "
                         "the decision time to extrapolate.")
        if est.get("piecewise"):
            pw = est["piecewise"]
            trend.append(f"Piecewise fit favoured a change of direction at point "
                         f"{pw['break_index']} (slopes {pw['slopes']}).")

    warn = [f"- {w}" for w in est.get("warnings", [])]
    if level == "E2":
        warn = [w for w in warn if "doubling time" not in w and "stable slope" not in w]
    sens = est.get("conflict_sensitivity") or []
    sens_lines = []
    for s in sens:
        if level == "E3":
            alts = ", ".join(f"{v['assume_value']} -> {v['slope_ng_ml_per_year']} ng/mL/yr"
                             for v in s["variants"])
            sens_lines.append(f"  ~ slope sensitivity at {s['window_dates']}: {alts} "
                              f"(spread {s['slope_spread']})")
        else:
            verdict = ("direction holds" if s.get("direction_stable")
                       else "direction would flip")
            sens_lines.append(f"  ~ at {s['window_dates']} the rival value changes the "
                              f"slope, but the {verdict}")
    parts = [head, body, "", *trend, ""]
    if tl.dedup:
        n = sum(len(d["entries"]) - 1 for d in tl.dedup)
        parts += [f"Deduplicated: {n} repeated expression(s) collapsed -- the same value "
                  f"printed in two sections, counted once.", ""]
    if tl.conflicts:
        parts += ["Conflicts:", *_conflict_lines(tl), ""]
    if sens_lines:
        parts += ["Conflict impact:", *sens_lines, ""]
    if warn:
        parts += ["Caveats:", *warn, ""]
    if level == "E3":
        parts += [_CONSTRAINTS]
    elif level == "E2":
        parts += [_CONSTRAINTS.replace(
            "- Labels [raw]/[quoted]/[estimated] are not interchangeable. A quoted value "
            "restates a number found in prose; an estimated value is a model output, not a measurement.\n",
            "- Labels [raw]/[quoted] are not interchangeable; a quoted value restates a number "
            "found in prose and may be rounded or copied.\n")]
    return "\n".join(parts)


def token_estimate(text: str) -> int:
    """Rough token count: ~4 chars/token for clinical English."""
    return max(1, round(len(text) / 4))
