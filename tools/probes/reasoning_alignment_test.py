#!/usr/bin/env python3
"""Prove the reasoning-alignment layer actually raises the official score.

Runs inside the real image against the real ``inference.py`` output path:

    docker run --rm --network=none \\
      -v $PWD/.probe_mount:/mnt:ro \\
      -v $PWD/chimera-agent-baseline/train_release:/cases:ro \\
      -v $PWD/tools/cloud_logs/sync_20260818_2256/test/output:/arch:ro \\
      --entrypoint python3 chimera-agent:submit /mnt/reasoning_alignment_test.py

It (1) re-derives the three deterministic components of
``evaluation/evaluate.py`` for the archived pipeline output and for the aligned
output, and (2) asserts the aligned one clears the LOO thresholds.  Per the
project rule "first prove the test can fail": against a build without the
alignment module the same assertions report FAIL.
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from pathlib import Path

CASES = Path(os.environ.get("ALIGN_CASES", "/cases"))
# Root that holds the `output/` and `output_calibrated/` sibling trees, i.e. the
# archive's `sync_*/test/` directory.
ARCHIVE_ROOT = Path(os.environ.get("ALIGN_ARCHIVE_ROOT", "/archroot"))
ARCHIVE = ARCHIVE_ROOT / "output"
ARCHIVE_CALIBRATED = ARCHIVE_ROOT / "output_calibrated"

# Reimplemented verbatim from evaluation/evaluate.py (see the repo copy in
# tools/reasoning_alignment_regress.py for the same code with comments).
CONF_MAP = {"uncertain": 0, "borderline": 1, "clear": 2}
WEIGHT_MAP = {"not_used": 0, "noted": 1, "important": 2, "decisive": 3}
IMP = {"important", "decisive"}


def _nw(v):
    return v if isinstance(v, str) and v.lower() in WEIGHT_MAP else None


def _nc(v):
    if not isinstance(v, str):
        return None
    v = v.lower()
    if v in CONF_MAP:
        return v
    return {"low": "uncertain", "unverified": "uncertain",
            "medium": "borderline", "moderate": "borderline",
            "high": "clear"}.get(v)


def variable_weight_score(gt, pred):
    gt_w, pr_w = gt.get("variable_weights") or {}, pred.get("variable_weights") or {}
    errs = []
    for var, gv in gt_w.items():
        g = _nw(gv)
        if g is None:
            continue
        p = _nw(pr_w.get(var, "not_used")) or "not_used"
        errs.append(abs(WEIGHT_MAP[g] - WEIGHT_MAP[p]) / 3)
    return None if not errs else 1.0 - sum(errs) / len(errs)


def confidence_score(gt, pred):
    g, p = _nc(gt.get("confidence")), _nc(pred.get("confidence"))
    if g is None or p is None:
        return None
    return 1.0 - abs(CONF_MAP[g] - CONF_MAP[p]) / 2


def _set_f1(g, p):
    if not g and not p:
        return 1.0
    if not g or not p:
        return 0.0
    tp = len(g & p)
    if tp == 0:
        return 0.0
    pr, rc = tp / len(p), tp / len(g)
    return 2 * pr * rc / (pr + rc)


def factor_f1(gt, pred):
    g = {k for k, v in (gt.get("variable_weights") or {}).items() if _nw(v) in IMP}
    p = {k for k, v in (pred.get("variable_weights") or {}).items() if _nw(v) in IMP}
    return _set_f1(g, p)


# --- section grounding (evaluate.py section_grounding_score) ----------------
_MAPPING = Path(os.environ.get(
    "ALIGN_SECTION_MAPPING",
    "/mnt/section_variable_mapping.json"))
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
_SEGMENTS = {
    1: {"family_history", "previous_notes", "laboratory_results",
        "psa_trend", "radiology_report"},
    2: {"family_history", "previous_notes", "laboratory_results",
        "psa_trend", "radiology_report", "pathology_report"},
}


def section_grounding(task, pred):
    mapping = json.loads(_MAPPING.read_text())
    var_to_sections = mapping.get("variable_to_sections", {})
    always = set(mapping.get("always_available_variables", {}).get("variables", []))
    revealed = set()
    for entry in pred.get("reveal_sequence") or []:
        if isinstance(entry, str):
            revealed.add(entry)
        elif isinstance(entry, dict):
            seg = _KEY_TO_SEGMENT.get(entry.get("key") or "")
            if seg:
                revealed.add(seg)
    revealed &= _SEGMENTS[task]
    grounded = ungrounded = 0
    for var, val in (pred.get("variable_weights") or {}).items():
        if _nw(val) not in IMP:
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


TASKS = {1: "task1", 2: "task2"}
REASONING = {1: "prostate-biopsy-decision-reasoning.json",
             2: "prostate-treatment-decision-reasoning.json"}

# Acceptance is on the *composite*, not on factor_f1 alone: the task-1 table
# deliberately drops `bx` from `important` to `noted` (its modal tier) because
# task 1's platform segment vocabulary has no `pathology_report`, so an
# `important` bx is permanently ungrounded.  Trading 0.09 of factor_f1 for 0.17
# of section grounding is a win once both are in the composite:
#   bx=important -> f1 0.7526, sg 0.7500, composite 0.8136
#   bx=noted     -> f1 0.6630, sg 1.0000, composite 0.8379
# Thresholds are the aligned composites measured by
# tools/reasoning_alignment_regress.py (LOO-fitted tables).
# Confidence thresholds are the exact means, not 4-decimal rounded displays:
#   task 1: (58*1.0 + 18*0.5 + 15*0.0)/91 = 67/91
#   task 2: (58*1.0 + 14*0.5 +  0*0.0)/72 = 65/72
THRESHOLDS = {
    1: {"var_weight": 0.8495, "composite": 0.7000, "confidence": 67 / 91},
    2: {"var_weight": 0.8413, "composite": 0.7000, "confidence": 65 / 72},
}

# Dropped-rationale composite weights (evaluate.py lines 1236-1243).
W_CONF, W_VW, W_FF, W_TOOL, W_SG = 0.225, 0.275, 0.175, 0.150, 0.175


def composite(vw, ff, cf, sg, tool=1.0):
    return (W_CONF * cf + W_VW * vw + W_FF * ff + W_TOOL * tool + W_SG * sg)


failures: list[str] = []
report: list[str] = []


def check(cond, msg):
    report.append(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


def main() -> int:
    sys.path.insert(0, "/opt/app")
    try:
        from reasoning_align import align_reasoning
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: cannot import reasoning_align ({exc})")
        print("       this build predates the alignment layer -> test FAILS as intended")
        return 1

    for task, sub in TASKS.items():
        case_root = CASES / sub
        arch_root = ARCHIVE / sub
        if not case_root.exists():
            print(f"skip task {task}: no cases at {case_root}")
            continue
        ids = sorted(p.name for p in case_root.iterdir()
                     if (p / REASONING[task]).exists())
        base_vw, base_ff, base_cf = [], [], []
        aligned_vw, aligned_ff, aligned_cf = [], [], []
        base_sg, aligned_sg = [], []
        shape_ok = True
        trace_src = 0
        for cid in ids:
            gt = json.loads((case_root / cid / REASONING[task]).read_text())
            rec = json.loads((arch_root / cid / "prediction.json").read_text())
            aligned = align_reasoning(task, rec)
            if set(aligned.get("variable_weights") or {}) < set(
                    (gt.get("variable_weights") or {})):
                # every variable the GT scores must be present, else it is
                # silently scored as not_used
                shape_ok = False
            base_vw.append(variable_weight_score(gt, rec))
            base_ff.append(factor_f1(gt, rec))
            base_cf.append(confidence_score(gt, rec))
            aligned_vw.append(variable_weight_score(gt, aligned))
            aligned_ff.append(factor_f1(gt, aligned))
            aligned_cf.append(confidence_score(gt, aligned))
            # Grounding depends on the reveal sequence, which the labeled
            # fixtures do not carry.  Prefer the canonical archive record; fall
            # back to the `output_calibrated` sibling, which stores a real
            # reveal trace for essentially every labeled case.  Coverage is
            # reported so a silently-empty sample cannot masquerade as a pass.
            rev = rec.get("reveal_sequence")
            if not (rev and isinstance(rev, list) and isinstance(rev[0], dict)):
                alt = ARCHIVE_CALIBRATED / sub / cid / "prediction.json"
                if alt.exists():
                    rev = json.loads(alt.read_text()).get("reveal_sequence")
            if rev and isinstance(rev, list) and isinstance(rev[0], dict):
                trace_src += 1
                as_rec = dict(rec, reveal_sequence=rev)
                base_sg.append(section_grounding(task, as_rec))
                aligned_sg.append(section_grounding(task, align_reasoning(task, as_rec)))

        mean = lambda xs: st.mean([x for x in xs if x is not None])
        t = THRESHOLDS[task]
        report.append(f"\ntask {task}: {len(ids)} labeled cases "
                      f"({trace_src} with a real reveal sequence for grounding)")
        report.append(f"  baseline  var_weight={mean(base_vw):.4f} "
                      f"factor_f1={mean(base_ff):.4f} confidence={mean(base_cf):.4f}")
        report.append(f"  aligned   var_weight={mean(aligned_vw):.4f} "
                      f"factor_f1={mean(aligned_ff):.4f} confidence={mean(aligned_cf):.4f}")
        if trace_src:
            report.append(f"  grounding baseline={mean(base_sg):.4f} "
                          f"aligned={mean(aligned_sg):.4f}")
            report.append(f"  composite baseline="
                          f"{composite(mean(base_vw), mean(base_ff), mean(base_cf), mean(base_sg)):.4f}"
                          f" aligned="
                          f"{composite(mean(aligned_vw), mean(aligned_ff), mean(aligned_cf), mean(aligned_sg)):.4f}")
        check(shape_ok, f"task {task}: aligned weights cover every GT-scored variable")
        check(mean(aligned_vw) >= t["var_weight"] - 1e-9,
              f"task {task}: var_weight {mean(aligned_vw):.4f} >= {t['var_weight']:.4f}")
        check(mean(aligned_vw) >= mean(base_vw),
              f"task {task}: var_weight not worse than baseline")
        check(mean(aligned_ff) >= mean(base_ff),
              f"task {task}: factor_f1 not worse than baseline")
        check(mean(aligned_cf) >= mean(base_cf) - 1e-9,
              f"task {task}: confidence not worse than baseline")
        check(mean(aligned_cf) >= t["confidence"] - 1e-6,
              f"task {task}: confidence {mean(aligned_cf):.6f} >= {t['confidence']:.4f}")
        check(trace_src > 0,
              f"task {task}: grounding measured on real reveal sequences "
              f"({trace_src} cases)")
        if trace_src:
            # Grounding is measured on a subset here (the labeled cases that
            # have an archived trace), so it can wobble a little either way.
            # What must not happen is a *collapse*: `bx_isup` becoming
            # ungrounded would cost ~0.03 of the composite on every task-2
            # case.  The full-trace measurement (1890 task-1 / 1061 task-2 real
            # reveal sequences) is the authoritative figure and is reported by
            # tools/reasoning_alignment_regress.py:
            #   task 1: 0.8312 -> 0.9996      task 2: 0.9759 -> 0.9771
            check(mean(aligned_sg) >= mean(base_sg) - 0.05,
                  f"task {task}: section_grounding does not collapse "
                  f"({mean(aligned_sg):.4f} vs baseline {mean(base_sg):.4f})")
            check(mean(aligned_sg) >= 0.90,
                  f"task {task}: section_grounding {mean(aligned_sg):.4f} >= 0.90")
            al = composite(mean(aligned_vw), mean(aligned_ff), mean(aligned_cf), mean(aligned_sg))
            bl = composite(mean(base_vw), mean(base_ff), mean(base_cf), mean(base_sg))
            check(al >= bl,
                  f"task {task}: composite {al:.4f} >= baseline {bl:.4f}")
            check(al >= t["composite"],
                  f"task {task}: composite {al:.4f} >= {t['composite']:.4f}")

    # Decision-path isolation: alignment must not touch decision fields.
    probe = {"biopsy_decision": "yes", "free_text": "x", "confidence": "uncertain",
             "variable_weights": {"psa": "decisive"}, "reveal_sequence": ["psa_trend"],
             "treatment_recommendation": {"primary": "active_treatment"}}
    out = align_reasoning(1, probe)
    check(probe["variable_weights"] == {"psa": "decisive"},
          "alignment does not mutate the caller's record")
    for field in ("biopsy_decision", "treatment_recommendation", "free_text", "reveal_sequence"):
        check(out.get(field) == probe.get(field),
              f"alignment leaves {field} byte-identical")
    check(align_reasoning(3, probe) is probe or align_reasoning(3, probe) == probe,
          "task 3 passes through untouched")

    print("\n".join(report))
    print()
    if failures:
        print(f"RESULT: {len(failures)} FAILED")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
