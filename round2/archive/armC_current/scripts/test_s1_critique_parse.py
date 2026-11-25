#!/usr/bin/env python3
"""L0 单测 — S1 self_refine critique 解析修复验证。

验证 _parse_critique_json 的 S1 修复（thinking 前缀不再错杀尾部 JSON）：
① thinking 前缀 + 尾部干净 JSON（真实 case PT-pseudo_0cdfb9410718）
   → 解析出 fabricated_facts / internal_inconsistency issue
② thinking 前缀 + 尾部反引号包裹 JSON → 解析成功
③ 纯 thinking 无任何 JSON → return no issues（原降级语义）
④ 干净 JSON 直接开头 → 行为不变
运行: .venv/bin/python scripts/test_s1_critique_parse.py
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from chimera_agent_baseline.agent.self_refine import (  # noqa: E402
    _extract_tail_critique_json,
    _parse_critique_json,
)

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


# ── ① 真实 case：thinking 前缀 + 尾部干净 JSON ──
print("== 用例 1: 真实 case PT-pseudo_0cdfb9410718 ==")
real = PROJECT_ROOT / "output" / "trace" / "PT-pseudo_0cdfb9410718" / "critique_iter1.json"
if real.exists():
    data = json.load(open(real))
    raw = data["critique_raw_full"]
    clean, issues = _parse_critique_json(raw)
    check("解析出 issues 非空", len(issues) >= 2, f"got {len(issues)}")
    joined = " ".join(issues).lower()
    check("含 fabricated_facts", "fabricated_facts" in joined, joined[:100])
    check("含 internal_inconsistency", "internal_inconsistency" in joined, joined[:100])
    check("clean 为合法 JSON（含 severity）", "severity" in clean)
else:
    print("  [SKIP] 真实 trace 不存在")

# ── ② thinking 前缀 + 尾部反引号包裹 JSON ──
print("== 用例 2: thinking 前缀 + 尾部反引号包裹 JSON ==")
t2 = (
    "The user wants me to audit a transcript.\nLet's analyze the provided text.\n"
    'Check format: `{"issues": [], "severity": "none"}`\n'
    'Output Generation -> `{"issues": ["fabricated_facts: invented lab value."], "severity": "mild"}`'
)
clean2, issues2 = _parse_critique_json(t2)
check("反引号包裹尾部 JSON 解析成功", len(issues2) == 1, f"got {issues2}")
check("issue 内容正确", "fabricated_facts" in issues2[0], issues2)

# ── ③ 纯 thinking 无任何 JSON → 原降级语义 ──
print("== 用例 3: 纯 thinking 无 JSON ==")
t3 = (
    "The user wants me to audit a clinical reasoning transcript for a prostate cancer case.\n"
    "I need to check for over-treatment bias and unsupported claims.\n"
    "After careful review I conclude the reasoning is sound.\n"
)
clean3, issues3 = _parse_critique_json(t3)
check("无 JSON → 空 issues", issues3 == [], f"got {issues3}")
check("返回原始 raw", clean3 == t3.strip(), "clean 应等于 raw")
check("_extract_tail_critique_json 返回 None", _extract_tail_critique_json(t3) is None)

# ── ④ 干净 JSON 直接开头 → 行为不变 ──
print("== 用例 4: 干净 JSON 直接开头 ==")
t4 = '{"issues": ["over_treatment: AS for high-risk patient"], "severity": "severe"}'
clean4, issues4 = _parse_critique_json(t4)
check("直接 JSON 解析成功", len(issues4) == 1, f"got {issues4}")
check("issue 内容正确", "over_treatment" in issues4[0])
check("clean 为规范 JSON", json.loads(clean4)["severity"] == "severe")

print()
print(f"L0 结果: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
