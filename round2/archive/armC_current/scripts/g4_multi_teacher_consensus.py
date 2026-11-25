#!/usr/bin/env python3
"""G4: Multi-teacher consensus pseudo-labeling for the 187 unlabeled cases.

Three teacher models (all on the same blsc.cn platform, same API key) each
generate a label for every unlabeled case. A majority vote (>=2 of 3) across
teachers yields a consensus pseudo-label; cases where all three teachers
disagree are discarded.

Teachers
--------
  - GLM-5.2          (platform id: GLM-5.2)
  - DeepSeek-V3      (platform id: DeepSeek-V3.1  -- the canonical DeepSeek-V3
                      series release on the blsc platform; override with
                      TEACHER_DEEPSEEK_MODEL, e.g. DeepSeek-V3-250324)
  - Qwen3.8-Max      (platform id: Qwen3.8-Max)

Per-task label schemas (output JSON only)
-----------------------------------------
  Task 1: {"biopsy_decision": "yes"|"no", "free_text": ">=40 chars"}
  Task 2: {"treatment_decision": "active_surveillance"|"continued_surveillance"
           |"watchful_waiting"|"active_treatment", "free_text": ">=40 chars"}
  Task 3: {"event": 0|1, "months_to_recurrence": number>0,
           "free_text": ">=40 chars"}

Consensus rule
--------------
  Task 1/2 : majority vote on the decision string (>=2 of 3 agree).
  Task 3   : majority vote on event (0/1); months_to_recurrence is the average
             of months from the agreeing teachers.
  If fewer than 2 teachers agree (all three disagree, or too few valid votes)
  the case is discarded.

Output
------
  data/pseudo_labels_consensus.json   {"task1":[...], "task2":[...], "task3":[...]}
  Each entry: {"case_id", "task", "decision", "free_text", "consensus",
              "votes": {teacher: primary | null}}
  data/g4_consensus_checkpoint.json   checkpoint every 10 cases (resumable)
  .task_locks/G4.done                 written on full completion (not --smoke)

Usage
-----
  python scripts/g4_multi_teacher_consensus.py            # full run (187)
  python scripts/g4_multi_teacher_consensus.py --smoke    # 5 cases only
  python scripts/g4_multi_teacher_consensus.py --resume   # continue from ckpt

Compliance: development-time distillation uses third-party API teachers; the
final submission system runs the open-source Qwen3.6-35B-A3B fully offline and
does not depend on any external API. Pseudo-labels enter training only and are
never submitted as evaluation-set predictions.
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

# Per-task ground-truth decision file (its presence == labeled / has GT).
DECISION_FILE = {
    1: "prostate-biopsy-decision.json",
    2: "prostate-treatment-decision.json",
    3: "prostate-time-to-recurrence-or-last-follow-up.json",
}
# Per-task clinical-data file (masked EHR reports).
CLINICAL_FILE = {
    1: "prostate-biopsy-decision-clinical-data.json",
    2: "prostate-treatment-decision-clinical-data.json",
    3: "prostate-time-to-recurrence-or-last-follow-up-clinical-data.json",
}
FEATURES_FILE = "prostate-modality-level-neural-representations.json"

OUT_FILE = DATA / "pseudo_labels_consensus.json"
CHECKPOINT_FILE = DATA / "g4_consensus_checkpoint.json"
LOCK_FILE = ROOT / ".task_locks" / "G4.done"

# Official / mock case exclusion (consistent with the runner skip_gt logic).
_EXCLUDE_PREFIX = ("BX_", "T2_", "T3-mock")
_EXCLUDE_SUBSTR = "-mock-"

# Three teacher models: (display_name, platform model_id).
# GLM-5.2 and Qwen3.8-Max map exactly to platform ids; "DeepSeek-V3" maps to the
# canonical DeepSeek-V3-series release available on the platform (DeepSeek-V3.1).
TEACHERS = [
    ("GLM-5.2", os.environ.get("TEACHER_GLM_MODEL", "GLM-5.2")),
    ("DeepSeek-V3", os.environ.get("TEACHER_DEEPSEEK_MODEL", "DeepSeek-V3.1")),
    ("Qwen3.8-Max", os.environ.get("TEACHER_QWEN_MODEL", "Qwen3.8-Max")),
]
TEACHER_ORDER = [t[0] for t in TEACHERS]

# ---------------------------------------------------------------------------
# Environment / API client
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("LLMAPI_BASE_URL", "https://llmapi.blsc.cn/v1")
_client = None


def _load_dotenv() -> None:
    """Load environment variables from .env at project root (dev convenience)."""
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


def get_client():
    """Lazy shared OpenAI-compatible client (thread-safe for concurrent calls)."""
    global _client
    if _client is not None:
        return _client
    from openai import OpenAI

    api_key = os.environ.get("LLMAPI_KEY")
    if not api_key:
        sys.exit("ERROR: LLMAPI_KEY not set. Export it or add it to .env")
    _client = OpenAI(api_key=api_key, base_url=BASE_URL, max_retries=3, timeout=120)
    return _client


# ---------------------------------------------------------------------------
# Schema + system prompt (output JSON only)
# ---------------------------------------------------------------------------

_ACTIONS = (
    "active_surveillance",
    "continued_surveillance",
    "watchful_waiting",
    "active_treatment",
)

SCHEMA_HINT = {
    1: (
        "{\n"
        '  "biopsy_decision": "yes" | "no",\n'
        '  "free_text": "string (>= 40 chars): the 2-4 key factors that drove the decision"\n'
        "}"
    ),
    2: (
        "{\n"
        '  "treatment_decision": "active_surveillance" | "continued_surveillance" | '
        '"watchful_waiting" | "active_treatment",\n'
        '  "free_text": "string (>= 40 chars): the 2-4 key factors that drove the decision"\n'
        "}"
    ),
    3: (
        "{\n"
        '  "event": 0 | 1,             // 1 = biochemical recurrence predicted, '
        "0 = censored / no recurrence\n"
        '  "months_to_recurrence": number > 0,   // months to recurrence (or last follow-up if event=0)\n'
        '  "free_text": "string (>= 40 chars): the evidence and factors driving the prognosis"\n'
        "}"
    ),
}

SYSTEM_PROMPT = (
    "You are a prostate cancer clinical decision agent following EAU 2026 Guidelines. "
    "Based on the provided clinical case data, produce a structured decision judgment. "
    "Output ONLY valid JSON (no markdown fences, no explanation outside JSON) matching this schema:\n\n"
    "{schema}\n\n"
    "Rules:\n"
    "- free_text: concise clinical reasoning (>= 40 characters).\n"
    "- Base decisions on EAU 2026 risk stratification and management guidelines."
)


# ---------------------------------------------------------------------------
# Prompt assembly
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


def _embedding_shapes(feat: dict) -> dict:
    """Only embed numeric summaries, never raw vectors."""
    shapes = {}
    for origin, vecs in (feat or {}).items():
        if isinstance(vecs, list):
            dims = [len(v) for v in vecs if isinstance(v, list)]
            shapes[origin] = {"n_vectors": len(vecs), "dims": dims[:3]}
        else:
            shapes[origin] = {"n_vectors": 0}
    return shapes


def build_case_prompt(case_dir: Path, task: int) -> dict:
    """Assemble structured-prompt + clinical-data + feature-shape summary."""
    sp = json.loads((case_dir / "structured-prompt.json").read_text())

    summary: dict = {
        "task": task,
        "case_id": sp.get("case_id") or case_dir.name,
        "structured_prompt": sp,
    }

    clin_file = case_dir / CLINICAL_FILE[task]
    if clin_file.exists():
        cd = json.loads(clin_file.read_text())
        summary["clinical_data"] = {
            "radiology_report": _trunc(cd.get("radiology_report"), 1500),
            "pathology_report": _trunc(cd.get("pathology_report"), 1500),
            "surgical_pathology_report": _trunc(cd.get("surgical_pathology_report"), 1500),
            "psa_trend": cd.get("psa_trend"),
            "family_history": cd.get("family_history"),
            "laboratory_results": cd.get("laboratory_results"),
            "previous_notes": _trunc(cd.get("previous_notes"), 1000),
        }

    feat_file = case_dir / FEATURES_FILE
    if feat_file.exists():
        feat = json.loads(feat_file.read_text())
        summary["embedding_shapes"] = _embedding_shapes(feat)

    return summary


# ---------------------------------------------------------------------------
# JSON extraction (salvage)
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict | None:
    """Extract a JSON object from a model response (direct / fence / regex)."""
    if not text:
        return None
    text = text.strip()
    # 1. direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 2. strip markdown fence
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 3. first {...} block
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


# ---------------------------------------------------------------------------
# Single-teacher query
# ---------------------------------------------------------------------------


def query_one_model(model_id: str, case_dir: Path, task: int) -> tuple[dict | None, str]:
    """Call one teacher model for one case. Returns (parsed_json, status).

    status: "ok" | "salvage_failed" | "api_error: ..."
    """
    client = get_client()
    summary = build_case_prompt(case_dir, task)
    schema = SCHEMA_HINT[task]
    sys_msg = SYSTEM_PROMPT.format(schema=schema)

    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=model_id,
                messages=[
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": json.dumps(summary, ensure_ascii=False)},
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
# Per-model output validation + primary extraction
# ---------------------------------------------------------------------------


def _normalize_event(ev):
    """Coerce event to int 0/1 when possible, else return unchanged."""
    if isinstance(ev, str) and ev in ("0", "1"):
        return int(ev)
    return ev


def validate_model_output(task: int, obj: dict) -> tuple[bool, str]:
    """Check a single teacher's JSON against the task schema."""
    if not isinstance(obj, dict):
        return False, "not_dict"
    ft = obj.get("free_text")
    if not (isinstance(ft, str) and len(ft) >= 40):
        return False, "free_text_too_short"

    if task == 1:
        if obj.get("biopsy_decision") not in ("yes", "no"):
            return False, f"bad_biopsy_decision:{obj.get('biopsy_decision')}"
        return True, "ok"

    if task == 2:
        if obj.get("treatment_decision") not in _ACTIONS:
            return False, f"bad_treatment_decision:{obj.get('treatment_decision')}"
        return True, "ok"

    # task 3
    ev = _normalize_event(obj.get("event"))
    if ev not in (0, 1):
        return False, f"bad_event:{obj.get('event')}"
    mtr = _num(obj.get("months_to_recurrence"))
    if mtr is None or mtr <= 0:
        return False, f"bad_months_to_recurrence:{obj.get('months_to_recurrence')}"
    return True, "ok"


