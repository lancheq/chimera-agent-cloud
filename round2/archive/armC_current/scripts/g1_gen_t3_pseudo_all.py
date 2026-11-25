#!/usr/bin/env python3
"""G1: Generate T3 (biochemical recurrence) pseudo-labels for 187 unlabeled
cases across task1, task2, task3 using the GLM-5.2 API.

For every case that lacks a task-specific decision file (and is not an
official/mock case), we ask GLM-5.2 to predict biochemical recurrence
(event + months_to_recurrence) and merge the result into the task3
section of data/pseudo_labels.json without overwriting task1/task2.

Usage
-----
  # Full run (187 cases)
  python scripts/g1_gen_t3_pseudo_all.py

  # Smoke test (5 cases only)
  python scripts/g1_gen_t3_pseudo_all.py --smoke

  # Resume from checkpoint
  python scripts/g1_gen_t3_pseudo_all.py --resume
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("LLMAPI_BASE_URL", "https://llmapi.blsc.cn/v1")
MODEL = os.environ.get("GLM_MODEL", "GLM-5.2")

DECISION_FILE = {
    1: "prostate-biopsy-decision.json",
    2: "prostate-treatment-decision.json",
    3: "prostate-time-to-recurrence-or-last-follow-up.json",
}

CLINICAL_FILE = {
    1: "prostate-biopsy-decision-clinical-data.json",
    2: "prostate-treatment-decision-clinical-data.json",
    3: "prostate-time-to-recurrence-or-last-follow-up-clinical-data.json",
}

_EXCLUDE_PREFIX = ("BX_", "T2_", "T3-mock")
_EXCLUDE_SUBSTR = "-mock-"

OUT_FILE = DATA / "pseudo_labels.json"
CHECKPOINT_FILE = DATA / "g1_t3_checkpoint.json"
LOCK_FILE = ROOT / ".task_locks" / "G1.done"

MAX_WORKERS = 6
CHECKPOINT_INTERVAL = 10
SMOKE_N = 5

# ---------------------------------------------------------------------------
# Schema & prompts
# ---------------------------------------------------------------------------

SCHEMA_HINT = (
    '{\n'
    '  "event": 0 | 1,             // 1 = biochemical recurrence predicted, 0 = censored / no recurrence\n'
    '  "months_to_recurrence": number,  // > 0; months to recurrence (event=1) or last follow-up (event=0); range 1-120\n'
    '  "free_text": "string (>= 40 chars): the evidence and factors driving the prognosis"\n'
    '}'
)

SYSTEM_PROMPT = (
    "You are a prostate cancer prognosis agent following EAU 2026 Guidelines. "
    "Based on the provided clinical case data, predict whether the patient will "
    "experience biochemical recurrence (BCR) after treatment, and if so, the time "
    "in months to recurrence. Output ONLY valid JSON (no markdown fences, no "
    "explanation outside JSON) matching this schema:\n\n"
    "{schema}\n\n"
    "Rules:\n"
    "- event: 1 if biochemical recurrence is predicted, 0 if censored/no recurrence.\n"
    "- months_to_recurrence: a number > 0. For event=1, the predicted months to "
    "recurrence (range 1-120). For event=0, the months of recurrence-free follow-up "
    "(range 1-120).\n"
    "- free_text: concise clinical reasoning (>= 40 characters) citing the key "
    "prognostic factors (PSA, ISUP grade, stage, margins, seminal vesicle invasion, "
    "lymph node status, etc.) per EAU 2026 risk stratification.\n"
    "- Base predictions on EAU 2026 biochemical recurrence risk factors and "
    "post-treatment prognosis guidelines."
)

# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------


def _load_dotenv() -> None:
    """Load environment variables from .env (development convenience)."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


# ---------------------------------------------------------------------------
# OpenAI client
# ---------------------------------------------------------------------------

_client = None


