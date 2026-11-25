#!/usr/bin/env python3
"""
CHIMERA-agent 输出格式转换器

将 agent 的 per-case prediction.json 文件转换为官方 evaluate.py 所需的
Grand Challenge predictions.json + pk_hash_to_case_map.json 格式。

输入布局 (agent 输出):
  test/output/task{N}/<case_id>/prediction.json

输出布局 (官方 evaluate.py 输入):
  <out_dir>/predictions.json           # list of jobs: {pk, inputs, outputs}
  <out_dir>/pk_hash_to_case_map.json   # {pk_to_case: {pk: "taskN/<case_id>"}}

用法:
  python scripts/convert_to_gc_format.py                          # 默认输出到 test/input_gc/
  python scripts/convert_to_gc_format.py --out /path/to/out       # 自定义输出目录
  python scripts/convert_to_gc_format.py --tasks 1 2 3            # 只转指定 task
  python scripts/convert_to_gc_format.py --in-root test/output    # 自定义输入根目录
"""
from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# ── 官方 socket slug 映射 (注意官方有 typo/truncation，必须精确匹配) ──────────

TASK_INPUT_SLUGS: dict[int, str] = {
    1: "prostate-biopsy-decision-clinical-data",
    2: "prostate-treatment-decision-clinical-data",
    3: "prostate-time-to-recurrence-or-last-follow-up-clin",  # 官方截断
}

# 决策 output slug (task1 有 typo "biospy")
TASK_DECISION_SLUGS: dict[int, str] = {
    1: "prostate-biospy-decision",  # 官方 typo: biospy 而非 biopsy
    2: "prostate-treatment-decision",
    3: "prostate-time-to-recurrence-or-last-follow-up",  # outcome 对象
}

# reasoning output slug (task3 官方截断为 -reas)
TASK_REASONING_SLUGS: dict[int, str] = {
    1: "prostate-biospy-decision-reasoning",
    2: "prostate-treatment-decision-reasoning",
    3: "prostate-time-to-recurrence-or-last-follow-up-reas",  # 官方截断
}

# 确定性 pk 生成 (uuid5 + 固定 namespace，保证可重现)
PK_NAMESPACE = uuid.UUID("6b8e1c5a-9d3f-4e7b-a0f2-1c5d8e9f0a2b")

# 不进入 reasoning socket 的字段 (这些是程序化字段或决策字段)
_EXCLUDE_FROM_REASONING = {
    "case_id", "patient", "schema_version", "task", "case_type",
    "biopsy_decision", "treatment_recommendation",
    "event", "months_to_recurrence",  # task3 outcome 字段单独走 decision socket
}


def make_pk(task: int, case_id: str) -> str:
    """确定性 UUID5，保证同一 (task, case_id) 总是生成同一 pk。"""
    return str(uuid.uuid5(PK_NAMESPACE, f"task{task}:{case_id}"))


def build_inputs(task: int, pred: dict[str, Any]) -> list[dict]:
    """构造 input sockets。evaluate.py 只用 slug 识别 task，value 可为占位。

    对 task3，clin socket 的 value 在启用 judge 时会作为 clinical_data 传给
    rationale judge；禁用 judge 时无影响。我们把 patient 字段塞进去以备后用。
    """
    patient = pred.get("patient") or {}
    clin_value: dict[str, Any] = {}
    if patient:
        clin_value["patient"] = patient
    return [
        {"socket": {"slug": "structured-prompt"}, "value": {"key": "value", "None": None}},
        {"socket": {"slug": TASK_INPUT_SLUGS[task]}, "value": clin_value},
        {"socket": {"slug": "prostate-modality-level-neural-representations"}, "value": {}},
    ]


