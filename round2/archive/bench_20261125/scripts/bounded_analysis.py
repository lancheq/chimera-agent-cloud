#!/usr/bin/env python3
"""Three bounded analyses on the frozen 472 trajectories. No model calls.

A. T1 confidence chain -- did the prose hedge while the form said "clear"?
B. T2 confidence -- class distribution, confusion, over-treatment, the 14 borderlines.
C. Grounding 0.430 -> 0.765 -- coverage, weight set, or output mechanism?
"""

from __future__ import annotations

import csv
import glob
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

DATA = Path("/home/hedongze/CHIMERA-agent/data")
GC = Path("/home/hedongze/CHIMERA-armC/output/bench/gc_v2")
CWORK = Path("/home/hedongze/CHIMERA-armC/output/bench_work")
AOUT = Path("/home/hedongze/CHIMERA-armA/output/bench_A_full")
SCRIPTS = Path("/home/hedongze/CHIMERA-armC/scripts")
MAP = Path("/home/hedongze/CHIMERA-agent/CHIMERA-agent/evaluation/ground_truth/section_variable_mapping.json")

T1_CASES = json.loads((SCRIPTS / "t1_full_91_gt_pids.json").read_text())
T2_CASES = json.loads((SCRIPTS / "t2_full_72_gt_pids.json").read_text())
R1 = "prostate-biopsy-decision-reasoning.json"
R2 = "prostate-treatment-decision-reasoning.json"

HEDGE = re.compile(
    r"\b(uncertain|uncertainty|unclear|equivocal|insufficient|inconclusive|cannot|can not|"
    r"cannot be (?:determined|excluded|assessed)|insufficient (?:data|evidence|information)|"
    r"limited (?:data|information)|borderline|ambiguous|conflicting|incomplete|not available|"
    r"missing|no (?:available|documented)|remains unclear|difficult to)\b", re.I)