def get_client():
    global _client
    if _client is not None:
        return _client
    from openai import OpenAI

    api_key = os.environ.get("LLMAPI_KEY")
    if not api_key:
        sys.exit("ERROR: LLMAPI_KEY not set. Export it or add to .env")
    _client = OpenAI(api_key=api_key, base_url=BASE_URL, max_retries=3, timeout=120)
    return _client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _num(v, default=None):
    try:
        f = float(v)
        return f if f == f else default  # NaN check
    except (TypeError, ValueError):
        return default


def _trunc(s, n):
    if s is None:
        return None
    s = str(s)
    return s[:n] + ("..." if len(s) > n else "")


# ---------------------------------------------------------------------------
# JSON extraction with salvage
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict | None:
    """Extract a JSON object from GLM response (salvage)."""
    if not text:
        return None
    text = text.strip()
    # 1. Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 2. Remove markdown fences
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 3. Extract first {...} block
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Summary building
# ---------------------------------------------------------------------------


def build_summary(case_dir: Path, task: int) -> dict:
    """Read structured-prompt.json and build a concise clinical summary."""
    sp = json.loads((case_dir / "structured-prompt.json").read_text())

    summary = {
        "case_id": sp.get("case_id") or case_dir.name,
        "psa": sp.get("psa"),
        "age": sp.get("age"),
        "pirads": sp.get("pirads"),
        "psad": sp.get("psad"),
        "vol": sp.get("vol"),
        "dre": sp.get("dre"),
        "bx": sp.get("bx"),
        "cspca": sp.get("cspca"),
        "bx_isup": sp.get("bx_isup"),
        "bx_gl_prim": sp.get("bx_gl_prim"),
        "bx_gl_sec": sp.get("bx_gl_sec"),
        "ct": sp.get("ct"),
        "active_treatment_flag": sp.get("active_treatment_flag"),
        "notes": _trunc(sp.get("notes"), 800),
        "medhx": sp.get("medhx"),
    }

    # Try to read clinical data for pathology/surgical/radiology reports
    clin_file = case_dir / CLINICAL_FILE[task]
    if not clin_file.exists():
        for t in (1, 2, 3):
            f = case_dir / CLINICAL_FILE[t]
            if f.exists():
                clin_file = f
                break

    if clin_file.exists():
        cd = json.loads(clin_file.read_text())
        summary["pathology_report"] = _trunc(cd.get("pathology_report"), 1500)
        summary["surgical_pathology_report"] = _trunc(
            cd.get("surgical_pathology_report"), 1500
        )
        summary["radiology_report"] = _trunc(cd.get("radiology_report"), 1500)

    return summary


# ---------------------------------------------------------------------------
# GLM API call
# ---------------------------------------------------------------------------


def gen_one(case_dir: Path, task: int) -> tuple[dict | None, str]:
    """Call GLM to produce a T3 prediction for one case.

    Returns (parsed_json, status) where status is
    "ok" | "salvage_failed" | "api_error: ..." | "summary_error: ..."
    """
    client = get_client()
    try:
        summary = build_summary(case_dir, task)
    except Exception as e:
        return None, f"summary_error: {e}"

    sys_msg = SYSTEM_PROMPT.format(schema=SCHEMA_HINT)

    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": sys_msg},
                    {
                        "role": "user",
                        "content": json.dumps(summary, ensure_ascii=False),
                    },
                ],
                temperature=0.3,
                max_tokens=8192,
            )
            content = resp.choices[0].message.content
            parsed = _extract_json(content or "")
            if parsed is not None:
                return parsed, "ok"
            if attempt == 0:
                time.sleep(1)
                continue
            return None, "salvage_failed"
        except Exception as e:
            if attempt == 0:
                time.sleep(2)
                continue
            return None, f"api_error: {e}"
    return None, "salvage_failed"


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def validate_schema(obj: dict) -> tuple[bool, str]:
    """Validate GLM output against T3 schema. Returns (valid, reason)."""
    if not isinstance(obj, dict):
        return False, "not_a_dict"

    ev = obj.get("event")
    if isinstance(ev, str) and ev.strip() in ("0", "1"):
        ev = int(ev.strip())
    if ev not in (0, 1):
        return False, f"bad_event:{obj.get('event')}"

    mtr = _num(obj.get("months_to_recurrence"))
    if mtr is None or mtr < 1 or mtr > 120:
        return False, f"bad_months_to_recurrence:{obj.get('months_to_recurrence')}"

    ft = obj.get("free_text")
    if not (isinstance(ft, str) and len(ft) >= 40):
        return False, "free_text_too_short"

    return True, "ok"


