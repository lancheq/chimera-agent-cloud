#!/usr/bin/env python3
"""
P0-D: GEval 评估（rationale alignment）
使用 Ollama judge 模型评估 prediction free_text 是否覆盖了关键推理点。

输出：
  - scripts/geval_eval_report.md
"""

import json
import os
import sys
import re
import time
import urllib.request
import urllib.error
from datetime import datetime
from collections import Counter

# ── 路径配置 ──
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRED_DIR = os.path.join(PROJECT_ROOT, "test", "output.pro_v1_20260816")
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
REPORT_PATH = os.path.join(PROJECT_ROOT, "scripts", "geval_eval_report.md")
CACHE_PATH = os.path.join(PROJECT_ROOT, "scripts", "geval_eval_cache.json")

OLLAMA_URL = "http://localhost:11434/api/generate"
JUDGE_MODEL = os.environ.get("GEVAL_JUDGE_MODEL", "qwen2.5:7b")

# GT 文件名
GT_FILES = {
    1: {
        "decision": "prostate-biopsy-decision.json",
        "reasoning": "prostate-biopsy-decision-reasoning.json",
        "clinical": "prostate-biopsy-decision-clinical-data.json",
    },
    2: {
        "decision": "prostate-treatment-decision.json",
        "reasoning": "prostate-treatment-decision-reasoning.json",
        "clinical": "prostate-treatment-decision-clinical-data.json",
    },
    3: {
        "decision": "prostate-time-to-recurrence-or-last-follow-up.json",
        "reasoning": None,
        "clinical": "prostate-time-to-recurrence-or-last-follow-up-clinical-data.json",
    },
}

# 每个 task 的关键推理变量
KEY_VARIABLES = {
    1: ["PIRADS", "PSA", "PSAD", "DRE", "cspca", "age", "prostate_volume", "family_history", "previous_biopsy"],
    2: ["PIRADS", "PSA", "PSAD", "Gleason_primary", "ISUP_grade", "clinical_T_stage", "age", "comorbidity", "family_history", "cspca"],
    3: ["PSA", "Gleason_primary", "ISUP_grade", "pathological_stage", "surgical_margins", "seminal_vesicle_invasion", "extraprostatic_extension", "lymph_node"],
}


# ── 工具函数 ──

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_gt_cases(task):
    gt_dir = os.path.join(DATA_DIR, f"task{task}", "agent_input")
    if not os.path.isdir(gt_dir):
        return []
    cases = []
    for case in sorted(os.listdir(gt_dir)):
        dec_file = os.path.join(gt_dir, case, GT_FILES[task]["decision"])
        if os.path.exists(dec_file):
            cases.append(case)
    return cases


def get_prediction(task, case):
    pred_path = os.path.join(PRED_DIR, f"task{task}", case, "prediction.json")
    if not os.path.exists(pred_path):
        return None
    return load_json(pred_path)


def get_case_data(task, case):
    """获取 case 的所有相关数据。"""
    gt_dir = os.path.join(DATA_DIR, f"task{task}", "agent_input", case)
    result = {}

    # GT decision
    dec_path = os.path.join(gt_dir, GT_FILES[task]["decision"])
    if os.path.exists(dec_path):
        result["gt_decision"] = load_json(dec_path)

    # GT reasoning
    rea_name = GT_FILES[task]["reasoning"]
    if rea_name:
        rea_path = os.path.join(gt_dir, rea_name)
        if os.path.exists(rea_path):
            result["gt_reasoning"] = load_json(rea_path)

    # Structured prompt (case facts)
    sp_path = os.path.join(gt_dir, "structured-prompt.json")
    if os.path.exists(sp_path):
        result["structured_prompt"] = load_json(sp_path)

    # Clinical data
    clin_name = GT_FILES[task]["clinical"]
    if clin_name:
        cd_path = os.path.join(gt_dir, clin_name)
        if os.path.exists(cd_path):
            result["clinical_data"] = load_json(cd_path)

    return result