def extract_primary(task: int, obj: dict) -> tuple[object, float | None, str | None]:
    """Return (primary_decision, months_to_recurrence, free_text).

    primary_decision: "yes"/"no" (t1) | treatment string (t2) | 0/1 (t3, int).
    """
    if task == 1:
        return obj.get("biopsy_decision"), None, obj.get("free_text")
    if task == 2:
        return obj.get("treatment_decision"), None, obj.get("free_text")
    ev = _normalize_event(obj.get("event"))
    return ev, _num(obj.get("months_to_recurrence")), obj.get("free_text")


# ---------------------------------------------------------------------------
# Consensus assembly
# ---------------------------------------------------------------------------


def assemble_consensus(task: int, case_id: str, per_model: dict) -> tuple[dict | None, str | None]:
    """Build a consensus entry from the three teachers' raw results.

    per_model: {teacher_display_name: (parsed_obj | None, status)}.
    Returns (entry, consensus_str) or (None, None) when discarded.
    """
    votes_out: dict = {}                       # teacher -> primary | None (for output)
    groups: dict = {}                          # primary_value -> [(teacher, obj, ft, mtr)]

    for label in TEACHER_ORDER:
        item = per_model.get(label)
        if item is None:
            votes_out[label] = None
            continue
        obj, status = item
        if status != "ok" or obj is None:
            votes_out[label] = None
            continue
        ok, _ = validate_model_output(task, obj)
        if not ok:
            votes_out[label] = None
            continue
        primary, mtr, ft = extract_primary(task, obj)
        if primary is None:
            votes_out[label] = None
            continue
        # record the vote value for output transparency
        if task in (1, 2):
            votes_out[label] = primary
        else:
            votes_out[label] = {
                "event": int(primary),
                "months_to_recurrence": float(mtr) if mtr is not None else None,
            }
        groups.setdefault(primary, []).append((label, obj, ft, mtr))

    if not groups:
        return None, None

    # majority group by size
    best_primary = max(groups, key=lambda k: len(groups[k]))
    best_group = groups[best_primary]
    n_agree = len(best_group)
    if n_agree < 2:
        return None, None  # all disagree / too few valid -> discard

    consensus_str = f"{n_agree}/3"

    # representative = first teacher (in canonical order) within the majority group
    best_group.sort(key=lambda x: TEACHER_ORDER.index(x[0]))
    rep_ft = best_group[0][2] or ""

    if task in (1, 2):
        decision = best_primary
    else:
        months_vals = [g[3] for g in best_group if g[3] is not None and float(g[3]) > 0]
        avg_mtr = sum(months_vals) / len(months_vals) if months_vals else 0.0
        decision = {"event": int(best_primary), "months_to_recurrence": round(avg_mtr, 4)}

    entry = {
        "case_id": case_id,
        "task": task,
        "decision": decision,
        "free_text": rep_ft,
        "consensus": consensus_str,
        "votes": votes_out,
    }
    return entry, consensus_str


