"""Robust trend / level estimation on a handful of irregular PSA points.

Three deliberate choices:

1. Theil-Sen as the primary slope. With 4-7 points and one possible
   transcription error, a median-of-pairwise-slopes is far safer than OLS.
2. Uncertainty is bootstrapped, and month-precision dates are jittered
   inside their month during resampling -- the interval then carries date
   imprecision, not just sampling noise.
3. No point estimate of "current PSA" unless the last observation is close
   to the decision time. Extrapolating three months past the last draw and
   printing a number invites the model to read it as a measurement.
"""

from __future__ import annotations

import math
import random
import statistics

from .schema import QUOTED, Observation

BOOTSTRAP_N = 1000
SEED = 20261124
MAX_EXTRAPOLATION_YEARS = 0.35      # ~4 months
PIECEWISE_MIN_N = 5


def _ols(ts, ys, ws):
    sw = sum(ws)
    mt = sum(w * t for w, t in zip(ws, ts)) / sw
    my = sum(w * y for w, y in zip(ws, ys)) / sw
    sxx = sum(w * (t - mt) ** 2 for w, t in zip(ws, ts))
    if sxx == 0:
        return None
    b = sum(w * (t - mt) * (y - my) for w, t, y in zip(ws, ts, ys)) / sxx
    a = my - b * mt
    ssr = sum(w * (y - (a + b * t)) ** 2 for w, t, y in zip(ws, ts, ys))
    return a, b, ssr


def _theil_sen(ts, ys):
    slopes = [(ys[j] - ys[i]) / (ts[j] - ts[i])
              for i in range(len(ts)) for j in range(i + 1, len(ts))
              if ts[j] != ts[i]]
    if not slopes:
        return None
    b = statistics.median(slopes)
    a = statistics.median(y - b * t for t, y in zip(ts, ys))
    return a, b


def _jitter(o: Observation, rng: random.Random) -> float:
    if o.precision == "month":
        return o.t + rng.uniform(-15, 15) / 365.25
    if o.precision == "year":
        return o.t + rng.uniform(-182, 182) / 365.25
    return o.t


def _bootstrap(fit_rows, as_of_t, rng):
    slopes, preds = [], []
    n = len(fit_rows)
    for _ in range(BOOTSTRAP_N):
        sample = [fit_rows[rng.randrange(n)] for _ in range(n)]
        ts = [_jitter(o, rng) for o in sample]
        ys = [o.value for o in sample]
        est = _theil_sen(ts, ys)
        if est is None:
            continue
        a, b = est
        slopes.append(b)
        preds.append(a + b * as_of_t)
    return slopes, preds


def _pct(sorted_vals, q):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * q
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _direction(fit_rows, slope, ci):
    vals = [o.value for o in fit_rows]
    diffs = [b - a for a, b in zip(vals, vals[1:])]
    rising = all(d > 0 for d in diffs)
    falling = all(d < 0 for d in diffs)
    rel = (vals[-1] - vals[0]) / vals[0] if vals[0] else None
    ci_lo, ci_hi = ci
    if rising and rel is not None and rel >= 0.25:
        return "rising", "monotonic_increase"
    if falling and rel is not None and rel <= -0.25:
        return "falling", "monotonic_decrease"
    if ci_lo is not None and ci_lo > 0:
        return "rising", "slope_ci_excludes_zero"
    if ci_hi is not None and ci_hi < 0:
        return "falling", "slope_ci_excludes_zero"
    return "indeterminate", "slope_ci_includes_zero"


def _piecewise(ts, ys, ws):
    n = len(ts)
    if n < PIECEWISE_MIN_N:
        return None
    base = _ols(ts, ys, ws)
    if base is None:
        return None
    best = None
    for k in range(2, n - 1):
        left = _ols(ts[:k], ys[:k], ws[:k])
        right = _ols(ts[k:], ys[k:], ws[k:])
        if left is None or right is None:
            continue
        ssr = left[2] + right[2]
        gain = 1 - ssr / base[2] if base[2] > 0 else 0
        if best is None or ssr < best["ssr"]:
            best = {"ssr": ssr, "k": k, "gain": gain,
                    "slopes": [left[1], right[1]], "break_date": ts[k]}
    if best and best["gain"] >= 0.40 and best["slopes"][0] * best["slopes"][1] < 0:
        return {"break_index": best["k"], "ssr_gain": round(best["gain"], 3),
                "slopes": [round(s, 3) for s in best["slopes"]]}
    return None