# ---------------------------------------------------------------------------
# Entry conversion
# ---------------------------------------------------------------------------


def to_pseudo_entry(case_id: str, obj: dict) -> dict:
    """Map GLM judgment to pseudo_labels.json task3 entry."""
    ev = obj.get("event")
    if isinstance(ev, str) and ev.strip() in ("0", "1"):
        ev = int(ev.strip())
    mtr = _num(obj.get("months_to_recurrence"))
    if mtr is None:
        mtr = 0.0
    return {
        "case_id": case_id,
        "decision": {
            "event": int(ev),
            "months_to_recurrence": float(mtr),
        },
        "free_text": obj.get("free_text"),
    }


# ---------------------------------------------------------------------------
# Case discovery
# ---------------------------------------------------------------------------


def _excluded(name: str) -> bool:
    return name.startswith(_EXCLUDE_PREFIX) or _EXCLUDE_SUBSTR in name


def find_unlabeled_cases() -> list[tuple[int, Path]]:
    """Find all unlabeled cases across task1, task2, task3.

    A case is unlabeled if it has no decision file for its task and is not
    excluded by the BX_/T2_/T3-mock prefix or -mock- substring rules.
    Returns a list of (task, case_dir) tuples, deduplicated by case_id.
    """
    results = []
    seen = set()
    for task in (1, 2, 3):
        task_dir = DATA / f"task{task}" / "agent_input"
        if not task_dir.exists():
            continue
        for c in sorted(task_dir.iterdir()):
            if not c.is_dir():
                continue
            if (c / DECISION_FILE[task]).exists():
                continue
            if _excluded(c.name):
                continue
            cid = c.name
            if cid in seen:
                continue
            seen.add(cid)
            results.append((task, c))
    return results


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------


def load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        try:
            return json.loads(CHECKPOINT_FILE.read_text())
        except Exception:
            pass
    return {"entries": []}


def save_checkpoint(ckpt: dict) -> None:
    CHECKPOINT_FILE.write_text(
        json.dumps(ckpt, indent=2, ensure_ascii=False) + "\n"
    )


# ---------------------------------------------------------------------------
# Merge into pseudo_labels.json
# ---------------------------------------------------------------------------


