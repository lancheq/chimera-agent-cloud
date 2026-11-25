#!/usr/bin/env python3
"""N1-P3 · 官方 evaluate.py 的 ground_truth 准备脚本（零推理）。

把 data/task<N>/agent_input/<case>/ 的 GT（decision 字符串/dict + reasoning）
映射为官方 evaluate.py 的 ground_truth/task<N>/<case_id>/pathologist_response.json
最小记录格式（evaluate.py normalize_task_records 能识别的结构）。

task1: {"biopsy_decision": <str>, "confidence", "variable_weights", "reveal_sequence"}
task2: {"treatment_recommendation": {"primary": <str>}, ...}
task3: {"event": int, "months_to_recurrence": float}（裸对象）

输出到 <out_dir>/task<N>/<case_id>/pathologist_response.json，
并复制官方 section_variable_mapping.json。

用法:
  python scripts/prepare_official_gt.py \
      --out test/official_gt_20260831 --tasks 1 2 3
"""
import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OFFICIAL_EVAL = (
    ROOT / "data_backup" / "upload_evaluation_official" / "CHIMERA-agent" / "evaluation"
)

GT_FILES = {
    1: {
        "decision": "prostate-biopsy-decision.json",
        "reasoning": "prostate-biopsy-decision-reasoning.json",
    },
    2: {
        "decision": "prostate-treatment-decision.json",
        "reasoning": "prostate-treatment-decision-reasoning.json",
    },
    3: {
        "decision": "prostate-time-to-recurrence-or-last-follow-up.json",
        "reasoning": None,
    },
}


def build_record(task: int, case_dir: Path) -> dict | None:
    files = GT_FILES[task]
    dec_path = case_dir / files["decision"]
    if not dec_path.exists():
        return None
    dec = json.load(open(dec_path))

    if task == 1:
        rec = {"biopsy_decision": dec}
    elif task == 2:
        rec = {"treatment_recommendation": {"primary": dec}}
    else:  # task3
        # 官方 T3 GT 为裸 {event, months_to_recurrence}
        if isinstance(dec, dict) and "event" in dec:
            return {
                "event": int(dec["event"]),
                "months_to_recurrence": float(dec.get("months_to_recurrence") or 0.0),
            }
        return None

    # 从 reasoning 补充 confidence / variable_weights / reveal_sequence
    rea_path = case_dir / files["reasoning"]
    if rea_path.exists():
        try:
            reasoning = json.load(open(rea_path))
        except json.JSONDecodeError:
            reasoning = {}
        if isinstance(reasoning, dict):
            for k in ("confidence", "variable_weights", "reveal_sequence", "free_text"):
                if k in reasoning:
                    rec[k] = reasoning[k]
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "test" / "official_gt_20260831"))
    ap.add_argument("--tasks", type=int, nargs="+", default=[1, 2, 3])
    args = ap.parse_args()

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    mapping_src = OFFICIAL_EVAL / "ground_truth" / "section_variable_mapping.json"
    if mapping_src.exists():
        shutil.copy(mapping_src, out_root / "section_variable_mapping.json")
        print(f"copied section_variable_mapping.json -> {out_root}")

    total = 0
    for task in args.tasks:
        src = DATA / f"task{task}" / "agent_input"
        if not src.is_dir():
            print(f"[skip] no {src}")
            continue
        n = 0
        for case_dir in sorted(p for p in src.iterdir() if p.is_dir()):
            rec = build_record(task, case_dir)
            if rec is None:
                continue
            out_case = out_root / f"task{task}" / case_dir.name
            out_case.mkdir(parents=True, exist_ok=True)
            # 官方 evaluate.py 按 task 读取不同 GT 文件名：
            #   task1/task2 → pathologist_response.json
            #   task3        → prostate-time-to-recurrence-or-last-follow-up.json
            gt_name = (
                "prostate-time-to-recurrence-or-last-follow-up.json"
                if task == 3
                else "pathologist_response.json"
            )
            (out_case / gt_name).write_text(
                json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            n += 1
        print(f"task{task}: {n} GT records -> {out_root}/task{task}")
        total += n
    print(f"total: {total} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
