#!/usr/bin/env python3
"""
分层抽样 30 个 GT case 用于 self_refine 消融实验。

采样策略:
  T1 (12 case): yes/no 均衡 + 灰区(PSA 3-10, PI-RADS 3, PSAD 0.10-0.20) + 已诊断(既往活检阳性)
  T2 (10 case): AS/AT/CS/WW 均衡 + 灰区 + 已诊断
  T3 (8 case):  event=1/event=0 均衡 + 灰区(复发时间 12-36 月)

输出: scripts/ablation_cases.json
"""

import json
import os
import re
import random
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
OUTPUT_PATH = os.path.join(PROJECT_ROOT, "scripts", "ablation_cases.json")

GT_FILES = {
    1: {
        "decision": "prostate-biopsy-decision.json",
        "reasoning": "prostate-biopsy-decision-reasoning.json",
        "clinical_data": "prostate-biopsy-decision-clinical-data.json",
    },
    2: {
        "decision": "prostate-treatment-decision.json",
        "reasoning": "prostate-treatment-decision-reasoning.json",
        "clinical_data": "prostate-treatment-decision-clinical-data.json",
    },
    3: {
        "decision": "prostate-time-to-recurrence-or-last-follow-up.json",
        "reasoning": None,
        "clinical_data": "prostate-time-to-recurrence-or-last-follow-up-clinical-data.json",
    },
}

random.seed(42)


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_psa(clinical_data):
    """Extract latest PSA value from clinical data."""
    # From psa_trend
    psa_trend = clinical_data.get("psa_trend", [])
    if psa_trend:
        return float(psa_trend[-1].get("val", 0))
    # From laboratory_results
    labs = clinical_data.get("laboratory_results", [])
    for lab in reversed(labs):
        if lab.get("name", "").upper() == "PSA":
            val_str = str(lab.get("val", "")).replace("ng/mL", "").strip()
            try:
                return float(val_str)
            except ValueError:
                pass
    return None


def extract_pirads(clinical_data):
    """Extract PI-RADS score from radiology report."""
    report = clinical_data.get("radiology_report", "")
    m = re.search(r"PI-RADS\s*(\d)", report, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def extract_psad(clinical_data):
    """Extract PSA density from radiology report."""
    report = clinical_data.get("radiology_report", "")
    m = re.search(r"PSA density\s*([\d.]+)", report, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).rstrip("."))
        except ValueError:
            pass
    m = re.search(r"PSAD\s*([\d.]+)", report, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).rstrip("."))
        except ValueError:
            pass
    return None


def has_prior_diagnosis(clinical_data):
    """Check if case has prior cancer diagnosis from previous_notes."""
    notes = clinical_data.get("previous_notes") or []
    for note in notes:
        if isinstance(note, dict):
            text = note.get("text", "").lower()
        elif isinstance(note, str):
            text = note.lower()
        else:
            continue
        if any(kw in text for kw in [
            "positive biopsy", "biopsy results", "gleason", "isup",
            "prostate cancer", "csPCa", "clinically significant",
            "prior biopsy", "previous biopsy", "histological finding",
        ]):
            return True
    # Also check if there's a pathology_report or surgical_pathology_report
    if clinical_data.get("pathology_report") or clinical_data.get("surgical_pathology_report"):
        return True
    return False


def categorize_t1(case_id, decision, clinical_data):
    """Categorize T1 case: positive/negative/grey-zone/diagnosed."""
    psa = extract_psa(clinical_data)
    pirads = extract_pirads(clinical_data)
    psad = extract_psad(clinical_data)
    diagnosed = has_prior_diagnosis(clinical_data)

    is_grey_zone = False
    if psa is not None and 3 <= psa <= 10:
        if pirads is not None and pirads == 3:
            is_grey_zone = True
        elif psad is not None and 0.10 <= psad <= 0.20:
            is_grey_zone = True

    if is_grey_zone:
        category = "grey_zone"
    elif decision == "yes":
        category = "positive"
    else:
        category = "negative"

    return {
        "case_id": case_id,
        "task": 1,
        "decision": decision,
        "psa": psa,
        "pirads": pirads,
        "psad": psad,
        "diagnosed": diagnosed,
        "category": category,
    }


def categorize_t2(case_id, decision, clinical_data):
    """Categorize T2 case."""
    psa = extract_psa(clinical_data)
    pirads = extract_pirads(clinical_data)
    psad = extract_psad(clinical_data)
    diagnosed = has_prior_diagnosis(clinical_data)

    is_grey_zone = False
    if psa is not None and 3 <= psa <= 10:
        if pirads is not None and pirads == 3:
            is_grey_zone = True
        elif psad is not None and 0.10 <= psad <= 0.20:
            is_grey_zone = True

    if is_grey_zone:
        category = "grey_zone"
    elif decision in ("active_treatment",):
        category = "positive"
    elif decision in ("watchful_waiting",):
        category = "negative"
    else:
        category = "intermediate"

    return {
        "case_id": case_id,
        "task": 2,
        "decision": decision,
        "psa": psa,
        "pirads": pirads,
        "psad": psad,
        "diagnosed": diagnosed,
        "category": category,
    }


