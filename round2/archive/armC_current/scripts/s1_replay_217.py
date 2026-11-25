#!/usr/bin/env python3
"""S1c · 217 case 离线重放（零推理）——S1 修复后的 critique 解析重放。

遍历 output/trace/*/critique_iter1.json（消融 B 的 217 个），用修复后的
_parse_critique_json 重放每个 critique_raw_full：
  - refine 将触发的 case 数（issues 非空）
  - issues 类型分布（10 类计数）
  - severity 分布
逐 case 明细落盘 scripts/s1_replay_results.json；
汇总报告 scripts/s1_replay_report.md。

运行: .venv/bin/python scripts/s1_replay_217.py
"""
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from chimera_agent_baseline.agent.self_refine import _parse_critique_json  # noqa: E402

TRACE_DIR = PROJECT_ROOT / "output" / "trace"
RESULTS_PATH = PROJECT_ROOT / "scripts" / "s1_replay_results.json"
REPORT_PATH = PROJECT_ROOT / "scripts" / "s1_replay_report.md"

ISSUE_TYPES = [
    "fabricated_facts",
    "unsupported_weights",
    "over_treatment",
    "under_treatment",
    "confidence_miscalibration",
    "internal_inconsistency",
    "missing_guideline",
    "imaging_pathology_mismatch",
    "psa_volume_inconsistency",
    "treatment_biopsy_confusion",
]

SEVERITIES = ["none", "mild", "moderate", "severe"]


def detect_types(issue: str) -> list[str]:
    """检测一条 issue 文本命中的 issue 类型（类型名前缀 + 散文关键词）。

    实际样本里只有 ~1/10 的 issue 带 "fabricated_facts:" 式前缀，其余为
    散文描述（如 "Fabricated PSA trend values..."），用关键词启发式归类。
    """
    il = issue.lower()
    hits = []
    if "fabricat" in il or "hallucinat" in il:
        hits.append("fabricated_facts")
    if "unsupported" in il or ("weight" in il and ("no support" in il or "without support" in il)):
        hits.append("unsupported_weights")
    if "over-treatment" in il or "over treatment" in il or "overtreat" in il:
        hits.append("over_treatment")
    if "under-treatment" in il or "under treatment" in il or "undertreat" in il:
        hits.append("under_treatment")
    if "confiden" in il and ("calibrat" in il or "inappropriate" in il or "too high" in il or "too low" in il):
        hits.append("confidence_miscalibration")
    if "inconsisten" in il or "contradict" in il:
        hits.append("internal_inconsistency")
    if "guideline" in il:
        hits.append("missing_guideline")
    if "mismatch" in il or ("imaging" in il and "pathology" in il):
        hits.append("imaging_pathology_mismatch")
    if ("psa density" in il or "psad" in il or "volume" in il) and ("inconsisten" in il or "mismatch" in il or "does not match" in il or "0.0" in il):
        hits.append("psa_volume_inconsistency")
    if ("biopsy" in il and "treatment" in il) or "confus" in il:
        hits.append("treatment_biopsy_confusion")
    return hits