def build_outputs(task: int, pred: dict[str, Any]) -> list[dict]:
    """把 agent prediction 拆成决策 socket + reasoning socket。"""
    outputs: list[dict] = []

    if task == 1:
        # 决策: 纯字符串 "yes"/"no"
        outputs.append({
            "socket": {"slug": TASK_DECISION_SLUGS[1]},
            "value": pred.get("biopsy_decision"),
        })
        # reasoning: 剩余字段 (free_text, confidence, variable_weights, reveal_sequence, repeat_test)
        reasoning = {k: v for k, v in pred.items() if k not in _EXCLUDE_FROM_REASONING}
        outputs.append({
            "socket": {"slug": TASK_REASONING_SLUGS[1]},
            "value": reasoning,
        })

    elif task == 2:
        # 决策: treatment_recommendation.primary 纯字符串
        rec = pred.get("treatment_recommendation") or {}
        outputs.append({
            "socket": {"slug": TASK_DECISION_SLUGS[2]},
            "value": rec.get("primary"),
        })
        # reasoning: 剩余字段 (free_text, confidence, variable_weights, reveal_sequence, repeat_test, + treatment_recommendation 完整对象)
        reasoning = {k: v for k, v in pred.items() if k not in _EXCLUDE_FROM_REASONING}
        # 保留 treatment_recommendation 完整对象在 reasoning 里 (evaluate.py 不读，但保留信息)
        if "treatment_recommendation" in pred:
            reasoning["treatment_recommendation"] = pred["treatment_recommendation"]
        outputs.append({
            "socket": {"slug": TASK_REASONING_SLUGS[2]},
            "value": reasoning,
        })

    elif task == 3:
        # outcome: {event, months_to_recurrence}
        outputs.append({
            "socket": {"slug": TASK_DECISION_SLUGS[3]},
            "value": {
                "event": pred.get("event"),
                "months_to_recurrence": pred.get("months_to_recurrence"),
            },
        })
        # reasoning: 纯 free_text 字符串 (官方 task3 reasoning 是 string 不是 dict)
        outputs.append({
            "socket": {"slug": TASK_REASONING_SLUGS[3]},
            "value": pred.get("free_text", ""),
        })

    return outputs


def convert_one(task: int, case_id: str, pred: dict[str, Any]) -> tuple[dict, str]:
    """转一个 case -> (job, pk)。"""
    pk = make_pk(task, case_id)
    job = {
        "pk": pk,
        "inputs": build_inputs(task, pred),
        "outputs": build_outputs(task, pred),
        "status": "Succeeded",
    }
    return job, pk


def convert_all(
    in_root: Path,
    out_dir: Path,
    tasks: list[int],
) -> dict[str, int]:
    """扫描 in_root/task{N}/*/prediction.json，生成 predictions.json + pk_map。"""
    out_dir.mkdir(parents=True, exist_ok=True)

    all_jobs: list[dict] = []
    pk_to_case: dict[str, str] = {}
    counts: dict[str, int] = {}

    for task in tasks:
        task_dir = in_root / f"task{task}"
        if not task_dir.exists():
            print(f"[skip] {task_dir} 不存在")
            counts[f"task{task}"] = 0
            continue

        n_ok = 0
        n_err = 0
        for case_dir in sorted(task_dir.iterdir()):
            if not case_dir.is_dir():
                continue
            pred_file = case_dir / "prediction.json"
            if not pred_file.exists():
                continue
            try:
                pred = json.loads(pred_file.read_text(encoding="utf-8"))
                case_id = case_dir.name
                job, pk = convert_one(task, case_id, pred)
                all_jobs.append(job)
                pk_to_case[pk] = f"test/input/task{task}/{case_id}"
                n_ok += 1
            except Exception as exc:  # noqa: BLE001
                print(f"[error] task{task}/{case_dir.name}: {exc}")
                n_err += 1

        counts[f"task{task}_ok"] = n_ok
        counts[f"task{task}_err"] = n_err
        print(f"task{task}: 转换 {n_ok} 个 case ({n_err} 错误)")

    predictions_path = out_dir / "predictions.json"
    pk_map_path = out_dir / "pk_hash_to_case_map.json"

    with predictions_path.open("w", encoding="utf-8") as f:
        json.dump(all_jobs, f, ensure_ascii=False, indent=2)

    pk_map = {
        "source": f"converted from {in_root}/taskN/*/prediction.json",
        "mapping_rule": f"pk = uuid5(namespace, 'task{{N}}:{{case_id}}'), namespace={PK_NAMESPACE}",
        "pk_to_case": pk_to_case,
    }
    with pk_map_path.open("w", encoding="utf-8") as f:
        json.dump(pk_map, f, ensure_ascii=False, indent=2)

    print(f"\n写入: {predictions_path} ({len(all_jobs)} jobs)")
    print(f"写入: {pk_map_path} ({len(pk_to_case)} mappings)")
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in-root", default=str(ROOT / "test" / "output"),
                    help="agent 输出根目录 (默认 test/output)")
    ap.add_argument("--out", default=str(ROOT / "test" / "input_gc"),
                    help="GC 格式输出目录 (默认 test/input_gc)")
    ap.add_argument("--tasks", type=int, nargs="+", default=[1, 2, 3],
                    help="要转换的 task 列表 (默认 1 2 3)")
    args = ap.parse_args()

    in_root = Path(args.in_root)
    out_dir = Path(args.out)
    convert_all(in_root, out_dir, args.tasks)


if __name__ == "__main__":
    main()
