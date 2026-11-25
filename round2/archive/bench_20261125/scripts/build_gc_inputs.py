#!/usr/bin/env python3
"""Convert our two arms' outputs and local labels into the official evaluator's format.

The official evaluator (``CHIMERA-agent/evaluation/evaluate.py``) reads:

* ``predictions.json``      -- a Grand Challenge job dump (no case_id in it)
* ``pk_hash_to_case_map.json`` -- pk -> ``taskN/<case_id>``
* ``ground_truth/<taskN>/<case_id>/<file>`` -- pathologist response (T1/T2) or the
  recurrence outcome (T3), plus ``section_variable_mapping.json`` next to it

Nothing is re-run and no label is invented: the ground truth is our existing
expert/pseudo annotation, only reshaped. Two known gaps are reported rather than
papered over -- see the notes printed at the end.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil

DATA = Path("/home/hedongze/CHIMERA-agent/data")
ARMS = {"A": Path("/home/hedongze/CHIMERA-armA/output/bench_A_full"),
        "C": Path("/home/hedongze/CHIMERA-armC/output/bench_work")}
OUT = Path("/home/hedongze/CHIMERA-armC/output/bench/gc")
SCRIPTS = Path("/home/hedongze/CHIMERA-armC/scripts")
EVAL_ROOT = Path("/home/hedongze/CHIMERA-agent/CHIMERA-agent/evaluation")

CASE_FILES = {1: "t1_full_91_gt_pids.json", 2: "t2_full_72_gt_pids.json", 3: "t3_full_73_gt_pids.json"}
DECISION = {1: "prostate-biopsy-decision.json", 2: "prostate-treatment-decision.json",
            3: "prostate-time-to-recurrence-or-last-follow-up.json"}
REASONING = {1: "prostate-biopsy-decision-reasoning.json",
             2: "prostate-treatment-decision-reasoning.json",
             3: "prostate-time-to-recurrence-or-last-follow-up-reasoning.json"}
INPUT_SLUG = {1: "prostate-biopsy-decision-clinical-data",
              2: "prostate-treatment-decision-clinical-data",
              3: "prostate-time-to-recurrence-or-last-follow-up-clin"}
OUT_SLUGS = {1: ("prostate-biopsy-decision", "prostate-biopsy-decision-reasoning"),
             2: ("prostate-treatment-decision", "prostate-treatment-decision-reasoning"),
             3: ("prostate-time-to-recurrence-or-last-follow-up",
                 "prostate-time-to-recurrence-or-last-follow-up-reasoning")}


def load(path: Path):
    return json.loads(path.read_text())


def cases(task: int) -> list[str]:
    return load(SCRIPTS / CASE_FILES[task])


def build_gt() -> dict:
    """Expert label -> official ground-truth layout."""
    stats = {}
    root = OUT / "gt"
    if root.exists():
        shutil.rmtree(root)
    for task in (1, 2, 3):
        task_dir = root / f"task{task}"
        kept = 0
        for case in cases(task):
            src = DATA / f"task{task}" / "agent_input" / case
            decision = load(src / DECISION[task])
            target = task_dir / case
            target.mkdir(parents=True, exist_ok=True)
            if task == 3:
                (target / DECISION[task]).write_text(json.dumps(decision, indent=2) + "\n")
            else:
                reasoning = load(src / REASONING[task])
                record = dict(case_id=case, confidence=reasoning.get("confidence"),
                              variable_weights=reasoning.get("variable_weights") or {},
                              reveal_sequence=reasoning.get("reveal_sequence") or [],
                              free_text=reasoning.get("free_text"))
                if task == 1:
                    record["biopsy_decision"] = decision
                    payload = {"source": "local expert annotation", "schema_version": "1.4",
                               "biopsy_decision": [record]}
                else:
                    record["treatment_recommendation"] = {"primary": decision}
                    payload = {"source": "local expert annotation", "schema_version": "1.4",
                               "treatment_decision": [record]}
                (target / "pathologist_response.json").write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            kept += 1
        stats[task] = kept
    shutil.copy(EVAL_ROOT / "ground_truth/section_variable_mapping.json",
                root / "section_variable_mapping.json")
    return stats


def build_predictions(arm: str) -> dict:
    """Arm outputs -> GC job dump + pk map, per task."""
    base = ARMS[arm]
    stats = {}
    for task in (1, 2, 3):
        jobs, pk_map = [], {}
        for case in cases(task):
            case_dir = base / f"task{task}" / case
            if task == 3:
                decision_path = case_dir / DECISION[3]
                reasoning_path = case_dir / REASONING[3]
                if arm == "C":
                    decision_path = case_dir / "sockets" / DECISION[3]
                    reasoning_path = case_dir / "sockets" / REASONING[3]
            else:
                if arm == "C":
                    case_dir = case_dir / "sockets"
                decision_path, reasoning_path = case_dir / DECISION[task], case_dir / REASONING[task]
            if not decision_path.is_file():
                continue
            reasoning = load(reasoning_path) if reasoning_path.is_file() else {}
            decision = load(decision_path)
            pk = case
            pk_map[pk] = f"task{task}/{case}"
            jobs.append({
                "pk": pk,
                "inputs": [{"socket": {"slug": INPUT_SLUG[task]}}],
                "outputs": [
                    {"socket": {"slug": OUT_SLUGS[task][0]}, "value": decision},
                    {"socket": {"slug": OUT_SLUGS[task][1]}, "value": reasoning},
                ],
            })
        target = OUT / f"arm{arm}" / f"task{task}"
        target.mkdir(parents=True, exist_ok=True)
        (target / "predictions.json").write_text(json.dumps(jobs, indent=2, ensure_ascii=False) + "\n")
        (target / "pk_hash_to_case_map.json").write_text(
            json.dumps({"pk_to_case": pk_map}, indent=2) + "\n")
        stats[task] = len(jobs)
    return stats


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    gt = build_gt()
    print("GT:", gt)
    for arm in ("A", "C"):
        print(f"arm{arm} jobs:", build_predictions(arm))
    # Known gaps in the local ground truth, reported not hidden.
    gaps = []
    for task, name in ((1, "task1"), (2, "task2")):
        empty = 0
        total = 0
        for case in cases(task):
            total += 1
            rec = load(OUT / "gt" / name / case / "pathologist_response.json")
            key = "biopsy_decision" if task == 1 else "treatment_decision"
            if not (rec[key][0].get("reveal_sequence")):
                empty += 1
        gaps.append(f"{name}: {empty}/{total} 个 GT 的 reveal_sequence 为空")
    print("GAPS:", " | ".join(gaps))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