# ---------------------------------------------------------------------------
# Case discovery
# ---------------------------------------------------------------------------


def _excluded(name: str) -> bool:
    return name.startswith(_EXCLUDE_PREFIX) or _EXCLUDE_SUBSTR in name


def find_unlabeled_cases() -> list[tuple[Path, int, str]]:
    """All unlabeled, non-official/mock cases across task1/2/3 agent_input.

    Each case's task is read from its structured-prompt.json "task" field
    (falling back to the directory's task number).
    Returns list of (case_dir, task, case_id).
    """
    cases: list[tuple[Path, int, str]] = []
    for t in (1, 2, 3):
        task_dir = DATA / f"task{t}" / "agent_input"
        if not task_dir.exists():
            continue
        for c in sorted(task_dir.iterdir()):
            if not c.is_dir():
                continue
            if (c / DECISION_FILE[t]).exists():
                continue
            if _excluded(c.name):
                continue
            sp_path = c / "structured-prompt.json"
            if not sp_path.exists():
                continue
            try:
                sp = json.loads(sp_path.read_text())
            except Exception:
                continue
            task = sp.get("task")
            if task not in (1, 2, 3):
                task = t  # fallback to the directory's task
            case_id = sp.get("case_id") or c.name
            cases.append((c, int(task), case_id))
    return cases


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------


