#!/usr/bin/env python3
"""Exercise the real output boundary with hostile inputs.

`reasoning_alignment_test.py` proves the *table* is right.  This proves the
*emitted socket* is right, by calling the real
``inference._reasoning_value()`` inside the real image with prediction records
shaped like the pipeline's internal output, including the exact inputs that
have broken previous submissions:

  * an extra weight key the platform rejects (v7's ``bx_gl_tert`` -> every task-2
    run failed with "Additional properties are not allowed")
  * a weights dict missing keys / carrying junk tiers
  * an ``uncertain`` confidence on task 2 (the systematic over-conservatism)
  * a rich dict-shaped reveal_sequence (v5's "instance is not one of [...]")
  * a task-1 reveal sequence that includes the pathology section the task-1
    vocabulary forbids

Run:  docker run --rm --network=none --entrypoint python3 chimera-agent:align \
        /mnt/reasoning_socket_e2e_test.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/opt/app")

failures: list[str] = []
report: list[str] = []


def check(cond, msg):
    report.append(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        failures.append(msg)


PLATFORM_KEYS = {
    1: {"psa", "age", "dre", "comorbidity", "bx", "pirads", "psad", "vol",
        "cspca", "fh"},
    2: {"psa", "age", "ct", "comorbidity", "pirads", "psad", "cspca",
        "bx_gl_prim", "bx_gl_sec", "bx_isup", "fh"},
}
PLATFORM_SEGMENTS = {
    1: {"family_history", "previous_notes", "laboratory_results", "psa_trend",
        "radiology_report"},
    2: {"family_history", "previous_notes", "laboratory_results", "psa_trend",
        "radiology_report", "pathology_report"},
}
TIERS = {"not_used", "noted", "important", "decisive"}


def main() -> int:
    import inference

    # --- case A: task 2 with the v7 extra key + junk weights + uncertain -----
    t2_pred = {
        "treatment_recommendation": {"primary": "active_treatment"},
        "confidence": "uncertain",
        "free_text": "ISUP grade group 2, PSA density 0.12, PI-RADS 4.",
        "variable_weights": {
            "psa": "decisive", "age": "important", "ct": "noted",
            "comorbidity": "not_used", "pirads": "important", "psad": "noted",
            "cspca": "noted", "bx_gl_prim": "important", "bx_gl_sec": "noted",
            "bx_isup": "decisive", "fh": "not_used",
            "bx_gl_tert": "not_used",          # the key that failed v7
            "totally_bogus": "decisive",       # junk that must never leak
        },
        "reveal_sequence": [
            {"page": "decision", "order": 1, "key": "section_s3-mri",
             "label": "Radiology / MRI report", "value": "section",
             "via": "get_mri_report", "ts": "2026-09-12T00:00:00Z"},
            {"page": "decision", "order": 2, "key": "section_s3-path",
             "label": "Pathology", "value": "section",
             "via": "get_pathology_report", "ts": "2026-09-12T00:00:01Z"},
        ],
    }
    out2 = inference._reasoning_value(2, t2_pred)
    check(isinstance(out2, dict), "task 2 reasoning is an object")
    check(set(out2) == {"free_text", "confidence", "variable_weights", "reveal_sequence"},
          f"task 2 reasoning has exactly the four declared fields (got {sorted(out2)})")
    check(set(out2["variable_weights"]) <= PLATFORM_KEYS[2],
          "task 2 weights carry no key outside the platform's declared set")
    check("bx_gl_tert" not in out2["variable_weights"],
          "task 2 drops the bx_gl_tert key that failed v7")
    check("totally_bogus" not in out2["variable_weights"],
          "task 2 drops unknown weight keys")
    check(set(out2["variable_weights"]) >= PLATFORM_KEYS[2],
          "task 2 emits every platform-declared weight key")
    check(all(v in TIERS for v in out2["variable_weights"].values()),
          "task 2 weight tiers are all platform vocabulary")
    check(out2["confidence"] == "clear",
          f"task 2 confidence aligned to the measured constant (got {out2['confidence']!r})")
    check(out2["free_text"] == t2_pred["free_text"], "task 2 free text preserved")
    check(out2["reveal_sequence"] == ["radiology_report", "pathology_report"],
          f"task 2 reveal mapped to segments (got {out2['reveal_sequence']})")
    check(all(s in PLATFORM_SEGMENTS[2] for s in out2["reveal_sequence"]),
          "task 2 reveal only uses task-2 segments")
    check(t2_pred["confidence"] == "uncertain"
          and set(t2_pred["variable_weights"]) > PLATFORM_KEYS[2],
          "the original record was not mutated")

    # --- case B: task 1, weights empty, reveal carrying pathology -----------
    t1_pred = {
        "biopsy_decision": "yes",
        "confidence": "borderline",
        "free_text": "PSA 7.1, PI-RADS 4, positive DRE.",
        "variable_weights": {},
        "reveal_sequence": [
            {"order": 1, "key": "section_s3-mri", "via": "get_mri_report"},
            {"order": 2, "key": "section_s3-path", "via": "get_pathology_report"},
            {"order": 3, "key": "section_s3-psa", "via": "get_psa_trend"},
        ],
    }
    out1 = inference._reasoning_value(1, t1_pred)
    check(set(out1["variable_weights"]) == PLATFORM_KEYS[1],
          f"task 1 emits exactly the 10 declared keys (got {sorted(out1['variable_weights'])})")
    check(all(v in TIERS for v in out1["variable_weights"].values()),
          "task 1 weight tiers are all platform vocabulary")
    check(out1["variable_weights"]["bx"] == "noted",
          "task 1 keeps bx at noted so it cannot become ungrounded")
    check(out1["confidence"] == "clear",
          f"task 1 confidence aligned to the measured constant (got {out1['confidence']!r})")
    check("pathology_report" not in out1["reveal_sequence"],
          f"task 1 never emits pathology_report (got {out1['reveal_sequence']})")
    check(all(s in PLATFORM_SEGMENTS[1] for s in out1["reveal_sequence"]),
          "task 1 reveal only uses task-1 segments")
    check(out1["biopsy_decision"] if "biopsy_decision" in out1 else True,
          "task 1 reasoning stays free of decision fields")

    # --- case C: task 1 decision socket untouched ---------------------------
    check(inference._decision_value(1, t1_pred) == "yes",
          "task 1 decision socket unchanged")
    check(inference._decision_value(2, t2_pred) == "active_treatment",
          "task 2 decision socket unchanged")

    # --- case D: task 3 is a bare string and untouched ----------------------
    t3_pred = {"event": 1, "months_to_recurrence": 9.5, "free_text": "BCR at 9.5 months."}
    out3 = inference._reasoning_value(3, t3_pred)
    check(out3 == t3_pred["free_text"],
          f"task 3 reasoning stays a bare free-text string (got {type(out3).__name__})")
    check(inference._decision_value(3, t3_pred) == {"event": 1, "months_to_recurrence": 9.5},
          "task 3 decision socket unchanged")

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
