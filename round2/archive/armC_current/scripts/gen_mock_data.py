#!/usr/bin/env python3
"""Generate mock agent-input data for CHIMERA-agent local/cloud validation.

Builds data/task<N>/agent_input/<case>/ from the sample files shipped in the
repo (input/ + test/input/interf*), so the pipeline can be validated end-to-end
before the official challenge data arrives.

    python scripts/gen_mock_data.py
"""
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
INPUT_DIR = ROOT / "input"
TEST_INPUT = ROOT / "test" / "input"


def load(p: Path):
    return json.loads(p.read_text())


def write(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False))


def make_case(task: int, case_id: str, prompt: dict, clinical: dict, features: dict):
    case_dir = DATA / f"task{task}" / "agent_input" / case_id
    prompt = dict(prompt)
    prompt["case_id"] = case_id
    prompt["task"] = task
    write(case_dir / "prompt.json", prompt)
    clinical = dict(clinical)
    clinical.setdefault("case_id", case_id)
    write(case_dir / "clinical.json", clinical)
    if features:
        features = dict(features)
        features.setdefault("case_id", case_id)
        write(case_dir / "features.json", features)
    print(f"  created: {case_dir.relative_to(ROOT)}")


def main():
    # --- Task 1: reuse the complete real sample case (input/) ---
    print("[1/3] Task 1 (biopsy decision) ...")
    prompt1 = load(INPUT_DIR / "structured-prompt.json")
    clinical1 = load(INPUT_DIR / "prostate-biopsy-decision-clinical-data.json")
    features1 = load(INPUT_DIR / "prostate-modality-level-neural-representations.json")
    make_case(1, "PT-pseudo_0bd72f429a8b", prompt1, clinical1, features1)

    # --- Task 2: reuse interf1 clinical data + a prompt template ---
    print("[2/3] Task 2 (treatment decision) ...")
    clinical2 = load(TEST_INPUT / "interf1" / "prostate-treatment-decision-clinical-data.json")
    features2 = load(TEST_INPUT / "interf1" / "prostate-modality-level-neural-representations.json")
    prompt2 = {
        "pid": "T2-mock-001",
        "task": 2,
        "psa": 17.4,
        "age": 71,
        "months": 3,
        "pirads": "5",
        "psad": 0.36,
        "psav": 1.0,
        "vol": 48.0,
        "dre": "Nodus",
        "bx": "Positive",
        "cspca": 0.90,
        "ct": "cT2a",
        "bx_isup": 4,
        "bx_gl_prim": 4,
        "bx_gl_sec": 4,
        "bx_gl_tert": None,
        "medhx": "Hypertension",
        "meds": "Amlodipine",
        "notes": "PSA rising rapidly; PI-RADS 5; Gleason 4+4.",
        "note_sections": [{"s": "Chief complaint", "t": "Rising PSA under active surveillance."}],
        "enc_dept": "Urology - outpatient",
        "enc_date": "Feb 2025",
        "enc_ref": "GP",
        "enc_type": "Re-evaluation",
    }
    make_case(2, "T2-mock-001", prompt2, clinical2, features2)

    # --- Task 3: reuse interf2 clinical data + a minimal prompt ---
    print("[3/3] Task 3 (time to recurrence) ...")
    clinical3 = load(
        TEST_INPUT / "interf2" / "prostate-time-to-recurrence-or-last-follow-up-clinical-data.json"
    )
    features3 = load(TEST_INPUT / "interf2" / "prostate-modality-level-neural-representations.json")
    prompt3 = {
        "pid": "T3-mock-001",
        "task": 3,
        "age": 64,
        "psa": 6.2,
        "dre": "Normal",
        "active_treatment_prior_to_surgery": "radical_prostatectomy",
    }
    make_case(3, "T3-mock-001", prompt3, clinical3, features3)

    print("\nDone. Mock data tree:")
    for p in sorted(DATA.rglob("*")):
        if p.is_file():
            print(f"  {p.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
