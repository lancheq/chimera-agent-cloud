#!/usr/bin/env python3
"""
diagnose_form_fill.py — 诊断 thinking 污染和空输出问题

扫描所有 form_fill_attempt*.json + critique_iter*.json + refined_full.txt
统计 thinking 污染率、空输出率，抽样 case 原文。
"""

import json
import os
import re
import glob
from collections import defaultdict
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── Thinking contamination patterns (from audit_traces.py + self_refine.py) ──
THINKING_PATTERNS = [
    "here's a thinking process",
    "here is a thinking process",
    "analyze user input",
    "let's analyze the provided",
    "the user wants me to audit",
    "i need to check for:",
    "i'll analyze",
    "let me review",
    "<think>",
    "<thinking>",
    "okay, let me",
    "alright, i",
    "sure, i",
    "let me think",
    "i need to",
    "first, let",
    "to analyze this",
    "i should",
    "let's break down",
]


def check_thinking_contamination(text, first_n=200):
    """Check if text contains thinking process contamination in first N chars."""
    if not text:
        return False
    text_lower = text.lower()[:first_n]
    return any(p in text_lower for p in THINKING_PATTERNS)


def load_json_safe(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def read_text_safe(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def find_files(pattern, root):
    """Find files matching pattern under root."""
    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            if re.match(pattern, fn):
                results.append(os.path.join(dirpath, fn))
    return sorted(results)


def main():
    print("=" * 70)
    print("FORM_FILL TRACE 诊断：Thinking 污染 + 空输出分析")
    print(f"时间: {datetime.now().isoformat()}")
    print(f"项目根: {PROJECT_ROOT}")
    print("=" * 70)

    # ════════════════════════════════════════════════════════════════════
    # Part 1: form_fill_attempt*.json
    # ════════════════════════════════════════════════════════════════════
    print("\n" + "-" * 70)
    print("PART 1: form_fill_attempt*.json")
    print("-" * 70)

    ff_files = find_files(r"form_fill_attempt.*\.json$", PROJECT_ROOT)
    print(f"找到 form_fill_attempt*.json 文件数: {len(ff_files)}")
    for fp in ff_files:
        print(f"  {fp}")

    ff_results = []
    contaminated_cases = []
    empty_cases = []

    for fp in ff_files:
        data = load_json_safe(fp)
        if not data:
            continue

        case_dir = os.path.basename(os.path.dirname(fp))
        raw_response = data.get("raw_response", "")
        user_content = data.get("user_content", "")
        reasoning_content = data.get("reasoning_content", "")
        parse_ok = data.get("parse_ok", False)
        attempt = data.get("attempt", 0)

        contaminated = check_thinking_contamination(raw_response, 200)
        uc_contaminated = check_thinking_contamination(user_content, 200)
        is_empty = len(raw_response) < 10 or not parse_ok

        result = {
            "file": fp,
            "case_id": case_dir,
            "attempt": attempt,
            "raw_response_len": len(raw_response),
            "raw_response_head200": raw_response[:200],
            "user_content_head200": user_content[:200],
            "reasoning_content_len": len(reasoning_content),
            "reasoning_content_head200": reasoning_content[:200],
            "parse_ok": parse_ok,
            "contaminated": contaminated,
            "uc_contaminated": uc_contaminated,
            "is_empty": is_empty,
            "raw_response_full": raw_response,
            "user_content_full": user_content,
        }
        ff_results.append(result)

        if contaminated:
            contaminated_cases.append(result)
        if is_empty:
            empty_cases.append(result)

    n_ff = len(ff_results)
    n_contam = len(contaminated_cases)
    n_empty = len(empty_cases)

    print(f"\n-- form_fill_attempt stats --")
    print(f"总 attempt 数: {n_ff}")
    if n_ff:
        print(f"Thinking 污染数 (前200字符): {n_contam} ({n_contam/n_ff*100:.1f}%)")
        print(f"空输出数 (raw<10 或 parse_ok=false): {n_empty} ({n_empty/n_ff*100:.1f}%)")
        print(f"parse_ok=true: {sum(1 for r in ff_results if r['parse_ok'])}/{n_ff}")
        print(f"parse_ok=false: {sum(1 for r in ff_results if not r['parse_ok'])}/{n_ff}")
        print(f"reasoning_content 非空: {sum(1 for r in ff_results if r['reasoning_content_len'] > 0)}/{n_ff}")
        print(f"user_content 污染 (输入侧): {sum(1 for r in ff_results if r['uc_contaminated'])}/{n_ff}")

    # ════════════════════════════════════════════════════════════════════
    # Part 2: critique_iter*.json
    # ════════════════════════════════════════════════════════════════════
    print("\n" + "-" * 70)
    print("PART 2: critique_iter*.json (thinking 污染源)")
    print("-" * 70)

    critique_files = find_files(r"critique_iter.*\.json$", PROJECT_ROOT)
    print(f"找到 critique_iter*.json 文件数: {len(critique_files)}")

    critique_results = []
    critique_contaminated = []

    for fp in critique_files:
        data = load_json_safe(fp)
        if not data:
            continue

        case_dir = os.path.basename(os.path.dirname(fp))
        critique_raw = data.get("critique_raw_full", "")
        issues = data.get("parsed_issues", [])

        contaminated = check_thinking_contamination(critique_raw, 500)

        result = {
            "file": fp,
            "case_id": case_dir,
            "critique_raw_len": len(critique_raw),
            "critique_raw_head500": critique_raw[:500],
            "issues_count": len(issues),
            "contaminated": contaminated,
            "issues": issues,
        }
        critique_results.append(result)

        if contaminated:
            critique_contaminated.append(result)

    n_crit = len(critique_results)
    n_crit_contam = len(critique_contaminated)

    print(f"总 critique 文件数: {n_crit}")
    if n_crit:
        print(f"Thinking 污染数 (前500字符): {n_crit_contam} ({n_crit_contam/n_crit*100:.1f}%)")
        print(f"有 issues 的 critique: {sum(1 for r in critique_results if r['issues_count'] > 0)}")
        print(f"污染导致 issues=0: {sum(1 for r in critique_results if r['contaminated'] and r['issues_count'] == 0)}")

    # ════════════════════════════════════════════════════════════════════
    # Part 3: refined_full.txt + plan_full.txt
    # ════════════════════════════════════════════════════════════════════
    print("\n" + "-" * 70)
    print("PART 3: refined_full.txt + plan_full.txt")
    print("-" * 70)

    output_dir = os.path.join(PROJECT_ROOT, "output")
    refined_files = find_files(r"refined_full\.txt$", output_dir)
    plan_files = find_files(r"plan_full\.txt$", output_dir)

    print(f"refined_full.txt 文件数: {len(refined_files)}")
    print(f"plan_full.txt 文件数: {len(plan_files)}")

    refined_contam = 0
    refined_empty = 0
    for fp in refined_files:
        text = read_text_safe(fp)
        if text:
            if check_thinking_contamination(text, 500):
                refined_contam += 1
            if len(text.strip()) < 10:
                refined_empty += 1

    plan_contam = 0
    for fp in plan_files:
        text = read_text_safe(fp)
        if text and check_thinking_contamination(text, 500):
            plan_contam += 1

    print(f"refined_full.txt 污染数: {refined_contam}/{len(refined_files)}")
    print(f"refined_full.txt 空输出数: {refined_empty}/{len(refined_files)}")
    print(f"plan_full.txt 污染数: {plan_contam}/{len(plan_files)}")

    # ════════════════════════════════════════════════════════════════════
    # Part 4: reflect.json warnings
    # ════════════════════════════════════════════════════════════════════
    print("\n" + "-" * 70)
    print("PART 4: reflect.json warnings")
    print("-" * 70)

    reflect_files = find_files(r"reflect\.json$", output_dir)
    print(f"reflect.json 文件数: {len(reflect_files)}")

    parse_fail_cases = []
    for fp in sorted(reflect_files):
        data = load_json_safe(fp)
        if not data:
            continue
        warnings = data.get("warnings", [])
        case_id = os.path.basename(os.path.dirname(fp))
        for w in warnings:
            if isinstance(w, str) and ("parse" in w.lower() or "form_fill" in w.lower() or "attempt" in w.lower()):
                parse_fail_cases.append({"case_id": case_id, "warning": w[:300], "file": fp})
                break

    print(f"含 parse/form_fill 失败 warning 的 case 数: {len(parse_fail_cases)}/{len(reflect_files)}")

    # ════════════════════════════════════════════════════════════════════
    # Part 5: prediction.json 空输出
    # ════════════════════════════════════════════════════════════════════
    print("\n" + "-" * 70)
    print("PART 5: prediction.json 空输出扫描")
    print("-" * 70)

    test_dir = os.path.join(PROJECT_ROOT, "test")
    pred_files = find_files(r"prediction\.json$", test_dir)
    print(f"prediction.json 文件数: {len(pred_files)}")

    empty_preds = 0
    for fp in pred_files:
        data = load_json_safe(fp)
        if data is None:
            empty_preds += 1
            continue
        biopsy = data.get("biopsy_decision")
        primary = data.get("treatment_recommendation", {})
        if isinstance(primary, dict):
            primary = primary.get("primary")
        event = data.get("event")
        if not biopsy and not primary and event is None:
            empty_preds += 1

    print(f"空 prediction (关键字段全空): {empty_preds}/{len(pred_files)}")

    # ════════════════════════════════════════════════════════════════════
    # Part 6: 抽样 case 原文
    # ════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("抽样 Case 原文")
    print("=" * 70)

    # 3 个 Critique 污染 case
    print("\n=== 3 个 Critique 污染 Case ===\n")
    for i, case in enumerate(critique_contaminated[:3]):
        print(f"--- 污染 Case {i+1}: {case['case_id']} ---")
        print(f"文件: {case['file']}")
        print(f"\n[critique_raw_full 前 500 字符]:")
        print(case["critique_raw_head500"])
        print(f"\nissues_count: {case['issues_count']}")
        print(f"issues: {case['issues'][:3]}")

        # 查找对应的 form_fill_attempt1.json
        case_trace_dir = os.path.dirname(case["file"])
        ff_path = os.path.join(case_trace_dir, "form_fill_attempt1.json")
        ff_data = load_json_safe(ff_path)
        if ff_data:
            print(f"\n[对应 form_fill_attempt1.json]")
            print(f"user_content 前 500 字符:")
            print(ff_data.get("user_content", "")[:500])
            print(f"\nraw_response 前 500 字符:")
            print(ff_data.get("raw_response", "")[:500])
            print(f"\nparse_ok: {ff_data.get('parse_ok')}")
        else:
            print(f"\n(对应 form_fill_attempt1.json 不存在: {ff_path})")
        print()

    # 3 个 Form Fill 污染 case (如果有)
    print("\n=== 3 个 Form Fill 污染 Case ===\n")
    ff_contam_shown = 0
    for i, case in enumerate(contaminated_cases[:3]):
        print(f"--- FF 污染 Case {i+1}: {case['case_id']} (attempt {case['attempt']}) ---")
        print(f"文件: {case['file']}")
        print(f"\n[user_content 前 500 字符]:")
        print(case["user_content_full"][:500])
        print(f"\n[raw_response 前 500 字符]:")
        print(case["raw_response_full"][:500])
        print(f"\nparse_ok: {case['parse_ok']}")
        print(f"reasoning_content 前 200 字符: {case['reasoning_content_head200']}")
        print()
        ff_contam_shown += 1

    # 补充到 3 个
    if ff_contam_shown < 3:
        print(f"(form_fill 仅 {ff_contam_shown} 个污染 case，从 critique 补充)")
        for i in range(ff_contam_shown, 3):
            if i < len(critique_contaminated):
                case = critique_contaminated[i]
                print(f"\n--- 补充污染 Case {i+1}: {case['case_id']} ---")
                print(f"critique 文件: {case['file']}")
                print(f"\n[critique_raw_full 前 500 字符]:")
                print(case["critique_raw_head500"])
                print(f"\nissues_count: {case['issues_count']}")
                print()

    # 3 个空输出 case
    print("\n=== 3 个空输出 Case ===\n")
    ff_empty_shown = 0
    for i, case in enumerate(empty_cases[:3]):
        print(f"--- 空输出 Case {i+1}: {case['case_id']} (attempt {case['attempt']}) ---")
        print(f"文件: {case['file']}")
        print(f"\n[user_content 前 500 字符]:")
        print(case["user_content_full"][:500])
        print(f"\n[raw_response 完整]:")
        print(case["raw_response_full"][:500])
        print(f"\nparse_ok: {case['parse_ok']}")
        print(f"raw_response_len: {case['raw_response_len']}")
        print()
        ff_empty_shown += 1

    # 补充到 3 个
    if ff_empty_shown < 3:
        print(f"(form_fill 仅 {ff_empty_shown} 个空输出 case，从 reflect.json 补充)")
        for i in range(ff_empty_shown, 3):
            if i < len(parse_fail_cases):
                pc = parse_fail_cases[i]
                case_id = pc["case_id"]
                trace_dir = os.path.join(output_dir, "trace", case_id)
                ff_path = os.path.join(trace_dir, "form_fill_attempt1.json")
                ff_data = load_json_safe(ff_path)
                print(f"\n--- 补充空输出 Case {i+1}: {case_id} ---")
                print(f"reflect warning: {pc['warning']}")
                if ff_data:
                    print(f"\n[form_fill_attempt1.json]")
                    print(f"user_content 前 500 字符:")
                    print(ff_data.get("user_content", "")[:500])
                    print(f"\nraw_response 前 500 字符:")
                    print(ff_data.get("raw_response", "")[:500])
                    print(f"\nparse_ok: {ff_data.get('parse_ok')}")
                    print(f"raw_response_len: {len(ff_data.get('raw_response', ''))}")
                else:
                    print(f"(form_fill_attempt1.json 不存在)")
                print()

    # ════════════════════════════════════════════════════════════════════
    # Part 7: 源头判断
    # ════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("源头判断")
    print("=" * 70)

    rc_nonempty = sum(1 for r in ff_results if r["reasoning_content_len"] > 0)
    uc_contam_count = sum(1 for r in ff_results if r["uc_contaminated"])
    ff_json_start = sum(1 for r in ff_results if r["raw_response_full"].strip().startswith("{"))

    print(f"""
污染源头分析:

① Critique 的 thinking 泄漏进 content:
   - critique 污染数: {n_crit_contam}/{n_crit}""" + (f" ({n_crit_contam/n_crit*100:.1f}%)" if n_crit else ""))
    print(f"   - 污染后 issues=0 的数: {sum(1 for r in critique_results if r['contaminated'] and r['issues_count'] == 0)}/{n_crit_contam if n_crit_contam else 0}")
    print(f"   - _parse_critique_json 前置过滤生效: 污染 critique 被归零 issues, 不触发 refine")

    print(f"""
② Form Fill LLM 自身 thinking:
   - form_fill raw_response 污染数: {n_contam}/{n_ff}""" + (f" ({n_contam/n_ff*100:.1f}%)" if n_ff else ""))
    print(f"   - user_content 污染 (输入侧): {uc_contam_count}/{n_ff}")
    print(f"   - raw_response 以 '{{' 开头 (prefill 效果): {ff_json_start}/{n_ff}")

    print(f"""
③ Server 未分离 reasoning_content:
   - reasoning_content 非空的 form_fill: {rc_nonempty}/{n_ff}
   → {"reasoning_content 为空: server 未返回 reasoning_content, thinking 直接泄漏到 content" if rc_nonempty == 0 else "reasoning_content 有值: server 已分离"}

综合判断:""")
    if n_crit_contam > 0 and n_contam == 0:
        print("   主要污染源: ① Critique 阶段 thinking 泄漏 (不影响 form_fill 直接输出)")
        print("   次要因素: ③ Server 未分离 reasoning_content" if rc_nonempty == 0 else "   次要因素: 无")
    elif n_contam > 0:
        print("   主要污染源: ② Form Fill LLM 自身 thinking 泄漏到 raw_response")
        if rc_nonempty == 0:
            print("   次要因素: ③ Server 未分离 reasoning_content, 导致 thinking 无法被分离")
    else:
        print("   无明显 thinking 污染")

    print(f"\n空输出分析:")
    print(f"   form_fill 空输出: {n_empty}/{n_ff}" + (f" ({n_empty/n_ff*100:.1f}%)" if n_ff else ""))
    print(f"   reflect.json 含 parse 失败 warning: {len(parse_fail_cases)}/{len(reflect_files)}")
    print(f"   prediction.json 空输出: {empty_preds}/{len(pred_files)}")


if __name__ == "__main__":
    main()
