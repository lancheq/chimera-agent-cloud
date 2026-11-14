#!/usr/bin/env python3
"""Inventory received inputs and conflict candidates, without opening any GT.

Uses the historical heuristic detector only as a candidate screener. Different
dates, ambiguous units and biopsy mentions can be false positives. Never repairs
records or declares which value is true. Source files are checked for changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from audit_cohorts import audit
from conflict_audit import audit_case, EHR


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if args.output_dir.exists():
        parser.error("Use a new output directory to preserve previous audits")
    sources = json.loads(args.sources.read_text())["sources"]
    inventory = audit(sources, args.sources.resolve().parent)
    rows, counts, snapshots = [], {}, {}
    for case in inventory["cases"]:
        directory = Path(case["directory"])
        # Explicit whitelist: decision/reasoning files are never opened.
        prompt = next((directory / p for p in ("prompt.json", "structured-prompt.json")
                       if (directory / p).is_file()), None)
        clinical = directory / EHR
        if not prompt or not clinical.is_file() or case["task"] != 1:
            continue
        for p in (prompt, clinical):
            snapshots[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
        card = json.loads(prompt.read_text())
        ehr = json.loads(clinical.read_text())
        findings = audit_case(card, ehr)
        counter = counts.setdefault(case["source"], Counter())
        counter["cases_scanned"] += 1
        counter["cases_flagged"] += bool(findings)
        counter["findings"] += len(findings)
        counter.update(f["type"] for f in findings)
        for finding in findings:
            rows.append({"source": case["source"], "case_id": case["case_id"],
                         "status": "candidate_requires_time_unit_source_review",
                         **finding})
    unchanged = all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h
                    for p, h in snapshots.items())
    args.output_dir.mkdir(parents=True)
    (args.output_dir / "inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2)+"\n")
    (args.output_dir / "conflict_candidates.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False)+"\n" for row in rows))
    summary = {"gt_files_opened": 0, "input_values_changed": 0,
               "t1_input_files_hash_checked": len(snapshots), "t1_inputs_unchanged": unchanged,
               "inventory": inventory["summary"], "conflict_screen": counts,
               "limits": ["Heuristic candidates, not adjudicated contradictions.",
                          "Historical detector ignores most date/unit equivalence checks.",
                          "No automatic split; no ID-only patient identity assertion.",
                          "Phase 2 missing prompt fields change detector sensitivity.",
                          "Do not compare release flag rates as data quality gains."]}
    (args.output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n")
    (args.output_dir / "input_hashes.json").write_text(json.dumps(snapshots, indent=2)+"\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if unchanged else 1


if __name__ == "__main__":
    raise SystemExit(main())
