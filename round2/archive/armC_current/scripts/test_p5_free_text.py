#!/usr/bin/env python3
"""L0 单测 — P5 free_text fallback 修复验证。

验证 _final_answer_tail / _is_tool_call_block：
1. transcript 带 <function=...> 工具调用尾巴 → 提取出正文块（不含工具语法）
2. 纯正文 transcript → 行为不变（与旧 (transcript)[-500:] 一致）
3. 真实中毒 case（t1_full_v2 的 PT-pseudo_0930885168c9）→ 提取结果无 <function=
运行: .venv/bin/python scripts/test_p5_free_text.py
"""
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from chimera_agent_baseline.agent.form_fill import (  # noqa: E402
    _final_answer_tail,
    _is_tool_call_block,
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


# ── 用例 1: <function=...> 尾巴 → 提取正文块 ──
print("== 用例 1: transcript 带 <function=...> 工具调用尾巴 ==")
t1 = (
    "The patient has PI-RADS 4 lesion and rising PSA. Biopsy is indicated "
    "because of high-risk features and abnormal imaging findings.\n"
    "The MRI finding overrides the low PSAD per EAU guidelines.\n"
    "</think>\n"
    "\n"
    "<tool_call>\n"
    "<function=search_guidelines>\n"
    "<parameter=query>\n"
    "EAU biopsy recommendation PI-RADS 4\n"
    "</parameter>\n"
    "</function>\n"
    "</tool_call>"
)
out1 = _final_answer_tail(t1)
check("提取结果不含 <function=", "<function=" not in out1, f"got: {out1!r}")
check("提取结果不含 <tool_call", "<tool_call" not in out1)
check("提取的是正文块（含 PI-RADS 临床内容）", "PI-RADS" in out1, f"got: {out1!r}")
check("提取结果非空且未截断正文", len(out1) >= 100, f"len={len(out1)}")
# [-500:] 语义保持：fallback 仍只取尾部 500 字符
check("[-500:] 语义保持", out1 == (_final_answer_tail(t1))[-500:])

# ── 用例 2: 纯正文 → 行为不变 ──
print("== 用例 2: 纯正文 transcript ==")
t2 = (
    "Patient is a 62-year-old male with PSA 8.7 ng/mL, PI-RADS 3 lesion, "
    "PSA density 0.25, and a prior positive biopsy. The high PSA density "
    "and rising PSA velocity support proceeding with biopsy despite the "
    "equivocal PI-RADS 3 lesion."
)
out2 = _final_answer_tail(t2)
check("纯正文返回原文（行为不变）", out2 == t2, f"got: {out2!r}")
check("纯正文 [-500:] 与旧行为一致", out2[-500:] == t2[-500:])

# 含空行但无工具调用的纯正文 → 仍应返回原文（无工具块即行为不变）
t3 = "Paragraph one with enough clinical detail to exceed one hundred characters easily.\n\n" + t2
out3 = _final_answer_tail(t3)
check("无工具块（含空行）返回原文", out3 == t3)

# ── 用例 3: 纯 JSON 工具调用块被跳过 ──
print("== 用例 3: 纯 JSON 工具调用 ==")
t4 = (
    "The decision is supported by PSA density above threshold and imaging "
    "findings consistent with clinically significant disease requiring "
    "confirmatory biopsy for treatment planning."
    '\n\n{"function": "search_guidelines", "arguments": {"query": "EAU biopsy PI-RADS 4"}}'
)
out4 = _final_answer_tail(t4)
check("JSON 工具调用被跳过", "search_guidelines" not in out4 and "function" not in out4, f"got: {out4!r}")
check("JSON 用例提取到正文", "PSA density" in out4)

# ── 用例 4: 真实中毒 case（t1_full_v2）──
print("== 用例 4: 真实中毒 case PT-pseudo_0930885168c9 ==")
real = os.path.join(
    PROJECT_ROOT, "test", "output.t1_full_v2_20260829", "task1",
    "PT-pseudo_0930885168c9", "trace.json",
)
if os.path.exists(real):
    tr = json.load(open(real))
    msgs = [m for m in tr["messages"] if m.get("type") == "ai"]
    content = msgs[-1]["content"] if msgs else ""
    out5 = _final_answer_tail(content)
    check("真实 case 提取结果不含 <function=", "<function=" not in out5)
    check("真实 case 提取结果不含 <tool_call", "<tool_call" not in out5)
    check("真实 case 提取结果含临床正文", len(out5) >= 100, f"len={len(out5)}")
    # 旧行为对照：直接截尾必中毒
    old = content[-500:]
    check("对照：旧行为确实中毒（确保用例有效）", "<function=" in old or "<tool_call" in old)
else:
    print("  [SKIP] 真实 case 不存在（路径: output.t1_full_v2_20260829）")

# ── 工具块判定 ──
print("== 辅助: _is_tool_call_block ==")
check("tool_call 块判定", _is_tool_call_block("<tool_call>\n<function=get_x>") is True)
check("<function= 块判定", _is_tool_call_block("<function=get_x>") is True)
check("纯 JSON 工具调用判定", _is_tool_call_block('{"name": "get_x"}') is True)
check("正文块判定", _is_tool_call_block("The patient has PI-RADS 4") is False)
check("空块判定", _is_tool_call_block("   ") is True)

print()
print(f"L0 结果: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