def merge_into_pseudo_labels(new_entries: list[dict]) -> int:
    """Merge new entries into task3 section of pseudo_labels.json.

    Preserves existing task1/task2 sections. Updates existing task3
    entries by case_id, appends new ones. Returns task3 total count.
    """
    if OUT_FILE.exists():
        data = json.loads(OUT_FILE.read_text())
    else:
        data = {"task1": [], "task2": [], "task3": []}

    if "task3" not in data or not isinstance(data["task3"], list):
        data["task3"] = []

    existing_ids = {e["case_id"] for e in data["task3"]}
    for entry in new_entries:
        if entry["case_id"] in existing_ids:
            for i, e in enumerate(data["task3"]):
                if e["case_id"] == entry["case_id"]:
                    data["task3"][i] = entry
                    break
        else:
            data["task3"].append(entry)
            existing_ids.add(entry["case_id"])

    OUT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return len(data["task3"])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    _load_dotenv()
    ap = argparse.ArgumentParser(
        description="G1: T3 pseudo-label generation via GLM-5.2"
    )
    ap.add_argument(
        "--smoke", action="store_true", help=f"Process only {SMOKE_N} cases"
    )
    ap.add_argument(
        "--resume", action="store_true", help="Resume from checkpoint"
    )
    args = ap.parse_args()

    all_cases = find_unlabeled_cases()
    print(f"G1 T3 pseudo-label generation | model={MODEL} base_url={BASE_URL}")
    print(f"Found {len(all_cases)} unlabeled cases across task1, task2, task3")

    # Load checkpoint
    ckpt = load_checkpoint() if args.resume else {"entries": []}
    resume_ids = (
        {e["case_id"] for e in ckpt["entries"]} if args.resume else set()
    )

    # Filter to cases not yet in checkpoint
    to_process = [(t, d) for t, d in all_cases if d.name not in resume_ids]
    if resume_ids:
        print(f"Resume: skipping {len(resume_ids)} cases already in checkpoint")

    if args.smoke:
        to_process = to_process[:SMOKE_N]
        print(f"Smoke mode: processing only {SMOKE_N} cases")

    print(f"Processing {len(to_process)} cases with max_workers={MAX_WORKERS}")
    print(f"{'=' * 60}")

    stats = {"ok": 0, "fail": 0, "schema_fail": 0}
    new_entries: list[dict] = []
    processed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_case = {}
        for task, case_dir in to_process:
            future = executor.submit(gen_one, case_dir, task)
            future_to_case[future] = (case_dir.name, task, case_dir)

        for future in as_completed(future_to_case):
            cid, task, case_dir = future_to_case[future]
            processed += 1

            try:
                obj, status = future.result()
            except Exception as e:
                obj, status = None, f"api_error: {e}"

            if status != "ok":
                stats["fail"] += 1
                print(
                    f"  [{processed}/{len(to_process)}] {cid}: FAIL ({status})"
                )
                continue

            valid, reason = validate_schema(obj)
            if not valid:
                stats["schema_fail"] += 1
                print(
                    f"  [{processed}/{len(to_process)}] {cid}: "
                    f"SCHEMA_FAIL ({reason})"
                )
                continue

            entry = to_pseudo_entry(cid, obj)
            new_entries.append(entry)
            ckpt["entries"].append(entry)
            stats["ok"] += 1
            ev = entry["decision"]["event"]
            mtr = entry["decision"]["months_to_recurrence"]
            print(
                f"  [{processed}/{len(to_process)}] {cid}: OK "
                f"(event={ev}, months={mtr})"
            )

            # Checkpoint every CHECKPOINT_INTERVAL processed cases
            if processed % CHECKPOINT_INTERVAL == 0:
                save_checkpoint(ckpt)

    # Final checkpoint save
    save_checkpoint(ckpt)

    # Merge into pseudo_labels.json
    task3_total = merge_into_pseudo_labels(new_entries)

    # Write task lock (not in smoke mode)
    if not args.smoke:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOCK_FILE.write_text(
            f"G1 T3 pseudo-label generation complete\n"
            f"ok={stats['ok']} fail={stats['fail']} "
            f"schema_fail={stats['schema_fail']}\n"
        )
        print(f"\nTask lock written: {LOCK_FILE}")

    # Stats
    total_attempts = stats["ok"] + stats["fail"] + stats["schema_fail"]
    pass_rate = (
        (stats["ok"] / total_attempts * 100) if total_attempts > 0 else 0.0
    )

    print(f"\n{'=' * 60}")
    print("G1 T3 Pseudo-Label Generation — Summary")
    print(f"{'=' * 60}")
    print(f"Total cases processed:    {total_attempts}")
    print(f"OK:                       {stats['ok']}")
    print(f"Fail:                     {stats['fail']}")
    print(f"Schema fail:              {stats['schema_fail']}")
    print(f"Schema validation pass rate: {pass_rate:.1f}%")
    print(f"Task3 total after merge:  {task3_total}")
    print(f"Checkpoint:               {CHECKPOINT_FILE}")
    print(f"Output:                   {OUT_FILE}")


if __name__ == "__main__":
    main()