def main() -> int:
    files = sorted(TRACE_DIR.glob("*/critique_iter1.json"))
    print(f"critique_iter1.json 共 {len(files)} 个")

    detail = []
    n_trigger = 0
    type_counter = Counter()
    severity_counter = Counter()
    n_no_json = 0
    n_clean_json = 0
    n_thinking_recovered = 0

    for f in files:
        case_id = f.parent.name
        data = json.load(open(f))
        raw = data.get("critique_raw_full", "")
        orig_parsed = data.get("parsed_issues", [])

        clean, issues = _parse_critique_json(raw)
        severity = "none"
        if clean and clean != raw:
            try:
                obj = json.loads(clean)
                severity = obj.get("severity", "none")
            except json.JSONDecodeError:
                pass

        types = []
        for issue in issues:
            for t in detect_types(issue):
                type_counter[t] += 1
                if t not in types:
                    types.append(t)

        triggered = bool(issues)
        if triggered:
            n_trigger += 1
            severity_counter[severity] += 1
        if not issues:
            severity_counter["none"] += 1

        # 统计 raw 形态
        if not any(m in raw.lower()[:200] for m in
                   ("here's a thinking process", "here is a thinking process", "analyze user input",
                    "let's analyze the provided", "the user wants me to audit", "i need to check for:",
                    "i'll analyze", "let me review")):
            n_clean_json += 1
        elif clean and clean != raw:
            n_thinking_recovered += 1
        else:
            n_no_json += 1

        detail.append({
            "case_id": case_id,
            "orig_parsed_issues": orig_parsed,
            "replay_issues": issues,
            "severity": severity,
            "types": types,
            "would_trigger_refine": triggered,
        })

    n_total = len(files)
    results = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "total_cases": n_total,
        "refine_trigger_cases": n_trigger,
        "refine_trigger_rate": round(n_trigger / n_total, 4) if n_total else 0.0,
        "issue_type_distribution": dict(type_counter),
        "severity_distribution": {k: severity_counter.get(k, 0) for k in ["none", "mild", "moderate", "severe"]},
        "raw_shape": {
            "clean_json_direct": n_clean_json,
            "thinking_with_tail_json": n_thinking_recovered,
            "thinking_no_json": n_no_json,
        },
        "cases": detail,
    }
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"明细已写入 {RESULTS_PATH}")

    # ── 报告 ──
    lines = [
        "# S1 critique 解析修复 · 217 case 离线重放报告",
        "",
        f"> 生成: {results['generated']}（零推理）",
        f"> 数据源: 0829 消融 B 的 {n_total} 个 output/trace/*/critique_iter1.json",
        "> 解析器: 修复后 `_parse_critique_json`（S1，commit 见 git log）",
        "",
        "## 核心结论",
        "",
        f"**若 S1 修复在 0829 消融 B 生效，refine 触发率会是 {n_trigger}/{n_total}"
        f"（{results['refine_trigger_rate']:.1%}），原触发率 0/217（0.0%）。**",
        "",
        f"- 原解析器把全部 217 个 thinking 前缀 critique 误判为污染 → `parsed_issues` 全为空 → refine 0% 触发。",
        f"- S1 修复后：{n_thinking_recovered} 个 thinking 前缀 case 从尾部恢复了有效 JSON；",
        f"  {n_clean_json} 个本就是干净 JSON 直接开头（原路径已正常）；{n_no_json} 个确无 JSON（保持降级）。",
        "",
        "## 统计",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| refine 将触发 case 数 | **{n_trigger}/{n_total}（{results['refine_trigger_rate']:.1%}）** |",
        "| 原触发率（修复前） | 0/217（0.0%） |",
        f"| 抓到的 issue 总条数 | {sum(len(c['replay_issues']) for c in detail)} |",
        "",
        "### issues 类型分布（10 类，按命中数）",
        "",
        "| 类型 | 命中数 |",
        "|---|---|",
    ]
    for t, c in type_counter.most_common():
        lines.append(f"| {t} | {c} |")
    lines += [
        "",
        "### severity 分布（全部 217 case，非触发 case 归入 none）",
        "",
        "| severity | case 数 |",
        "|---|---|",
    ]
    for s, c in severity_counter.most_common():
        lines.append(f"| {s} | {c} |")
    lines += [
        "",
        "> 注意：模型 severity 未严格按 schema（none/mild/moderate/severe），",
        "> 大量使用 high/medium/low 尺度——格式漂移，refine 消费 severity 时需映射。",
    ]
    lines += [
        "",
        "## 结论补全（消融 B 真实结论）",
        "",
        "0829 消融 B 得出「refine 0% 触发率 → self_refine 无增益」的结论，但根因是",
        "解析器 bug（2A 前置过滤错杀 thinking 前缀后的有效 JSON），而非 refine 本身无效。",
        "S1 修复后重放显示 refine 本可触发 "
        f"{n_trigger}/{n_total}（{results['refine_trigger_rate']:.1%}），"
        f"top issue 类型为「{type_counter.most_common(1)[0][0] if type_counter else 'N/A'}」等。",
        "self_refine 的真实增益需在修复后重跑消融 B 才能定案（下一步，GPU 任务）。",
        "",
        f"> 逐 case 明细见 scripts/s1_replay_results.json",
    ]
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"报告已写入 {REPORT_PATH}")

    # 控制台摘要
    print()
    print(f"refine 触发: {n_trigger}/{n_total} ({results['refine_trigger_rate']:.1%})")
    print(f"issue 类型 top5: {type_counter.most_common(5)}")
    print(f"severity: {dict(severity_counter)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
