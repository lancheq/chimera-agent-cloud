#!/usr/bin/env python3
"""推理侧：用训练好的确定性 predictor 对 case 预测决策（毫秒级）。

预测字段（gate 关键决策）：
  Task1 -> biopsy_decision (yes/no)
  Task2 -> primary (AS/CS/WW/AT)
  Task3 -> event + months_to_recurrence

free_text / variable_weights / confidence 由 LLM 或模板另行生成。

用法:
  python scripts/predict_case.py --task 1 --case data/task1/agent_input/PT-xxx
  python scripts/predict_case.py --task 3 --dir data/task3/agent_input   # 批量
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))  # 允许 import train_predictor

import train_predictor  # noqa: E402  顶层 import 使 VectorReducer 等类可被 joblib 反序列化
from train_predictor import build_case_features  # noqa: E402  复用特征构建（训练/推理一致）

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "model" / "predictor"


def _add_task3_derived(row: dict) -> None:
    """给 task3 补派生特征（与 train_predictor.run_task3 一致）。"""
    psa = row.get("psa", 0) or 0
    age = row.get("age", 1) or 1
    try:
        psa = float(psa)
    except (TypeError, ValueError):
        psa = 0.0
    try:
        age = float(age)
    except (TypeError, ValueError):
        age = np.nan
    row["log_psa"] = np.log1p(psa)
    row["psa_age_ratio"] = psa / max(age, 1) if np.isfinite(age) else np.nan


def _feat_matrix(row: dict, vec, m) -> np.ndarray:
    """按保存的模型重建特征矩阵（顺序与训练一致）。"""
    # task3 模型含派生特征（log_psa / psa_age_ratio），特征管线含 VarianceThreshold(vt)
    is_survival = m.get("kind") == "survival"
    if is_survival:
        _add_task3_derived(row)
        num_names = m.get("all_num_names") or m["num_names"]
    else:
        num_names = m["num_names"]
    num_mat = m["imp"].transform(np.array([[row.get(n, np.nan) for n in num_names]], dtype=float))
    if is_survival and m.get("vt") is not None:
        num_mat = m["vt"].transform(num_mat)
    num_mat = m["scaler"].transform(num_mat)
    parts = [num_mat]
    cat_names = m.get("cat_names") or []
    if cat_names:
        cat_mat = m["cat_encoder"].transform([[row.get(c) for c in cat_names]])
        parts.append(cat_mat.toarray() if hasattr(cat_mat, "toarray") else np.asarray(cat_mat))
    reducer = m.get("reducer")
    if reducer is not None:
        parts.append(reducer.transform([vec]))
    return np.hstack(parts)


def predict_case(case_dir: Path, task: int) -> dict:
    import joblib

    model_file = MODEL_DIR / f"predictor_task{task}.joblib"
    if not model_file.exists():
        raise FileNotFoundError(f"模型不存在: {model_file}（先跑 python scripts/train_predictor.py --save）")
    m = joblib.load(model_file)

    row, vec = build_case_features(case_dir, task)
    X = _feat_matrix(row, vec, m)

    if m["kind"] == "classifier":
        label = m["label_names"][int(m["clf"].predict(X)[0])]
        proba = m["clf"].predict_proba(X)[0]
        proba_dict = dict(zip(m["label_names"], proba.tolist()))
        return {"case_id": case_dir.name, "task": task, "decision": label, "probabilities": proba_dict}
    else:  # survival (task3)
        p_event = float(m["clf_event"].predict_proba(X)[0][1])
        event = 1 if p_event >= m["event_rate"] else 0  # 以训练集事件率为阈值
        months = None
        if event == 1 and m["reg_months"] is not None:
            months = float(np.clip(m["reg_months"].predict(X)[0], 0, None))
        return {
            "case_id": case_dir.name,
            "task": 3,
            "event": event,
            "p_event": p_event,
            "months_to_recurrence": months,
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", type=int, choices=[1, 2, 3], required=True)
    ap.add_argument("--case", type=str, help="单个 case 目录")
    ap.add_argument("--dir", type=str, help="批量：case 父目录（agent_input）")
    ap.add_argument("--out", type=str, help="批量输出目录（写 decision.json）")
    args = ap.parse_args()

    if args.case:
        print(json.dumps(predict_case(Path(args.case), args.task), ensure_ascii=False, indent=2))
        return

    if args.dir:
        case_dir = Path(args.dir)
        out = Path(args.out) if args.out else None
        ok = fail = 0
        for c in sorted(case_dir.iterdir()):
            if not c.is_dir():
                continue
            try:
                pred = predict_case(c, args.task)
            except Exception as e:
                print(f"{c.name}: FAIL {e}")
                fail += 1
                continue
            ok += 1
            if out:
                od = out / c.name
                od.mkdir(parents=True, exist_ok=True)
                (od / "decision.json").write_text(json.dumps(pred, indent=2, ensure_ascii=False))
        print(f"批量完成: 成功 {ok}, 失败 {fail}")
        return

    ap.error("需要 --case 或 --dir")


if __name__ == "__main__":
    main()
