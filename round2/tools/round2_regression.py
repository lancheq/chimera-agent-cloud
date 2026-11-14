#!/usr/bin/env python3
"""Replay cached predictions through the current container output boundary.

Calls a pinned, UNMODIFIED upstream evaluator. No scoring formula is copied.
This is a deterministic regression diagnostic with the rationale judge OFF;
it is neither a fresh inference run nor a full platform-score reproduction.
Only pass a predeclared cohort; old dev/test data are legacy_exposed.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_file(path: Path, expected: str) -> None:
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f"Pinned file mismatch: {path}: expected {expected}, got {actual}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pin", type=Path, default=ROOT / "configs/round2_evaluator.json")
    parser.add_argument("--evaluator", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--predictions-root", type=Path, required=True)
    parser.add_argument("--task", type=int, action="append", choices=(1, 2, 3), required=True)
    parser.add_argument("--cohort-role", choices=("legacy_exposed", "selection", "holdout"), required=True)
    parser.add_argument("--output", type=Path, required=True, help="A NEW output directory")
    args = parser.parse_args()
    pin = json.loads(args.pin.read_text())
    verify_file(args.evaluator, pin["evaluate_sha256"])
    verify_file(args.mapping, pin["mapping_sha256"])
    if args.output.exists():
        raise FileExistsError(f"Use a new output directory: {args.output}")
    if not args.predictions_root.is_dir():
        raise FileNotFoundError(args.predictions_root)

    os.environ["USE_RATIONALE_JUDGE"] = "0"
    os.environ["SECTION_MAPPING_FILE"] = str(args.mapping.resolve())
    spec = importlib.util.spec_from_file_location("round2_official_evaluator", args.evaluator)
    evaluator = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = evaluator
    spec.loader.exec_module(evaluator)
    sys.path.insert(0, str(ROOT))
    import inference

    aggregates = {}
    all_rows = []
    diagnostics = {}
    for task in sorted(set(args.task)):
        task_id = f"task{task}"
        gt_dir = args.data_root / task_id
        if (gt_dir / "agent_input").is_dir():
            gt_dir /= "agent_input"
        targets = evaluator.load_ground_truth_records(gt_dir, task_id)
        if not targets:
            raise ValueError(f"No labeled targets: {gt_dir}")
        rows = []
        conversion_errors = []
        for gt in targets:
            cid = gt["case_id"]
            prediction_file = args.predictions_root / task_id / cid / "prediction.json"
            pred = None
            if prediction_file.is_file():
                try:
                    original = json.loads(prediction_file.read_text())
                    decision = inference._decision_value(task, original)
                    reasoning = inference._reasoning_value(task, original)
                    if task == 3:
                        pred = {**decision, "free_text": reasoning}
                    else:
                        pred = dict(reasoning)
                        if task == 1:
                            pred["biopsy_decision"] = decision
                        else:
                            pred["treatment_recommendation"] = {"primary": decision}
                    pred["case_id"] = cid
                    pred["clinical_data"] = gt.get("clinical_data", {})
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    # Broken/missing outputs count as failures; never drop a hard case.
                    conversion_errors.append({"case_id": cid, "error": f"{type(exc).__name__}: {exc}"})
                    pred = None
            row = evaluator.evaluate_case(gt, pred, evaluator.build_tool_metric(), None)
            evaluator.attach_kappa_fields(row, gt, pred)
            rows.append(row)
        aggregates[task_id] = evaluator.compute_aggregate_metrics(rows)
        diagnostics[task_id] = {
            "targets": len(targets), "missing_or_unreadable_predictions": sum(
                r.get("gate") == "missing_candidate" for r in rows
            ),
            "schema_failures": sum(r.get("gate") == "schema_failed" for r in rows),
            "conversion_errors": conversion_errors,
        }
        all_rows.extend(rows)
    report = {
        "schema_version": 1,
        "mode": "cached_predictions_current_boundary_deterministic_diagnostic",
        "rationale_judge_enabled": False,
        "cohort_role": args.cohort_role,
        "data_root": str(args.data_root.resolve()),
        "predictions_root": str(args.predictions_root.resolve()),
        "evaluator_pin": pin,
        "boundary_sha256": {
            name: sha256(ROOT / name) for name in ("inference.py", "reasoning_align.py")
        },
        "aggregates": aggregates, "diagnostics": diagnostics,
        "limitations": [
            "Cached predictions were not regenerated; architecture changes are not evaluated.",
            "Current output-boundary conversion is applied; this may differ from the historical submission.",
            "Judge-off component weights differ from full judge-on platform scoring.",
            "No comparison across release batches or causal attribution is performed.",
            "Cohort role is caller-declared; this tool does not certify data independence.",
        ],
    }
    args.output.mkdir(parents=True)
    evaluator.write_json(report, args.output / "report.json")
    # Use task-specific official writers (T3 has different CSV columns).
    for task_id in aggregates:
        kind = {"task1": "biopsy", "task2": "treatment", "task3": "recurrence"}[task_id]
        evaluator.write_csv([r for r in all_rows if r["task"] == kind], args.output / f"{task_id}.csv")
    print(json.dumps({"aggregates": aggregates, "diagnostics": diagnostics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