def categorize_t3(case_id, decision, clinical_data):
    """Categorize T3 case."""
    event = decision.get("event", 0)
    months = decision.get("months_to_recurrence", 0)
    diagnosed = has_prior_diagnosis(clinical_data)

    # Grey zone: recurrence between 12-36 months (uncertain prognosis)
    is_grey_zone = event == 1 and 12 <= months <= 36

    if is_grey_zone:
        category = "grey_zone"
    elif event == 1:
        category = "positive"
    else:
        category = "negative"

    return {
        "case_id": case_id,
        "task": 3,
        "decision": decision,
        "event": event,
        "months": months,
        "diagnosed": diagnosed,
        "category": category,
    }


def get_gt_cases(task):
    gt_dir = os.path.join(DATA_DIR, f"task{task}", "agent_input")
    if not os.path.isdir(gt_dir):
        return []
    cases = []
    for case in sorted(os.listdir(gt_dir)):
        dec_file = os.path.join(gt_dir, case, GT_FILES[task]["decision"])
        if os.path.exists(dec_file):
            cases.append(case)
    return cases


def sample_task(cases_info, n_needed, task_num):
    """Stratified sample ensuring category coverage."""
    by_category = defaultdict(list)
    for c in cases_info:
        by_category[c["category"]].append(c)

    # Target distribution per task
    if task_num == 1:
        # 12 cases: ~5 yes, ~4 no, ~2 grey_zone, ~1 diagnosed
        targets = {"positive": 4, "negative": 3, "grey_zone": 2,
                   "positive+diagnosed": 1, "negative+diagnosed": 1, "grey_zone+diagnosed": 1}
    elif task_num == 2:
        # 10 cases: 3 AT, 3 AS, 2 CS, 1 WW, 1 grey_zone
        targets = {"positive": 3, "intermediate": 3, "negative": 1,
                   "grey_zone": 1, "positive+diagnosed": 1, "intermediate+diagnosed": 1}
    else:
        # 8 cases: 3 event=1, 4 event=0, 1 grey_zone
        targets = {"positive": 3, "negative": 3, "grey_zone": 1, "negative+diagnosed": 1}

    sampled = []
    remaining = []

    for cat, target_n in targets.items():
        pool = by_category.get(cat, [])
        random.shuffle(pool)
        take = min(target_n, len(pool))
        sampled.extend(pool[:take])
        remaining.extend(pool[take:])

    # Fill remaining slots
    n_sampled = len(sampled)
    if n_sampled < n_needed:
        random.shuffle(remaining)
        sampled.extend(remaining[:n_needed - n_sampled])
    elif n_sampled > n_needed:
        random.shuffle(sampled)
        sampled = sampled[:n_needed]

    return sampled


def main():
    all_sampled = {}

    for task in [1, 2, 3]:
        gt_cases = get_gt_cases(task)
        cases_info = []

        for case_id in gt_cases:
            gt_dir = os.path.join(DATA_DIR, f"task{task}", "agent_input", case_id)
            decision = load_json(os.path.join(gt_dir, GT_FILES[task]["decision"]))

            cd_path = os.path.join(gt_dir, GT_FILES[task]["clinical_data"])
            clinical_data = load_json(cd_path) if os.path.exists(cd_path) else {}

            if task == 1:
                info = categorize_t1(case_id, decision, clinical_data)
            elif task == 2:
                info = categorize_t2(case_id, decision, clinical_data)
            else:
                info = categorize_t3(case_id, decision, clinical_data)
            cases_info.append(info)

        n_needed = {1: 12, 2: 10, 3: 8}[task]
        sampled = sample_task(cases_info, n_needed, task)
        all_sampled[f"task{task}"] = sampled

        # Print distribution
        print(f"\n=== Task {task}: sampled {len(sampled)}/{n_needed} ===")
        for c in sampled:
            print(f"  {c['case_id']:40s} dec={str(c.get('decision', ''))[:30]:30s} cat={c['category']}")

    # Build pids list for hydra
    all_pids = []
    for task in [1, 2, 3]:
        for c in all_sampled[f"task{task}"]:
            all_pids.append(c["case_id"])

    output = {
        "description": "Stratified 30-case sample for self_refine ablation",
        "total": len(all_pids),
        "pids": all_pids,
        "by_task": {
            f"task{task}": [c["case_id"] for c in all_sampled[f"task{task}"]]
            for task in [1, 2, 3]
        },
        "details": {
            f"task{task}": all_sampled[f"task{task}"]
            for task in [1, 2, 3]
        },
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n[Output] {OUTPUT_PATH}")
    print(f"Total: {len(all_pids)} cases")
    print(f"  T1: {len(all_sampled['task1'])}, T2: {len(all_sampled['task2'])}, T3: {len(all_sampled['task3'])}")


if __name__ == "__main__":
    main()
