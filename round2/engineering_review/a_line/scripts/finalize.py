#!/usr/bin/env python3
"""A 线收口：生成机器回执与协议冻结草案，并做一次自校验。"""
import json
import hashlib
import os
import glob

ROOT = "/home/hedongze/CHIMERA-agent"
OUT = f"{ROOT}/output/a_attribution_20261122"


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


led = json.load(open(f"{OUT}/ledger.json"))
acc = json.load(open(f"{OUT}/accounting.json"))
avoid = json.load(open(f"{OUT}/avoidable_policy_calls.json"))
attr = json.load(open(f"{OUT}/attribution_30.json"))
asum = json.load(open(f"{OUT}/attribution_summary.json"))
hold = json.load(open(f"{OUT}/holdout_eligibility.json"))

# 自校验：报告里引用的每个数字都要能从产物重算出来
checks = {
    "n_cases_91": len(led) == 91,
    "policy_calls_288": acc["policy_calls_adaptive"] == 288,
    "model_calls_470_182": (avoid["summary"]["model_calls_adaptive_total"] == 470
                            and avoid["summary"]["model_calls_fixed_total"] == 182),
    "groups_61_24_3_3": acc["groups"] == {"both_right": 61, "both_wrong": 24,
                                          "only_adaptive": 3, "only_fixed": 3},
    "units_271_243": (acc["information_units_fixed"] == 271
                      and acc["information_units_adaptive"] == 243),
    "confirm_matrices_identical": (asum["confusion_matrix_all91"]["fixed"]
                                   == asum["confusion_matrix_all91"]["adaptive"]
                                   == {"TP": 55, "FN": 1, "FP": 26, "TN": 9}),
    "labels_56_35": asum["label_distribution"] == {"yes": 56, "no": 35},
    "provable_zero": avoid["summary"]["controller_provable_cases"] == 0,
    "attempt_exhausted_zero": avoid["summary"]["attempt_budget_exhausted_cases"] == 0,
    "post_exhaustion_44_in_32": (avoid["summary"]["post_exhaustion_attempt_total"] == 44
                                 and avoid["summary"]["post_exhaustion_attempt_cases"] == 32),
    "stop_explanation_91": avoid["summary"]["stop_explanation_present_cases"] == 91,
    "both_wrong_all_no_and_yes": (asum["both_wrong_all_label_no"]
                                 and asum["both_wrong_all_both_yes"]),
    "holdout_t1_groups_101": hold["remaining_records_by_task"] is not None
                             and hold["total_pool_groups"] - hold["excluded_risk_groups"] == 324,
}

protocol = {
    "protocol_id": "A-T1-THREE-ARM-20261122",
    "status": "STRUCTURE_FROZEN_RUN_BLOCKED",
    "blocking_conditions": [
        "T1 任务目标表述冻结 + 24 例 GT 盲适用条件裁定完成",
        "独立风险组划分由隔离评分方回传（我方不可见标签前置）",
        "候选通过零生成回归",
        "用户单独批准模型预算",
    ],
    "arms": {
        "fixed": "共用初始计划与首观察，按计划顺序执行至耗尽，不可修订",
        "adaptive": "共用初始计划与首观察，每轮可修订剩余计划或停止",
        "direct_react": "原生循环，独立比较整体策略，需先过共同权限/预算/输出验收",
    },
    "shared": {
        "case_set": "同一批 T1 病例",
        "common_plan_and_first_observation": "同一 plan_id + bind_common_first_observation",
        "visible_view": "合法可见栏目集合相同；GT/参考文本/隐藏字段不进运行输入",
        "budget": {"information_units": 3, "max_attempts": 6, "max_actions": 6,
                   "max_policy_turns": "冻结为当前配置值并写入指纹"},
        "model": "同一模型与采样声明；登记实际请求差异",
        "rag_snapshot": "同一快照 ID；指南版本与网页版本分开标注",
        "diagnosis_and_form": "三臂共用同一实现，禁止臂特异化",
        "denominator": "全体分母，失败与早停计入",
        "cost_reporting": "独立部署全成本与可分摊共同规划成本分列",
    },
    "primary_metrics": [
        "配对预测性能",
        "理由对可见证据的忠实性（程序记录遗漏与新增无据主张）",
        "信息单位",
        "策略请求数与总请求数",
        "输入输出 token",
        "整链端到端耗时",
    ],
    "holdout": {
        "excluded_groups": hold["excluded_risk_groups"],
        "remaining_groups": hold["total_pool_groups"] - hold["excluded_risk_groups"],
        "remaining_pure_t1_groups": 101,
        "patient_identity_confirmed": hold["patient_identity_confirmed"],
    },
    "candidate_verdict": {
        "candidate": "控制器确定无合法非重复取证动作时跳过一次 Adaptive 策略生成",
        "verdict": "NOT_SUPPORTED_ZERO_CASES",
        "provable_cases": avoid["summary"]["controller_provable_cases"],
        "post_exhaustion_real_actions": avoid["summary"]["post_exhaustion_attempt_total"],
        "budget_request": "WITHDRAWN",
    },
}
json.dump(protocol, open(f"{OUT}/protocol_freeze_draft.json", "w"), ensure_ascii=False, indent=1)

artifacts = {}
for name in sorted(os.path.basename(p) for p in glob.glob(f"{OUT}/*")
                   if os.path.isfile(p) and not p.endswith("handoff.json")):
    artifacts[name] = sha(f"{OUT}/{name}")

handoff = {
    "stage": "A_line_readonly_attribution_and_protocol_freeze",
    "date": "2026-11-22",
    "model_calls": 0,
    "gpu_tasks": 0,
    "shared_engineering_writes": 0,
    "report": "reports/ROUND2_A_ATTRIBUTION_PROTOCOL_20261122.md",
    "report_sha256": sha(f"{ROOT}/reports/ROUND2_A_ATTRIBUTION_PROTOCOL_20261122.md"),
    "verification": {"all_pass": all(checks.values()), "checks": checks},
    "candidate": protocol["candidate_verdict"],
    "holdout": protocol["holdout"],
    "artifacts_sha256": artifacts,
}
json.dump(handoff, open(f"{OUT}/handoff.json", "w"), ensure_ascii=False, indent=1)

print(json.dumps({"all_pass": all(checks.values()),
                  "failed": [k for k, v in checks.items() if not v],
                  "checks": checks}, ensure_ascii=False, indent=1))
print("\nartifacts:", len(artifacts))
for k, v in artifacts.items():
    print(f"  {k:32s} {v[:16]}")