def load(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def stages(case: str, task: int) -> dict:
    """Per-stage assistant text from the C arm's audit journal."""
    events = sorted(glob.glob(str(CWORK / f"task{task}" / case / "private" / "*" / "events.jsonl")))
    out = defaultdict(list)
    if not events:
        return out
    for line in Path(events[-1]).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("kind") != "model_response":
            continue
        msg = ((row.get("response") or {}).get("choices") or [{}])[0].get("message") or {}
        text = msg.get("content")
        if isinstance(text, str):
            out[row.get("stage")].append(text)
    return out


def arm_a_t1_confidence(case: str):
    return load(AOUT / "task1" / case / R1)


def joint_reveal_and_weights(task: int, case: str, arm: str):
    """Reveal count and the set of variables weighted above not_used."""
    base = (AOUT if arm == "A" else CWORK) / f"task{task}" / case
    if arm == "C":
        base = base / "sockets"
    rec = load(base / (R1 if task == 1 else R2)) or {}
    seq = rec.get("reveal_sequence") or []
    names = []
    for item in seq:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and item.get("key"):
            names.append(str(item["key"]))
    weights = rec.get("variable_weights") or {}
    used = {k for k, v in weights.items() if str(v).lower().strip() not in ("not_used", "none", "")}
    return len(names), sorted(used), str(rec.get("confidence") or "").lower()


def analyse_t1_chain() -> dict:
    gt_clear, gt_unclear = [], []
    hedge_in_unclear, hedge_in_clear = 0, 0
    diag_hedge_then_clear = []
    fill_conf_mismatch = []
    for case in T1_CASES:
        gt = (load(DATA / "task1" / "agent_input" / case / R1) or {}).get("confidence")
        gt = str(gt).lower()
        st = stages(case, 1)
        diagnosis = "\n".join(st.get("diagnosis") or [])
        fill = "\n".join(st.get("form_fill") or [])
        final_conf = None
        m = re.search(r'"confidence"\s*:\s*"([a-z]+)"', fill)
        if m:
            final_conf = m.group(1)
        hedged_diag = bool(HEDGE.search(diagnosis))
        if gt == "clear":
            gt_clear.append(hedged_diag)
        else:
            gt_unclear.append(hedged_diag)
            if hedged_diag:
                hedge_in_unclear += 1
            if hedged_diag and final_conf == "clear":
                diag_hedge_then_clear.append(case)
        if gt != "clear" and final_conf == "clear":
            fill_conf_mismatch.append(dict(case=case, gt=gt, diag_hedged=hedged_diag))
    return dict(
        gt_clear_n=len(gt_clear), gt_unclear_n=len(gt_unclear),
        hedge_rate_gt_clear=round(sum(gt_clear) / len(gt_clear), 3) if gt_clear else None,
        hedge_rate_gt_unclear=round(sum(gt_unclear) / len(gt_unclear), 3) if gt_unclear else None,
        gt_unclear_but_output_clear=len(fill_conf_mismatch),
        of_which_diagnosis_hedged=len(diag_hedge_then_clear),
        examples=diag_hedge_then_clear[:5],
    )


def analyse_t2() -> dict:
    gt_conf = Counter()
    cell = Counter()          # (gt_class, gt_conf) -> n
    pred_at_by_conf = Counter()
    acc_by_conf = defaultdict(lambda: [0, 0])
    border = []
    pred_conf_hist = Counter()
    for case in T2_CASES:
        gt = load(DATA / "task2" / "agent_input" / case / R2) or {}
        gt_decision = load(DATA / "task2" / "agent_input" / case / "prostate-treatment-decision.json")
        pred_rec = load(CWORK / "task2" / case / "sockets" / R2) or {}
        pred_decision = load(CWORK / "task2" / case / "sockets" / "prostate-treatment-decision.json")
        conf = str(gt.get("confidence") or "?").lower()
        gt_conf[conf] += 1
        pred_conf_hist[str(pred_rec.get("confidence") or "?").lower()] += 1
        cell[(str(gt_decision), conf)] += 1
        ok = str(pred_decision) == str(gt_decision)
        acc_by_conf[conf][0] += int(ok)
        acc_by_conf[conf][1] += 1
        if str(pred_decision) == "active_treatment":
            pred_at_by_conf[conf] += 1
        if conf == "borderline":
            border.append(dict(case=case, gt_class=str(gt_decision), pred_class=str(pred_decision),
                               correct=ok, free_text=(gt.get("free_text") or "")[:90]))
    table = {f"{cls} / {conf}": n for (cls, conf), n in sorted(cell.items())}
    return dict(
        gt_confidence=dict(gt_conf), pred_confidence=dict(pred_conf_hist),
        class_by_confidence=table,
        accuracy_by_confidence={k: dict(n=v[1], acc=round(v[0] / v[1], 3)) for k, v in acc_by_conf.items()},
        pred_active_treatment_share_by_confidence={k: dict(n=gt_conf[k], pred_at=pred_at_by_conf[k],
                                                          share=round(pred_at_by_conf[k] / gt_conf[k], 3))
                                                   for k in gt_conf},
        borderline_cases=border,
    )


def analyse_grounding() -> dict:
    mapping = load(MAP) or {}
    always = set((mapping.get("always_available_variables") or {}).get("variables", []))
    var2sec = mapping.get("variable_to_sections") or {}
    out = {}
    for task, cases, reasoning in ((1, T1_CASES, R1), (2, T2_CASES, R2)):
        out[task] = {}
        for arm in ("A", "C"):
            revealed, used, ungrounded, zero = [], [], [], 0
            sec_dep_used = 0
            for case in cases:
                n_rev, used_vars, _ = joint_reveal_and_weights(task, case, arm)
                revealed.append(n_rev)
                used.append(len(used_vars))
                if n_rev == 0:
                    zero += 1
                for var in used_vars:
                    secs = var2sec.get(var) or []
                    if var in always or not secs:
                        continue
                    sec_dep_used += 1
                    if not set(secs) & set(_reveal_names(task, case, arm)):
                        ungrounded.append(var)
            n = len(cases)
            out[task][arm] = dict(
                cases=n,
                mean_revealed=round(sum(revealed) / n, 2),
                pct_zero_reveal=round(100 * zero / n, 1),
                mean_weighted_vars=round(sum(used) / n, 2),
                section_dependent_weighted=sec_dep_used,
                ungrounded=len(ungrounded),
                ungrounded_share_of_section_dep=round(ungrounded.__len__() / sec_dep_used, 3) if sec_dep_used else None,
            )
    return out


def _reveal_names(task: int, case: str, arm: str) -> set[str]:
    base = (AOUT if arm == "A" else CWORK) / f"task{task}" / case
    if arm == "C":
        base = base / "sockets"
    rec = load(base / (R1 if task == 1 else R2)) or {}
    seq = rec.get("reveal_sequence") or []
    names = set()
    for item in seq:
        if isinstance(item, str):
            names.add(item)
        elif isinstance(item, dict) and item.get("key"):
            key = str(item["key"])
            names.add(key)
            names.add(key.replace("section_s3-", "section_"))
    translate = {"section_s3-prev": "previous_notes", "section_s3-labs": "laboratory_results",
                 "section_s3-psa": "psa_trend", "section_s3-mri": "radiology_report",
                 "section_s3-path": "pathology_report", "section_s3-fh": "family_history"}
    return {translate.get(n, n) for n in names}


def main() -> int:
    result = dict(t1_chain=analyse_t1_chain(), t2=analyse_t2(), grounding=analyse_grounding())
    (GC / "BOUNDED_ANALYSIS.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result, indent=2, ensure_ascii=False)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
