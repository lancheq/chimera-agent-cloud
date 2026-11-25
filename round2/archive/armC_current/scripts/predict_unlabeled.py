#!/usr/bin/env python3
"""对 187 无标注 case 用离线 baseline predictor 出决策（任务#3）。

纯 CPU，可并行于 GPU agent 长跑。读取 scripts/unlabeled_187_pids.json
限定 case 集合，避免误处理 GT case。输出 scripts/predictor_decisions_187.json。
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import predict_case  # noqa: E402  复用推理逻辑（含 joblib + VectorReducer 反序列化）

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")

PID_FILE = ROOT / "scripts" / "unlabeled_187_pids.json"
OUT_FILE = ROOT / "scripts" / "predictor_decisions_187.json"


def main() -> None:
    pids_by_task = json.loads(PID_FILE.read_text())["by_task"]
    all_cases = sum((v for v in pids_by_task.values()), [])
    results = {"task1": {}, "task2": {}, "task3": {}}
    ok, fail = 0, []

    for task in (1, 2, 3):
        base = ROOT / "data" / f"task{task}" / "agent_input"
        for cid in pids_by_task.get(f"task{task}", []):
            case_dir = base / cid
            try:
                pred = predict_case.predict_case(case_dir, task)
            except Exception as exc:  # noqa: BLE001
                fail.append((cid, str(exc)))
                continue
            results[f"task{task}"][cid] = pred
            ok += 1

    OUT_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"离线 predictor 决策完成: 成功 {ok}/{len(all_cases)}, 失败 {len(fail)}")
    for cid, err in fail:
        print(f"  FAIL {cid}: {err}")
    print(f"输出 -> {OUT_FILE}")


if __name__ == "__main__":
    main()