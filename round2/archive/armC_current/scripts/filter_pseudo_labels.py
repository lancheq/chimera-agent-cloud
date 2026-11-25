#!/usr/bin/env python3
"""批次4：从 187 无标注 case 筛选高置信伪标签。

筛选标准（同时满足）：
  1. LLM confidence == "clear"
  2. LLM 决策 == 离线 predictor 决策
     T1: biopsy_decision；T2: treatment_recommendation.primary；T3: event
  3. form_fill_warnings 不含 "fallback" / "override" / "parse failed"

输出 data/pseudo_labels.json（格式同 GT decision 文件）：
  {"task1": [{"case_id","decision"}], "task2": [...], "task3": [...]}
  其中 T1/T2 的 decision 为裸字符串（yes/no / primary），
  T3 的 decision 为 {"event", "months_to_recurrence"}。

同时打印淘汰原因分布（用作结构化回传）。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "test" / "output.unlabeled_20260826"
PRED_FILE = ROOT / "scripts" / "predictor_decisions_187.json"
OUT_FILE = ROOT / "data" / "pseudo_labels.json"

# task -> (LLM 决策字段读取路径, predictor 决策字段, GT decision 标签类型)
LLM_DEC = {1: ("biopsy_decision", None), 2: ("treatment_recommendation", "primary"), 3: ("event", None)}

BAD_WORDS = ("fallback", "override", "parse failed")


def llm_decision(trace: dict, task: int) -> object:
    """从 trace.json 的 structured_response 提取 LLM 决策。"""
    sr = trace.get("structured_response") or {}
    if task == 1:
        return sr.get("biopsy_decision")
    if task == 2:
        return (sr.get("treatment_recommendation") or {}).get("primary")
    # task3
    return sr.get("event")


def predictor_decision(pred: dict, task: int) -> object:
    if task == 3:
        return pred.get("event")
    return pred.get("decision")


def main() -> None:
    preds = json.loads(PRED_FILE.read_text())
    stats = {"task1": {}, "task2": {}, "task3": {}}
    out = {"task1": [], "task2": [], "task3": []}

    for task in (1, 2, 3):
        tdir = OUT_DIR / f"task{task}"
        pkey = preds[f"task{task}"]
        skey, okey = f"task{task}", f"task{task}"
        for case_dir in sorted(tdir.iterdir()):
            if not case_dir.is_dir():
                continue
            cid = case_dir.name
            trace_file = case_dir / "trace.json"
            if not trace_file.exists():
                stats[skey][cid] = "no_trace"
                continue
            trace = json.loads(trace_file.read_text())
            pred = pkey.get(cid)
            if pred is None:
                stats[skey][cid] = "no_predictor_decision"
                continue

            reason = None

            # 1. confidence == clear
            sr = trace.get("structured_response") or {}
            conf = sr.get("confidence")
            if conf != "clear":
                reason = "confidence_not_clear"

            # 3. 无 warning
            if reason is None:
                fw = trace.get("form_fill_warnings") or []
                joined = " ".join(str(w) for w in fw).lower()
                if any(b in joined for b in BAD_WORDS):
                    reason = "has_warning"

            # 2. LLM == predictor
            if reason is None:
                d_llm = llm_decision(trace, task)
                d_pred = predictor_decision(pred, task)
                if d_llm != d_pred:
                    reason = "llm_ne_predictor"

            if reason is not None:
                stats[skey][cid] = reason
                continue

            # 通过 → 构造伪标签（格式同 GT decision 文件）
            if task == 3:
                sr = trace.get("structured_response") or {}
                decision = {
                    "event": int(sr.get("event")),
                    "months_to_recurrence": float(sr.get("months_to_recurrence")) if sr.get("months_to_recurrence") is not None else None,
                }
            else:
                decision = llm_decision(trace, task)
            out[okey].append({"case_id": cid, "decision": decision})

    # 淘汰原因分布汇总
    for task in (1, 2, 3):
        skey, okey = f"task{task}", f"task{task}"
        reasons = stats[skey]
        dist = {}
        for r in reasons.values():
            dist[r] = dist.get(r, 0) + 1
        n = len(reasons)
        print(f"task{task}: 通过伪标签={len(out[okey])} 淘汰={n}")
        for r, c in sorted(dist.items(), key=lambda x: -x[1]):
            print(f"    {r}: {c}")

    # 写 T3 额外信息：event 分布（供偏置监控）
    for task in (1, 2, 3):
        okey = f"task{task}"
        if out[okey]:
            decs = [i["decision"] for i in out[okey]]
            print(f"task{task} 伪标签决策分布:", decs if task == 3 else {d: decs.count(d) for d in set(decs)})

    OUT_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    total = sum(len(v) for v in out.values())
    print(f"\n写入 {OUT_FILE}，伪标签总数 = {total}")


if __name__ == "__main__":
    main()