def extract_case_facts(task, case_data):
    """从 structured_prompt 和 clinical_data 提取关键 case facts。"""
    sp = case_data.get("structured_prompt", {})
    cd = case_data.get("clinical_data", {})
    facts = {}

    if task == 1:
        facts["age"] = sp.get("age")
        facts["psa"] = sp.get("psa")
        facts["pirads"] = sp.get("pirads")
        facts["psad"] = sp.get("psad")
        facts["dre"] = sp.get("dre")
        facts["cspca"] = sp.get("cspca")
        facts["prostate_volume"] = sp.get("vol")
        facts["family_history"] = sp.get("fh")
        facts["previous_biopsy"] = sp.get("bx")
        # PSA trend
        if isinstance(cd, dict) and "psa_trend" in cd:
            facts["psa_trend"] = cd["psa_trend"]
        if isinstance(cd, dict) and "radiology_report" in cd:
            facts["radiology_report"] = cd["radiology_report"][:500]
    elif task == 2:
        facts["age"] = sp.get("age")
        facts["psa"] = sp.get("psa")
        facts["pirads"] = sp.get("pirads")
        facts["psad"] = sp.get("psad")
        facts["cspca"] = sp.get("cspca")
        facts["dre"] = sp.get("dre")
        if isinstance(cd, dict):
            if "pathology_report" in cd:
                facts["pathology_report"] = cd["pathology_report"][:500]
            if "radiology_report" in cd:
                facts["radiology_report"] = cd["radiology_report"][:500]
    elif task == 3:
        facts["age"] = sp.get("age")
        facts["psa"] = sp.get("psa")
        facts["dre"] = sp.get("dre")
        if isinstance(cd, dict):
            if "pathology_report" in cd:
                facts["pathology_report"] = cd["pathology_report"][:500]
            if "surgical_pathology_report" in cd:
                facts["surgical_pathology_report"] = cd["surgical_pathology_report"][:500]
            if "radiology_report" in cd:
                facts["radiology_report"] = cd["radiology_report"][:500]

    return facts


def get_pred_decision(task, pred):
    """从 prediction 提取决策。"""
    if task == 1:
        return pred.get("biopsy_decision", "")
    elif task == 2:
        tr = pred.get("treatment_recommendation", {})
        return tr.get("primary", "") if isinstance(tr, dict) else ""
    elif task == 3:
        return f"event={pred.get('event')}, months={pred.get('months_to_recurrence')}"
    return ""


def get_gt_decision_str(task, gt_decision):
    """从 GT 提取决策字符串。"""
    if task == 3:
        if isinstance(gt_decision, dict):
            return f"event={gt_decision.get('event')}, months={gt_decision.get('months_to_recurrence')}"
        return str(gt_decision)
    return str(gt_decision)


def build_judge_prompt(task, case_facts, gt_decision, pred_free_text):
    """构造 judge 模型的 prompt。"""
    key_vars = KEY_VARIABLES[task]

    system_prompt = (
        "You are a clinical reasoning evaluation expert specializing in prostate cancer "
        "decision-making. Your task is to evaluate whether an LLM-generated rationale (free_text) "
        "aligns with the ground truth decision and covers the key clinical variables. "
        "You must output ONLY a valid JSON object, no other text."
    )

    facts_str = json.dumps(case_facts, indent=2, default=str, ensure_ascii=False)

    user_prompt = f"""Evaluate the following clinical reasoning text for rationale alignment.

## Case Facts
{facts_str}

## Ground Truth Decision
{gt_decision}

## Key Variables to Check
{', '.join(key_vars)}

## Prediction Free Text (to evaluate)
"{pred_free_text}"

## Evaluation Criteria
1. Does the free_text mention the key clinical variables that influence the decision?
2. Does the free_text explain WHY the decision was made (causal reasoning)?
3. Is the reasoning direction consistent with the ground truth decision?

## Output Format (JSON only, no markdown)
{{"score": 0.0-1.0, "covered_points": ["variable1", ...], "missing_points": ["variable1", ...], "rationale": "brief explanation"}}
"""

    return system_prompt, user_prompt


