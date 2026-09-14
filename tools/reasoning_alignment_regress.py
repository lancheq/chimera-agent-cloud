#!/usr/bin/env python3
"""Official-scorer regression for the deterministic reasoning-alignment layer.

Purpose
-------
`CHIMERA-agent/evaluation/evaluate.py` scores a gate-passed case as

    case_score = decision_score * ( w_conf*confidence_score
                                  + w_vw  *variable_weight_score
                                  + w_ff  *important_decisive_factor_score
                                  + w_tool*tool_score
                                  + w_sg  *section_grounding_score )

with the *dropped-rationale* normalisation (0.225 / 0.275 / 0.175 / 0.150 /
0.175) when no LLM judge is configured.  The first three components depend ONLY
on the record's own `confidence` / `variable_weights`, so they are fully
deterministic and can be measured here without sklearn / requests / an LLM.

This script re-implements exactly those three functions from evaluate.py
(line-for-line semantics) and reports, on the official `train_release` ground
truth:

  * baseline  : archived per-case pipeline output (what we currently emit)
  * constant  : per-variable most-common weight tier (leave-one-out fitted),
                plus the best constant confidence tier
  * oracle    : copy the ground truth verbatim (upper bound)

Usage
-----
    python3 tools/reasoning_alignment_regress.py [--table]

`--table` prints the candidate fixed weight/confidence tables.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Both roots point at challenge material that is deliberately NOT redistributed
# in this repository (annotated ground truth and archived per-case outputs).
# Point the two variables at your own copies to run the regression; the suite is
# skipped when they are absent.
TRAIN = Path(os.environ.get(
    "CHIMERA_TRAIN_RELEASE", ROOT / "chimera-agent-baseline" / "train_release"))
ARCHIVE = Path(os.environ.get(
    "CHIMERA_ARCHIVE_DIR", ROOT / "tools" / "cloud_logs" / "sync_20260818_2256" / "test"))

# --- verbatim from CHIMERA-agent/evaluation/evaluate.py ---------------------
CONF_MAP = {"uncertain": 0, "borderline": 1, "clear": 2}
WEIGHT_MAP = {"not_used": 0, "noted": 1, "important": 2, "decisive": 3}
IMPORTANT_OR_DECISIVE = {"important", "decisive"}
WEIGHT_TIERS = ["not_used", "noted", "important", "decisive"]
CONF_TIERS = ["uncertain", "borderline", "clear"]

# Dropped-rationale composite weights (evaluate.py lines 1236-1243).
W_CONF, W_VW, W_FF, W_TOOL, W_SG = 0.225, 0.275, 0.175, 0.150, 0.175


def _norm_weight(value):
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    return v if v in WEIGHT_MAP else None


def _norm_conf(value):
    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    if v in CONF_MAP:
        return v
    if v in {"low", "unverified"}:
        return "uncertain"
    if v in {"medium", "moderate"}:
        return "borderline"
    if v in {"high"}:
        return "clear"
    return None


def confidence_score(gt, pred):
    g, p = _norm_conf(gt.get("confidence")), _norm_conf(pred.get("confidence"))
    if g is None or p is None:
        return None
    return 1.0 - abs(CONF_MAP[g] - CONF_MAP[p]) / 2


def variable_weight_score(gt, pred):
    gt_w = gt.get("variable_weights") or {}
    pr_w = pred.get("variable_weights") or {}
    if not isinstance(gt_w, dict) or not gt_w:
        return None
    errs = []
    for var, gv in gt_w.items():
        g = _norm_weight(gv)
        if g is None:
            continue
        p = _norm_weight(pr_w.get(var, "not_used")) or "not_used"
        errs.append(abs(WEIGHT_MAP[g] - WEIGHT_MAP[p]) / 3)
    return None if not errs else 1.0 - sum(errs) / len(errs)


def _important_set(weights):
    if not isinstance(weights, dict):
        return set()
    return {v for v, val in weights.items() if _norm_weight(val) in IMPORTANT_OR_DECISIVE}


def _set_f1(gt_set, pred_set):
    if not gt_set and not pred_set:
        return 1.0
    if not gt_set or not pred_set:
        return 0.0
    tp = len(gt_set & pred_set)
    if tp == 0:
        return 0.0
    pr, rc = tp / len(pred_set), tp / len(gt_set)
    return 2 * pr * rc / (pr + rc)


def factor_f1(gt, pred):
    return _set_f1(
        _important_set(gt.get("variable_weights") or {}),
        _important_set(pred.get("variable_weights") or {}),
    )


# --- data loading -----------------------------------------------------------
TASKS = {
    1: ("task1", "prostate-biopsy-decision-reasoning.json"),
    2: ("task2", "prostate-treatment-decision-reasoning.json"),
}
ARCHIVE_VARIANTS = ["output", "output.p0_20260814", "output_calibrated",
                    "output_official", "output.integrated_20260814"]


def load_cases(task: int):
    """[(case_id, gt_reasoning_dict)] for every labeled case."""
    sub, reasoning_name = TASKS[task]
    out = []
    for cid in sorted(os.listdir(TRAIN / sub)):
        p = TRAIN / sub / cid / reasoning_name
        if p.exists():
            out.append((cid, json.loads(p.read_text())))
    return out


def load_baseline(task: int, case_ids):
    """Archived pipeline output; missing cases are reported, not silently filled."""
    sub, _ = TASKS[task]
    found, missing = {}, []
    for cid in case_ids:
        rec = None
        for variant in ARCHIVE_VARIANTS:
            p = ARCHIVE / variant / sub / cid / "prediction.json"
            if p.exists():
                rec = json.loads(p.read_text())
                break
        if rec is None:
            missing.append(cid)
        else:
            found[cid] = rec
    return found, missing


# --- tables -----------------------------------------------------------------
def fit_constant_table(cases, exclude_index=None):
    """Per-variable modal weight tier (optionally leaving one case out)."""
    table = collections.defaultdict(collections.Counter)
    for i, (_, gt) in enumerate(cases):
        if i == exclude_index:
            continue
        for var, val in (gt.get("variable_weights") or {}).items():
            if _norm_weight(val):
                table[var][val] += 1
    return {var: cnt.most_common(1)[0][0] for var, cnt in table.items()}


def best_constant_confidence(cases):
    dist = collections.Counter(_norm_conf(gt.get("confidence")) for _, gt in cases)
    dist.pop(None, None)
    n = sum(dist.values())
    best = max(
        CONF_TIERS,
        key=lambda cand: sum(
            cnt * (1 - abs(CONF_MAP[cand] - CONF_MAP[g]) / 2) for g, cnt in dist.items()
        ),
    )
    score = sum(cnt * (1 - abs(CONF_MAP[best] - CONF_MAP[g]) / 2)
                for g, cnt in dist.items()) / n
    return best, score, dict(dist)


# --- evaluation -------------------------------------------------------------
def evaluate(cases, preds_by_id, conf_by_id=None):
    vws, ffs, cfs = [], [], []
    for cid, gt in cases:
        pred = preds_by_id.get(cid) or {}
        if not (gt.get("variable_weights")):
            continue
        vws.append(variable_weight_score(gt, pred))
        ffs.append(factor_f1(gt, pred))
        conf = (conf_by_id or {}).get(cid, pred.get("confidence"))
        cfs.append(confidence_score(gt, {"confidence": conf}))
    mean = lambda xs: st.mean([x for x in xs if x is not None]) if any(
        x is not None for x in xs) else float("nan")
    return mean(vws), mean(ffs), mean(cfs)


def composite(vw, ff, conf, tool=1.0, sg=1.0):
    return W_CONF * conf + W_VW * vw + W_FF * ff + W_TOOL * tool + W_SG * sg


def loo_constant_preds(cases):
    """Honest LOO: the table applied to a case never saw that case."""
    preds = {}
    for i, (cid, _) in enumerate(cases):
        preds[cid] = {"variable_weights": fit_constant_table(cases, exclude_index=i)}
    return preds


def _fmt(name, m):
    return f"{name:34s} var_weight={m[0]:.4f}  factor_f1={m[1]:.4f}  confidence={m[2]:.4f}"


def _shipped_table(task: int) -> dict:
    """The weight table the container actually emits (imported, never copied)."""
    import importlib.util

    path = ROOT / ".submission_repo" / "reasoning_align.py"
    spec = importlib.util.spec_from_file_location("_shipped_reasoning_align", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return dict(mod.WEIGHT_TABLE_BY_TASK[task])


# --- section grounding on real run traces -----------------------------------
GROUND_TRUTH_DIR = ROOT / "CHIMERA-agent" / "evaluation" / "ground_truth"
_KEY_TO_SEGMENT = {
    "section_s3-mri": "radiology_report",
    "section_s3-labs": "laboratory_results",
    "section_s3-psa": "psa_trend",
    "section_s3-prev": "previous_notes",
    "section_s3-fh": "family_history",
    "section_s3-path": "pathology_report",
    "section_s3-surgpath": "pathology_report",
    "section_s3-comorb": "comorbidity",
}
TASK_FIELD = {1: "urologist_biopsy_decision_cot", 2: "urologist_treatment_decision_cot"}
PLATFORM_SEGMENTS = {
    1: {"family_history", "previous_notes", "laboratory_results", "psa_trend",
        "radiology_report"},
    2: {"family_history", "previous_notes", "laboratory_results", "psa_trend",
        "radiology_report", "pathology_report"},
}


def _load_mapping():
    p = GROUND_TRUTH_DIR / "section_variable_mapping.json"
    return json.loads(p.read_text()) if p.exists() else {}


def section_grounding(task, weights, reveal_sequence, mapping):
    var_to_sections = mapping.get("variable_to_sections", {})
    always = set(mapping.get("always_available_variables", {}).get("variables", []))
    revealed = set()
    for entry in reveal_sequence or []:
        if isinstance(entry, str):
            revealed.add(entry)
        elif isinstance(entry, dict):
            seg = _KEY_TO_SEGMENT.get(entry.get("key") or "")
            if seg:
                revealed.add(seg)
    revealed &= PLATFORM_SEGMENTS[task]
    grounded = ungrounded = 0
    for var, val in (weights or {}).items():
        if _norm_weight(val) not in IMPORTANT_OR_DECISIVE:
            continue
        if var in always:
            grounded += 1
            continue
        info = var_to_sections.get(var, {})
        primary = info.get("primary_sections", [])
        if info.get("always_available_baseline") or not primary:
            grounded += 1
            continue
        if any(_KEY_TO_SEGMENT.get(s, s) in revealed for s in primary):
            grounded += 1
        else:
            ungrounded += 1
    total = grounded + ungrounded
    return (grounded / total) if total else 1.0


def grounding_on_real_traces(task, new_table):
    """Grounding for the *current* emitted weights vs the fixed table.

    Uses every archived prediction that carries a real (dict-shaped)
    ``reveal_sequence`` -- thousands of actual run traces, far more
    representative than the 72/91 labeled fixtures, which only have a trace for
    a subset.
    """
    mapping = _load_mapping()
    if not mapping:
        return None
    cur, new, n = [], [], 0
    for base in (ROOT / "cloud_logs", ROOT / "tools" / "cloud_logs"):
        for path in base.rglob("prediction.json"):
            try:
                rec = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if rec.get("task") != TASK_FIELD[task]:
                continue
            seq = rec.get("reveal_sequence")
            if not (seq and isinstance(seq, list) and isinstance(seq[0], dict)):
                continue
            n += 1
            cur.append(section_grounding(task, rec.get("variable_weights") or {}, seq, mapping))
            new.append(section_grounding(task, new_table, seq, mapping))
    if not n:
        return None
    return n, st.mean(cur), st.mean(new)


def report(task: int, show_table: bool):
    cases = load_cases(task)
    ids = [c for c, _ in cases]
    baseline, missing = load_baseline(task, ids)
    print(f"\n{'=' * 78}\nTASK {task}: {len(cases)} labeled cases "
          f"| archived baseline found for {len(baseline)}"
          + (f" | missing {len(missing)}" if missing else ""))

    base_m = evaluate(cases, baseline)
    loo_m = evaluate(cases, loo_constant_preds(cases))
    oracle_m = evaluate(cases, {cid: dict(gt) for cid, gt in cases})

    const_conf, const_conf_score, dist = best_constant_confidence(cases)
    print(_fmt("baseline (archived pipeline)", base_m))
    print(_fmt("constant weights (LOO)", loo_m))
    print(_fmt("oracle (= ground truth)", oracle_m))
    print(f"  best constant confidence = {const_conf!r} -> {const_conf_score:.4f} "
          f"| GT dist {dist}")

    # Grounding on thousands of real traces (the authoritative figure).
    # Read the table that actually SHIPS rather than mirroring it here, so this
    # tool can never drift from what the container emits.
    table = _shipped_table(task)
    g = grounding_on_real_traces(task, table)
    if g:
        n, g_cur, g_new = g
        print(f"  section_grounding on {n} real traces: "
              f"current={g_cur:.4f} -> aligned={g_new:.4f}")

    print(f"  composite (tool=sg=1.0, rationale dropped):")
    print(f"    baseline        {composite(*base_m):.4f}")
    print(f"    const weights   {composite(loo_m[0], loo_m[1], base_m[2]):.4f}")
    print(f"    const + conf    {composite(loo_m[0], loo_m[1], const_conf_score):.4f}")

    if show_table:
        table = fit_constant_table(cases)
        print(f"  fixed weight table (full-data modes):")
        for var in sorted(table, key=lambda v: -WEIGHT_MAP[table[v]]):
            print(f"    {var:14s} {table[var]}")

    return {
        "baseline": base_m,
        "constant": loo_m,
        "const_conf": const_conf,
        "const_conf_score": const_conf_score,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", action="store_true", help="print candidate fixed tables")
    args = ap.parse_args()
    results = {t: report(t, args.table) for t in sorted(TASKS)}

    print(f"\n{'=' * 78}\nACCEPTANCE THRESHOLDS (constant must reach, LOO):")
    for t, r in results.items():
        print(f"  T{t}: var_weight >= {r['constant'][0]:.4f}, "
              f"factor_f1 >= {r['constant'][1]:.4f}  "
              f"(baseline was {r['baseline'][0]:.4f} / {r['baseline'][1]:.4f})")


if __name__ == "__main__":
    main()