def fresh_checkpoint() -> dict:
    return {"task1": [], "task2": [], "task3": []}


def load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        try:
            ck = json.loads(CHECKPOINT_FILE.read_text())
            if isinstance(ck, dict) and all(k in ck for k in ("task1", "task2", "task3")):
                return ck
        except Exception:
            pass
    return fresh_checkpoint()


def save_checkpoint(ckpt: dict) -> None:
    CHECKPOINT_FILE.write_text(json.dumps(ckpt, indent=2, ensure_ascii=False) + "\n")


def checkpoint_case_ids(ckpt: dict) -> set[str]:
    ids: set[str] = set()
    for t in (1, 2, 3):
        for e in ckpt.get(f"task{t}", []):
            if isinstance(e, dict) and e.get("case_id"):
                ids.add(e["case_id"])
    return ids


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="G4: multi-teacher consensus pseudo-labeling")
    ap.add_argument("--smoke", action="store_true", help="process only 5 cases")
    ap.add_argument("--resume", action="store_true", help="resume from checkpoint")
    return ap.parse_args()


def main() -> None:
    _load_dotenv()
    args = _parse_args()
    get_client()  # fail fast if the key is missing

    cases = find_unlabeled_cases()
    ckpt = load_checkpoint() if args.resume else fresh_checkpoint()

    done_ids = checkpoint_case_ids(ckpt) if args.resume else set()
    pending = [(c, t, cid) for (c, t, cid) in cases if cid not in done_ids]
    skipped_resume = len(cases) - len(pending)
    if args.smoke:
        pending = pending[:5]

    teacher_names = ", ".join(f"{dn} (id={mid})" for dn, mid in TEACHERS)
    print("=" * 60)
    print("G4 Multi-Teacher Consensus Pseudo-Labeling")
    print("=" * 60)
    print(f"Teachers: {teacher_names}")
    print(f"Base URL: {BASE_URL}")
    print(f"Total unlabeled cases: {len(cases)}")
    if args.resume:
        print(f"Resuming -- skipped {skipped_resume} already-checkpointed cases")
    if args.smoke:
        print("SMOKE mode -- processing 5 cases only")
    print(f"Pending cases: {len(pending)}")
    print("=" * 60)

    # Seed results from checkpoint so resumed entries are preserved.
    results: dict[int, list] = {1: [], 2: [], 3: []}
    for t in (1, 2, 3):
        results[t] = list(ckpt.get(f"task{t}", []))

    stats = {
        "total": len(pending),
        "consensus": 0,
        "discarded": 0,
        "per_task": {
            1: {"total": 0, "consensus": 0, "discarded": 0},
            2: {"total": 0, "consensus": 0, "discarded": 0},
            3: {"total": 0, "consensus": 0, "discarded": 0},
        },
    }

    assembled = 0

    # One pool of 6 workers; each case submits 3 futures (one per teacher),
    # so up to 6 model queries run concurrently.
    with ThreadPoolExecutor(max_workers=6) as ex:
        future_map: dict = {}
        for idx, (case_dir, task, case_id) in enumerate(pending):
            for label, model_id in TEACHERS:
                fut = ex.submit(query_one_model, model_id, case_dir, task)
                future_map[fut] = (idx, label)

        per_case: dict = {idx: {} for idx in range(len(pending))}
        completed: set[int] = set()

        for fut in as_completed(future_map):
            idx, label = future_map[fut]
            try:
                obj, status = fut.result()
            except Exception as e:
                obj, status = None, f"api_error: {e}"
            per_case[idx][label] = (obj, status)

            if idx in completed or len(per_case[idx]) < 3:
                continue
            completed.add(idx)

            case_dir, task, case_id = pending[idx]
            entry, consensus_str = assemble_consensus(task, case_id, per_case[idx])
            stats["per_task"][task]["total"] += 1

            if entry is not None:
                results[task].append(entry)
                stats["consensus"] += 1
                stats["per_task"][task]["consensus"] += 1
                print(f"  [{assembled + 1}/{len(pending)}] task{task} {case_id}: "
                      f"OK consensus={consensus_str} decision={entry['decision']}")
            else:
                stats["discarded"] += 1
                stats["per_task"][task]["discarded"] += 1
                votes = {k: v for k, v in per_case[idx].items()}
                print(f"  [{assembled + 1}/{len(pending)}] task{task} {case_id}: "
                      f"DISCARDED (no majority)")

            assembled += 1
            if assembled % 10 == 0:
                save_checkpoint(results)

    save_checkpoint(results)

    # Write final output (sort each task by case_id for deterministic ordering).
    out = {
        "task1": sorted(results[1], key=lambda e: e.get("case_id", "")),
        "task2": sorted(results[2], key=lambda e: e.get("case_id", "")),
        "task3": sorted(results[3], key=lambda e: e.get("case_id", "")),
    }
    OUT_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")

    # Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"Total cases: {stats['total']}")
    print(f"Consensus reached: {stats['consensus']}")
    print(f"Discarded (all disagree): {stats['discarded']}")
    rate = (stats["consensus"] / stats["total"] * 100) if stats["total"] else 0.0
    print(f"Consensus rate: {rate:.1f}%")
    print("Per-task breakdown:")
    for t in (1, 2, 3):
        pt = stats["per_task"][t]
        r = (pt["consensus"] / pt["total"] * 100) if pt["total"] else 0.0
        print(f"  Task {t}: total={pt['total']} consensus={pt['consensus']} "
              f"discarded={pt['discarded']} (rate {r:.1f}%)")
    print(f"\nOutput:      {OUT_FILE}")
    print(f"Checkpoint:  {CHECKPOINT_FILE}")

    # Completion lock (full run only, not smoke).
    if not args.smoke:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        lock = {
            "stage": "G4",
            "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "total_cases": stats["total"],
            "consensus": stats["consensus"],
            "discarded": stats["discarded"],
            "teachers": [dn for dn, _ in TEACHERS],
        }
        LOCK_FILE.write_text(json.dumps(lock, indent=2, ensure_ascii=False) + "\n")
        print(f"Lock:        {LOCK_FILE}")


if __name__ == "__main__":
    main()