def call_ollama(system_prompt, user_prompt, retry=True):
    """调用 Ollama HTTP API，返回解析后的 JSON dict。"""
    payload = {
        "model": JUDGE_MODEL,
        "system": system_prompt,
        "prompt": user_prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0.1,
            "num_predict": 512,
        },
    }

    for attempt in range(2 if retry else 1):
        try:
            req = urllib.request.Request(
                OLLAMA_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            text = result.get("response", "").strip()

            # 尝试直接解析 JSON
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                # 尝试从 markdown code block 中提取
                match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
                if match:
                    return json.loads(match.group(1))
                # 尝试提取第一个 JSON 对象
                match = re.search(r'\{[^{}]*"score"[^{}]*\}', text, re.DOTALL)
                if match:
                    return json.loads(match.group(0))
                # 最后尝试
                match = re.search(r'\{.*\}', text, re.DOTALL)
                if match:
                    return json.loads(match.group(0))

                if attempt == 0 and retry:
                    time.sleep(2)
                    continue
                return {"score": -1, "error": f"JSON parse failed: {text[:200]}"}

        except urllib.error.URLError as e:
            if attempt == 0 and retry:
                time.sleep(5)
                continue
            return {"score": -1, "error": f"Ollama request failed: {str(e)}"}
        except Exception as e:
            if attempt == 0 and retry:
                time.sleep(2)
                continue
            return {"score": -1, "error": f"Unexpected error: {str(e)}"}

    return {"score": -1, "error": "Max retries exceeded"}


def normalize_score(result):
    """规范化 judge 输出。"""
    score = result.get("score", -1)
    if isinstance(score, str):
        try:
            score = float(score)
        except ValueError:
            score = -1
    if score is not None and not isinstance(score, (int, float)):
        score = -1
    result["score"] = float(score) if score is not None else -1.0
    return result


# ── 评估主逻辑 ──

def eval_task_geval(task, max_cases=None):
    """评估一个 task 的 GEval。"""
    gt_cases = get_gt_cases(task)
    results = []
    errors = 0

    # 筛选有 prediction + free_text 的 case
    evaluable = []
    for case in gt_cases:
        pred = get_prediction(task, case)
        if pred is None:
            continue
        free_text = pred.get("free_text", "")
        if not free_text or not free_text.strip():
            continue
        evaluable.append((case, pred))

    if max_cases:
        evaluable = evaluable[:max_cases]

    total = len(evaluable)
    print(f"  可评估 case: {total}")

    for i, (case, pred) in enumerate(evaluable):
        case_data = get_case_data(task, case)
        case_facts = extract_case_facts(task, case_data)
        gt_decision = get_gt_decision_str(task, case_data.get("gt_decision", ""))
        pred_free_text = pred.get("free_text", "")
        pred_decision = get_pred_decision(task, pred)

        system_prompt, user_prompt = build_judge_prompt(
            task, case_facts, gt_decision, pred_free_text
        )

        result = call_ollama(system_prompt, user_prompt)
        result = normalize_score(result)
        result["case_id"] = case
        result["task"] = task
        result["gt_decision"] = gt_decision
        result["pred_decision"] = pred_decision
        result["pred_free_text"] = pred_free_text[:300]

        results.append(result)

        if result["score"] == -1:
            errors += 1

        # 进度日志
        if (i + 1) % 10 == 0 or i == 0 or i == total - 1:
            score = result["score"]
            print(f"  [{i+1}/{total}] {case}: score={score:.2f}" if score >= 0 else f"  [{i+1}/{total}] {case}: ERROR")

        # 短暂暂停避免过载
        time.sleep(0.5)

    return results, total, errors


# ── 报告生成 ──

def generate_report(task_results):
    lines = []
    lines.append("# GEval 评估报告 (rationale alignment)")
    lines.append("")
    lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> Judge 模型: {JUDGE_MODEL}")
    lines.append(f"> 预测目录: {PRED_DIR}")
    lines.append(f"> 评估方式: Ollama judge 模型近似评估（最终以 GC 提交为准）")
    lines.append("")

    for task in [1, 2, 3]:
        results, total, errors = task_results[task]
        valid_scores = [r["score"] for r in results if r["score"] >= 0]

        if task == 1:
            lines.append(f"=== Task1 GEval 评估（{total} case）===")
        elif task == 2:
            lines.append(f"=== Task2 GEval 评估（{total} case）===")
        elif task == 3:
            lines.append(f"=== Task3 GEval 评估（仅 {total} case，全量待重跑）===")

        lines.append("")

        if not valid_scores:
            lines.append("  无有效评分，跳过")
            lines.append("")
            continue

        avg = sum(valid_scores) / len(valid_scores)
        high = sum(1 for s in valid_scores if s >= 0.8)
        mid = sum(1 for s in valid_scores if 0.5 <= s < 0.8)
        low = sum(1 for s in valid_scores if s < 0.5)

        lines.append(f"平均 rationale alignment: {avg:.3f}")
        lines.append(f"  - 高分(≥0.8): {high} case ({high/len(valid_scores)*100:.1f}%)")
        lines.append(f"  - 中分(0.5-0.8): {mid} case ({mid/len(valid_scores)*100:.1f}%)")
        lines.append(f"  - 低分(<0.5): {low} case ({low/len(valid_scores)*100:.1f}%)")
        lines.append(f"  - 解析失败: {errors} case")

        # 典型低分 case
        low_cases = sorted([r for r in results if 0 <= r["score"] < 0.5], key=lambda x: x["score"])
        if low_cases:
            lines.append("")
            lines.append(f"典型低分 case（前 5）:")
            for r in low_cases[:5]:
                score = r["score"]
                missing = r.get("missing_points", [])
                if isinstance(missing, list):
                    missing_str = ", ".join(missing[:5])
                else:
                    missing_str = str(missing)[:100]
                lines.append(f"  - {r['case_id']}: score={score:.2f}, missing=[{missing_str}]")

        # 典型高分 case
        high_cases = sorted([r for r in results if r["score"] >= 0.8], key=lambda x: -x["score"])
        if high_cases:
            lines.append("")
            lines.append(f"典型高分 case（前 3）:")
            for r in high_cases[:3]:
                score = r["score"]
                covered = r.get("covered_points", [])
                if isinstance(covered, list):
                    covered_str = ", ".join(covered[:5])
                else:
                    covered_str = str(covered)[:100]
                lines.append(f"  - {r['case_id']}: score={score:.2f}, covered=[{covered_str}]")

        lines.append("")

    # 验收标准
    lines.append("=== 验收标准 ===")
    lines.append("")
    lines.append("| 指标 | 当前值 | 目标 | 状态 |")
    lines.append("|---|---|---|---|")
    for task in [1, 2, 3]:
        results, total, errors = task_results[task]
        valid_scores = [r["score"] for r in results if r["score"] >= 0]
        avg = sum(valid_scores) / len(valid_scores) if valid_scores else 0
        lines.append(f"| task{task} GEval | {avg:.3f} | 建立基线，P0-B 后提升 ≥0.05 | 基线已建立 |")
    lines.append(f"| Ollama judge 可用率 | {sum(t[2] for t in task_results.values())} 错误 / {sum(t[1] for t in task_results.values())} 总 | 100% 跑通 | {'✅' if sum(t[2] for t in task_results.values()) == 0 else '⚠️'} |")
    lines.append("")

    return "\n".join(lines)


# ── 主函数 ──

def main():
    global JUDGE_MODEL
    print("=" * 60)
    print("Agent 2 P0-D: GEval 评估 (rationale alignment)")
    print("=" * 60)
    print(f"Judge 模型: {JUDGE_MODEL}")
    print(f"Ollama URL: {OLLAMA_URL}")
    print()

    # 检查 Ollama 可用性
    print("[检查 Ollama...]")
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
        models = [m["name"] for m in tags.get("models", [])]
        print(f"  已安装模型: {models}")
        if JUDGE_MODEL not in models and f"{JUDGE_MODEL}:latest" not in models:
            # 检查部分匹配
            found = any(JUDGE_MODEL in m for m in models)
            if not found:
                print(f"  ⚠️ 模型 {JUDGE_MODEL} 未安装！请运行: ollama pull {JUDGE_MODEL}")
                print("  尝试使用已安装的模型...")
                if models:
                    JUDGE_MODEL = models[0]
                    print(f"  使用: {JUDGE_MODEL}")
                else:
                    print("  无可用模型，退出。")
                    return
    except Exception as e:
        print(f"  ❌ Ollama 不可用: {e}")
        print("  请先启动 Ollama: ollama serve")
        return
    print()

    # 冒烟测试：先跑 5 case
    smoke_mode = "--full" not in sys.argv
    if smoke_mode:
        print("[冒烟测试] 先跑 5 case 验证 prompt + JSON 解析...")
        max_cases = 5
    else:
        print("[全量模式]")
        max_cases = None
    print()

    # 加载缓存（全量模式下）
    cache = {}
    if not smoke_mode and os.path.exists(CACHE_PATH):
        cache = load_json(CACHE_PATH)
        print(f"  已加载缓存: {len(cache)} case")

    task_results = {}
    for task in [1, 2, 3]:
        print(f"[评估 Task{task} GEval...]")
        results, total, errors = eval_task_geval(task, max_cases=max_cases if smoke_mode else None)
        task_results[task] = (results, total, errors)

        # 缓存结果
        if not smoke_mode:
            for r in results:
                cache[f"task{task}_{r['case_id']}"] = r
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)

        valid_scores = [r["score"] for r in results if r["score"] >= 0]
        if valid_scores:
            avg = sum(valid_scores) / len(valid_scores)
            print(f"  平均分: {avg:.3f} ({len(valid_scores)} valid, {errors} errors)")
        else:
            print(f"  无有效评分 ({errors} errors)")
        print()

    # 生成报告
    report = generate_report(task_results)
    report_path = REPORT_PATH if not smoke_mode else REPORT_PATH.replace(".md", "_smoke.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[报告已写入] {report_path}")

    if smoke_mode:
        print()
        print("冒烟测试完成。检查结果后运行 --full 进行全量评估。")
    else:
        print()
        print("全量评估完成。")


if __name__ == "__main__":
    main()
