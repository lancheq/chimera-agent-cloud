#!/usr/bin/env python3
"""Build inputs for the CURRENT official evaluator (main branch, 2055 lines).

The evaluator we used earlier was a stale vendored copy: it accepted only dict
reveal entries and gave 0.5 partial credit on task 2. Official main identifies an
interface by the *set of input socket slugs*, reads outputs from files under
``INPUT_DIRECTORY/<job_pk>/``, and accepts a flat list of section-name strings in
``reveal_sequence``. This script produces exactly that shape.

Nothing is re-generated: the agent outputs are the frozen benchmark artifacts and
the ground truth is the existing local annotation, only re-laid-out.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil

DATA = Path("/home/hedongze/CHIMERA-agent/data")
ARMS = {"A": Path("/home/hedongze/CHIMERA-armA/output/bench_A_full"),
        "C": Path("/home/hedongze/CHIMERA-armC/output/bench_work")}
OUT = Path("/home/hedongze/CHIMERA-armC/output/bench/gc_v2")
SCRIPTS = Path("/home/hedongze/CHIMERA-armC/scripts")
EVAL_ROOT = Path("/home/hedongze/CHIMERA-agent/CHIMERA-agent/evaluation")

CASE_FILES = {1: "t1_full_91_gt_pids.json", 2: "t2_full_72_gt_pids.json", 3: "t3_full_73_gt_pids.json"}
DECISION = {1: "prostate-biopsy-decision.json", 2: "prostate-treatment-decision.json",
            3: "prostate-time-to-recurrence-or-last-follow-up.json"}
REASONING = {1: "prostate-biopsy-decision-reasoning.json",
             2: "prostate-treatment-decision-reasoning.json",
             3: "prostate-time-to-recurrence-or-last-follow-up-reasoning.json"}
CLINICAL = {1: "prostate-biopsy-decision-clinical-data.json",
            2: "prostate-treatment-decision-clinical-data.json",
            3: "prostate-time-to-recurrence-or-last-follow-up-clinical-data.json"}
CLIN_SLUG = {1: "prostate-biopsy-decision-clinical-data",
             2: "prostate-treatment-decision-clinical-data",
             3: "prostate-time-to-recurrence-or-last-follow-up-clin"}
NEURAL_SLUG = "prostate-modality-level-neural-representations"
OUT_SLUGS = {1: ("prostate-biopsy-decision", "prostate-biopsy-decision-reasoning"),
             2: ("prostate-treatment-decision", "prostate-treatment-decision-reasoning"),
             3: ("prostate-time-to-recurrence-or-last-follow-up",
                 "prostate-time-to-recurrence-or-last-follow-up-reasoning")}


def load(path: Path):
    return json.loads(path.read_text())


def cases(task: int) -> list[str]:
    return load(SCRIPTS / CASE_FILES[task])


def build_gt() -> dict:
    root = OUT / "gt"
    if root.exists():
        shutil.rmtree(root)
    counts = {}
    for task in (1, 2, 3):
        kept = 0
        for case in cases(task):
            src = DATA / f"task{task}" / "agent_input" / case
            dst = root / f"task{task}" / case
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy(src / DECISION[task], dst / DECISION[task])
            if task == 3:
                pass  # task 3 ground truth is the outcome file only
            else:
                shutil.copy(src / REASONING[task], dst / REASONING[task])
            kept += 1
        counts[task] = kept
    shutil.copy(EVAL_ROOT / "ground_truth/section_variable_mapping.json",
                root / "section_variable_mapping.json")
    # The evaluator requires the archive case map to exist even when every job
    # carries an inline structured-prompt case id (which ours do).
    (root / "debug_archive_pks.csv").write_text("case_id,structured-prompt_pk\n")
    return counts


def build_arm(arm: str) -> dict:
    inputs_root = OUT / f"arm{arm}" / "inputs"
    if inputs_root.exists():
        shutil.rmtree(inputs_root)
    cases_root = inputs_root
    jobs, counts = [], {}
    for task in (1, 2, 3):
        kept = 0
        for case in cases(task):
            case_dir = ARMS[arm] / f"task{task}" / case
            if arm == "C":
                case_dir = case_dir / "sockets"
            decision = case_dir / DECISION[task]
            reasoning = case_dir / REASONING[task]
            if not decision.is_file() or not reasoning.is_file():
                continue
            pk = case
            target = cases_root / pk
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy(decision, target / "decision.json")
            shutil.copy(reasoning, target / "reasoning.json")
            jobs.append({
                "pk": pk,
                "inputs": [
                    {"socket": {"slug": CLIN_SLUG[task]},
                     "value": load(DATA / f"task{task}" / "agent_input" / case / CLINICAL[task])},
                    {"socket": {"slug": NEURAL_SLUG}, "value": None},
                    {"socket": {"slug": "structured-prompt"}, "value": {"case_id": case}},
                ],
                "outputs": [
                    {"socket": {"slug": OUT_SLUGS[task][0], "relative_path": "decision.json"}},
                    {"socket": {"slug": OUT_SLUGS[task][1], "relative_path": "reasoning.json"}},
                ],
            })
            kept += 1
        counts[task] = kept
    (inputs_root / "predictions.json").write_text(json.dumps(jobs, indent=2, ensure_ascii=False) + "\n")
    return counts


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print("GT:", build_gt())
    for arm in ("A", "C"):
        print(f"arm{arm} jobs:", build_arm(arm))
    # Sanity: interface key must equal the three-slug tuple the evaluator expects.
    job = load(OUT / "armA" / "inputs" / "predictions.json")[0]
    print("interface key:", tuple(sorted(sv["socket"]["slug"] for sv in job["inputs"])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
