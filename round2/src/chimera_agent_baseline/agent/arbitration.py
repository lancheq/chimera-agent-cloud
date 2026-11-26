"""Calibration 与 Reflection 的仲裁冻结（G0-B3）与覆盖分类。

本轮策略：**现有 Calibration 规则优先，不强制 Reflection 优先。**
理由：F4 不必然是缺陷。若原 Calibration 正确阻止了一次错误修改，覆盖反而有益。
同时修改 Reflection 与 Calibration 两个变量会让后续实验无法归因。

本模块只做机械分类（保留 / 授权覆盖 / 未解释覆盖 / 丢失），
不判断覆盖的医学对错——那需要 GT 与医学评价，属 G2 之后。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

POLICY_NAME = "reflection_arbitration_v1.json"


def default_policy_path() -> str:
    """按包位置向上找 configs/，避免依赖当前工作目录（worktree 与 round2 布局不同）。"""
    for base in Path(__file__).resolve().parents:
        cand = base / "configs" / POLICY_NAME
        if cand.exists():
            return str(cand)
    return os.path.join("configs", POLICY_NAME)


POLICY_PATH = default_policy_path()

FORBIDDEN = "reflection_priority_forbidden"


def load_policy(path: str | None = None) -> dict:
    path = path or default_policy_path()
    with open(path, encoding="utf-8") as f:
        policy = json.load(f)
    if policy.get("reflection_priority") is not False:
        raise RuntimeError(f"{FORBIDDEN}: 本轮冻结要求 reflection_priority=false")
    if policy.get("override_recording") != "required":
        raise RuntimeError("本轮冻结要求 override_recording=required")
    return policy


def classify_receipt(receipt) -> dict:
    """把 FinalReceipt 的字段历史映射成四类机械结论。

    返回 {counts, fields}；fields 保留逐字段明细，供 G2 归因。
    注意：overridden_unexplained 才是需要调查的项；overridden_authorized
    在 policy 里已显式登记规则，不算异常。
    """
    counts = {"retained": 0, "overridden_authorized": 0,
              "overridden_unexplained": 0, "dropped": 0, "internal_only": 0}
    detail = []
    for f in getattr(receipt, "fields", ()) or ():
        status = f.get("status")
        if status == "internal_only":
            counts["internal_only"] += 1
        elif status in ("retained", "finalized_retained"):
            counts["retained"] += 1
        elif status == "authorized_overwrite":
            counts["overridden_authorized"] += 1
        elif status in ("unauthorized_overwrite", "overridden"):
            counts["overridden_unexplained"] += 1
        elif status == "dropped":
            counts["dropped"] += 1
        detail.append({
            "field": f.get("field"),
            "status": status,
            "selected": f.get("selected"),
            "final": f.get("final"),
            "history": [h.get("stage") for h in (f.get("history") or ())],
            "rule_ids": [h.get("rule_id") for h in (f.get("history") or ()) if h.get("rule_id")],
        })
    return {"terminal": getattr(receipt, "terminal", None),
            "counts": counts, "fields": detail}


def acceptance_summary(receipt) -> dict:
    """G2 汇报口径：终态 + 四类计数 + 是否存在未解释覆盖。"""
    r = classify_receipt(receipt)
    r["needs_investigation"] = r["counts"]["overridden_unexplained"] > 0
    return r