def _sensitivity(fit_rows, conflicts, as_of_t):
    """Re-fit with each rival value in a conflicting window.

    Answers the only question a clinician asks about a disputed number:
    does it change the direction, or just the steepness?
    """
    out = []
    if not conflicts or len(fit_rows) < 2:
        return out
    ts = [o.t for o in fit_rows]
    ys = [o.value for o in fit_rows]
    for c in conflicts:
        if c.get("type") != "value_disagreement":
            continue
        dates = {e["date"] for e in c.get("entries", [])}
        vals = [e["value"] for e in c.get("entries", []) if e.get("value") is not None]
        hit = {i for i, o in enumerate(fit_rows) if o.date in dates}
        if not hit or len(vals) < 2:
            continue
        variants = []
        for alt in vals:
            y2 = [alt if i in hit else y for i, y in enumerate(ys)]
            e2 = _theil_sen(ts, y2)
            if e2:
                variants.append({"assume_value": alt, "slope_ng_ml_per_year": round(e2[1], 3)})
        if len(variants) > 1:
            slopes = [v["slope_ng_ml_per_year"] for v in variants]
            out.append({
                "window_dates": sorted(dates),
                "variants": variants,
                "slope_spread": round(max(slopes) - min(slopes), 3),
                "direction_stable": max(slopes) * min(slopes) > 0,
            })
    return out


def estimate(rows: list[Observation], as_of_t: float, conflicts: list[dict] | None = None) -> dict:
    fit_rows = [o for o in rows if o.fit == "FIT" and o.nature != QUOTED]
    quoted_rows = [o for o in rows if o.nature == QUOTED]
    base = {
        "n_fit_points": len(fit_rows),
        "n_quoted_points": len(quoted_rows),
        "method": None,
        "slope_ng_ml_per_year": None,
        "slope_ci95": [None, None],
        "direction": "unknown",
        "direction_basis": "insufficient_points",
        "level_estimate": None,
        "doubling_time_months": None,
        "piecewise": None,
        "conflict_sensitivity": [],
        "reliability": "insufficient",
        "warnings": [],
    }
    if len(fit_rows) < 2:
        base["warnings"].append(
            "fewer than two comparable raw measurements; no trend is estimable")
        return base

    ts = [o.t for o in fit_rows]
    ys = [o.value for o in fit_rows]
    ws = [o.weight for o in fit_rows]
    span = max(ts) - min(ts)
    base["span_years"] = round(span, 2)

    rng = random.Random(SEED)
    slopes, preds = _bootstrap(fit_rows, as_of_t, rng)
    sf = sorted(slopes)
    ci = (_pct(sf, 0.025), _pct(sf, 0.975))
    ts_est = _theil_sen(ts, ys)
    ols_est = _ols(ts, ys, ws)

    base.update({
        "method": "theil_sen",
        "slope_ng_ml_per_year": round(ts_est[1], 3),
        "slope_ci95": [None if ci[0] is None else round(ci[0], 3),
                       None if ci[1] is None else round(ci[1], 3)],
        "slope_ols_per_year": round(ols_est[1], 3) if ols_est else None,
    })
    direction, basis = _direction(fit_rows, ts_est[1], ci)
    base["direction"], base["direction_basis"] = direction, basis

    # log-linear growth -> PSA doubling time (clinical convention)
    if all(v > 0 for v in ys):
        log_rows = [_LogRow(o.t, math.log(o.value), o.weight) for o in fit_rows]
        est = _ols([r.t for r in log_rows], [r.y for r in log_rows], [r.w for r in log_rows])
        if est and est[1] > 0:
            dt_months = math.log(2) / est[1] * 12
            base["doubling_time_months"] = round(dt_months, 1)
            if len(fit_rows) < 5 or span < 1.0:
                base["warnings"].append(
                    "doubling time from {} points over {:.1f} y; treat as illustrative only"
                    .format(len(fit_rows), span))

    # level estimate, only when the gap to decision time is short
    gap = as_of_t - max(ts)
    if gap <= MAX_EXTRAPOLATION_YEARS:
        p = sorted(preds)
        base["level_estimate"] = {
            "value": round(ts_est[0] + ts_est[1] * as_of_t, 2),
            "ci95": [round(_pct(p, 0.025), 2), round(_pct(p, 0.975), 2)],
            "gap_years": round(gap, 3),
            "status": "interpolated",
        }
    else:
        base["level_estimate"] = None
        base["warnings"].append(
            "last measurement is {:.1f} months before the decision time; "
            "no current-value estimate is offered".format(gap * 12))

    base["piecewise"] = _piecewise(ts, ys, ws)

    if len(fit_rows) >= 5 and span >= 1.0:
        base["reliability"] = "moderate"
    else:
        base["reliability"] = "low"
        base["warnings"].append(
            "{} points over {:.1f} y -- below the usual threshold for a stable slope"
            .format(len(fit_rows), span))
    if quoted_rows:
        base["warnings"].append(
            "{} prose-quoted value(s) shown but excluded from the fit "
            "(they may restate a measurement already counted)".format(len(quoted_rows)))
    base["conflict_sensitivity"] = _sensitivity(fit_rows, conflicts or [], as_of_t)
    return base


class _LogRow:
    __slots__ = ("t", "y", "w")

    def __init__(self, t, y, w):
        self.t, self.y, self.w = t, y, w
