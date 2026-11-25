"""Convert the raw Task-3 clinical dump into the task1/task2 input format.

The raw file (``data/Task3_train_<N>cases_clinical_data.json``) is a flat list
of case dicts whose fields mix free text with embedded numbers. This script
splits each case into the canonical per-case layout used by tasks 1 & 2::

    data/task3/agent_input/T3-<nnn>/prompt.json     # age / psa / dre (free-text DRE)
    data/task3/agent_input/T3-<nnn>/clinical.json   # the masked-document fields (tools)

and writes the prediction target (``follow_up_outcome``) to a separate
ground-truth file so it never leaks into the agent input::

    data/task3/ground_truth.json   # [{case_id, months_to_recurrence, event, raw}]

Field mapping
-------------
prompt.json:   case_id (-> "T3-nnn") + task=3, age (parsed int),
               psa (parsed number), dre (<- digital_rectal_examination),
               active_treatment_prior_to_surgery (kept as-is, mostly "unknown")
clinical.json: radiology_report, pathology_report (<- biopsy_pathology),
               surgical_pathology_report (<- surgical_pathology),
               previous_notes (<- previous_note), family_history
ground truth:  follow_up_outcome -> months_to_recurrence + event (1 recurrence,
               0 censored / last follow-up)

Usage::

    python scripts/convert_task3_data.py \
        --input data/Task3_train_75cases_clinical_data.json
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("convert_task3")


def _first_number(text: str) -> float | None:
    """Return the first int/float found in *text*, or ``None``."""
    m = re.search(r"-?\d+(?:\.\d+)?", text or "")
    return float(m.group()) if m else None


def _as_number(text: str) -> float | int | None:
    """First number in *text*, as ``int`` when integer-valued."""
    v = _first_number(text)
    if v is None:
        return None
    return int(v) if v.is_integer() else v


def _build_prompt(case_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    age = _first_number(raw.get("age", ""))
    return {
        "case_id": case_id,
        "task": 3,
        "age": int(age) if age is not None else None,
        "psa": _as_number(raw.get("psa", "")),
        # The free-text physical examination (DRE + derived cT-stage).
        "dre": raw.get("digital_rectal_examination"),
        # Neoadjuvant treatment (mostly "unknown"); kept as-is.
        "active_treatment_prior_to_surgery": raw.get("active_treatment_prior_to_surgery"),
    }


def _build_clinical(case_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    # ``active_treatment_prior_to_surgery`` is intentionally dropped.
    return {
        "case_id": case_id,
        "radiology_report": raw.get("radiology_report"),
        "pathology_report": raw.get("biopsy_pathology"),
        "surgical_pathology_report": raw.get("surgical_pathology"),
        "previous_notes": raw.get("previous_note"),
        "family_history": raw.get("family_history"),
    }


def _parse_outcome(text: str) -> dict[str, Any]:
    """Parse follow_up_outcome into months + event indicator."""
    months = _first_number(text)
    event = 1 if re.search(r"recurrence occurred", text or "", re.IGNORECASE) else 0
    return {"months_to_recurrence": months, "event": event, "raw": text}


def convert(input_path: Path, output_root: Path, ground_truth_path: Path) -> None:
    cases: list[dict[str, Any]] = json.loads(input_path.read_text())
    log.info("Loaded %d raw cases from %s", len(cases), input_path)

    ground_truth: list[dict[str, Any]] = []
    n_events = 0
    for raw in cases:
        case_id = f"T3-{int(raw['case_id']):03d}"
        case_dir = output_root / case_id
        case_dir.mkdir(parents=True, exist_ok=True)

        (case_dir / "prompt.json").write_text(json.dumps(_build_prompt(case_id, raw), indent=2))
        (case_dir / "clinical.json").write_text(json.dumps(_build_clinical(case_id, raw), indent=2))

        outcome = _parse_outcome(raw.get("follow_up_outcome", ""))
        n_events += outcome["event"]
        ground_truth.append({"case_id": case_id, **outcome})

    ground_truth_path.parent.mkdir(parents=True, exist_ok=True)
    ground_truth_path.write_text(json.dumps(ground_truth, indent=2))

    log.info("Wrote %d cases to %s", len(cases), output_root)
    log.info(
        "Ground truth -> %s (%d recurrence events, %d censored)", ground_truth_path, n_events, len(cases) - n_events
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert raw Task-3 data to the task1/2 per-case format.")
    parser.add_argument("--input", type=Path, default=Path("data/Task3_train_75cases_clinical_data.json"))
    parser.add_argument("--output-root", type=Path, default=Path("data/task3/agent_input"))
    parser.add_argument("--ground-truth", type=Path, default=Path("data/task3/ground_truth.json"))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    convert(args.input, args.output_root, args.ground_truth)


if __name__ == "__main__":
    main()
