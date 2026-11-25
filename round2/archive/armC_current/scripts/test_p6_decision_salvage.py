#!/usr/bin/env python3
"""L0 单测 — P6 decision 兜底修复验证。

验证 _salvage_decision / _fill_missing_decision_fields (成功路径) +
_salvage_decision_from_raw / _has_decision / _default_judgment /
_merge_salvaged_into_schema (失败路径)：

成功路径:
1. raw_json 含 decision 但 parser 输出缺字段 → salvage 成功 + warning
2. raw_json 无 decision → DEFAULTED warning 路径（默认值不变）
3. 正常完整输出 → 行为与改前一致（回归保护）

失败路径 (P6-failure):
4. _salvage_decision_from_raw: 6 种 raw_response 形态 (5/6 成功, 空→None)
5. _has_decision / _default_judgment / _merge_salvaged_into_schema

运行: .venv/bin/python scripts/test_p6_decision_salvage.py
"""
import copy
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from chimera_agent_baseline.agent.form_fill import (  # noqa: E402
    _default_judgment,
    _fill_missing_decision_fields,
    _has_decision,
    _merge_salvaged_into_schema,
    _salvage_decision,
    _salvage_decision_from_raw,
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


# ── 1. salvage 成功路径 ──
print("== 用例 1: raw_json 含 decision → salvage 成功 ==")
# T1: parser 输出缺 biopsy_decision，raw_json 里有
j1 = {"confidence": "clear", "variable_weights": {}}
w1 = []
_fill_missing_decision_fields(1, j1, '{"biopsy_decision":"yes","confidence":"clear"}', w1)
check("T1 salvage 命中", j1["biopsy_decision"] == "yes", f"got: {j1['biopsy_decision']!r}")
check("T1 salvage warning", any("decision salvaged from raw response" in x for x in w1), f"w={w1}")

# T1 带空格: "biopsy_decision" : "no"
j2 = {"confidence": "clear", "variable_weights": {}}
w2 = []
_fill_missing_decision_fields(1, j2, '{"biopsy_decision" : "no"}', w2)
check("T1 salvage 带空格", j2["biopsy_decision"] == "no", f"got: {j2['biopsy_decision']!r}")

# T2: 缺 primary，raw_json 嵌套 treatment_recommendation.primary
j3 = {"treatment_recommendation": {"modalities": ["AS"]}, "confidence": "clear", "variable_weights": {}}
w3 = []
_fill_missing_decision_fields(
    2, j3, '{"treatment_recommendation":{"primary":"active_treatment","modalities":["AS"]}}', w3
)
check("T2 salvage 命中", j3["treatment_recommendation"]["primary"] == "active_treatment",
      f"got: {j3['treatment_recommendation'].get('primary')!r}")
check("T2 salvage warning", any("decision salvaged from raw response" in x for x in w3), f"w={w3}")

# _salvage_decision 直接验证
check("_salvage_decision T1 yes", _salvage_decision(1, '{"biopsy_decision":"yes"}') == ("yes", True))
check("_salvage_decision T2 AT", _salvage_decision(2, '{"primary":"active_treatment"}') == ("active_treatment", True))
check("_salvage_decision T2 WW", _salvage_decision(2, '{"primary":"watchful_waiting"}') == ("watchful_waiting", True))

# ── 2. DEFAULTED 路径 ──
print("== 用例 2: raw_json 无 decision → DEFAULTED warning ==")
j4 = {}
w4 = []
_fill_missing_decision_fields(1, j4, '{"confidence":"clear"}', w4)
check("T1 DEFAULTED 默认值", j4["biopsy_decision"] == "no", f"got: {j4.get('biopsy_decision')!r}")
check("T1 DEFAULTED warning", any("decision DEFAULTED (parse incomplete): biopsy_decision" in x for x in w4), f"w={w4}")
check("T1 无 salvage warning", not any("salvaged" in x for x in w4))

j5 = {"treatment_recommendation": {}}
w5 = []
_fill_missing_decision_fields(2, j5, '{"confidence":"clear"}', w5)
check("T2 DEFAULTED 默认值", j5["treatment_recommendation"]["primary"] == "active_surveillance",
      f"got: {j5['treatment_recommendation'].get('primary')!r}")
check("T2 DEFAULTED warning", any("decision DEFAULTED (parse incomplete): primary" in x for x in w5), f"w={w5}")

# 非法值（非 yes/no）不 salvage → DEFAULTED
j6 = {}
w6 = []
_fill_missing_decision_fields(1, j6, '{"biopsy_decision":"maybe"}', w6)
check("T1 非法值不 salvage → no", j6["biopsy_decision"] == "no", f"got: {j6['biopsy_decision']!r}")
check("T1 非法值 DEFAULTED warning", any("decision DEFAULTED" in x for x in w6), f"w={w6}")

check("_salvage_decision 空串", _salvage_decision(1, "") == (None, False))
check("_salvage_decision 非法值", _salvage_decision(1, '{"biopsy_decision":"maybe"}') == (None, False))
check("_salvage_decision T2 非法值", _salvage_decision(2, '{"primary":"xyz"}') == (None, False))
check("_salvage_decision T3 不支持", _salvage_decision(3, '{"biopsy_decision":"yes"}') == (None, False))

# ── 3. 正常完整输出 → 行为与改前一致（回归保护）──
print("== 用例 3: 正常完整输出 → 无改动、无 warning ==")
j7 = {
    "biopsy_decision": "yes",
    "confidence": "clear",
    "variable_weights": {"psa": "important", "age": "noted"},
}
before7 = copy.deepcopy(j7)
w7 = []
_fill_missing_decision_fields(1, j7, '{"biopsy_decision":"yes","confidence":"clear"}', w7)
check("T1 完整输出字节一致", j7 == before7, f"got: {j7}")
check("T1 无 warning", len(w7) == 0, f"w={w7}")

j8 = {
    "treatment_recommendation": {
        "primary": "watchful_waiting",
        "modalities": ["AS"],
        "detail": "low risk",
        "as_protocol": None,
        "as_trigger": None,
    },
    "confidence": "borderline",
    "variable_weights": {"pirads": "noted"},
}
before8 = copy.deepcopy(j8)
w8 = []
_fill_missing_decision_fields(2, j8, '{"treatment_recommendation":{"primary":"watchful_waiting"}}', w8)
check("T2 完整输出字节一致", j8 == before8, f"got: {j8}")
check("T2 无 warning", len(w8) == 0, f"w={w8}")


# ── 4. _salvage_decision_from_raw: 6 种 raw_response 形态 ──
print("== 用例 4: _salvage_decision_from_raw 6 种 raw_response ==")

# 4a. 正常 JSON
r1 = '{"biopsy_decision": "yes", "confidence": "clear"}'
s1 = _salvage_decision_from_raw(r1, 1)
check("4a 正常JSON T1", s1 == {"biopsy_decision": "yes"}, f"got: {s1!r}")

# 4b. 代码块 JSON
r2 = '```json\n{"biopsy_decision": "no"}\n```'
s2 = _salvage_decision_from_raw(r2, 1)
check("4b 代码块JSON T1", s2 == {"biopsy_decision": "no"}, f"got: {s2!r}")

# 4c. 字段散落在散文中
r3 = 'The patient has PSA 12.5 and PI-RADS 5. "biopsy_decision": "yes" was decided based on high-risk features.'
s3 = _salvage_decision_from_raw(r3, 1)
check("4c 字段散落 T1", s3 == {"biopsy_decision": "yes"}, f"got: {s3!r}")

# 4d. thinking 标签污染
r4 = '<thinking>Let me analyze the PSA and PI-RADS scores...\n"biopsy_decision": "no"\n</thinking>'
s4 = _salvage_decision_from_raw(r4, 1)
check("4d thinking污染 T1", s4 == {"biopsy_decision": "no"}, f"got: {s4!r}")

# 4e. 乱码中嵌入合法字段
r5 = 'asdf{"biopsy_decision": "yes"}sdfasdf123'
s5 = _salvage_decision_from_raw(r5, 1)
check("4e 乱码嵌入 T1", s5 == {"biopsy_decision": "yes"}, f"got: {s5!r}")

# 4f. 空文本 → None (回退默认)
s6 = _salvage_decision_from_raw("", 1)
check("4f 空文本 → None", s6 is None, f"got: {s6!r}")
s6b = _salvage_decision_from_raw("   \n  ", 1)
check("4f 空白文本 → None", s6b is None, f"got: {s6b!r}")

# 验证 5/6 成功率
salvage_results = [s1, s2, s3, s4, s5, s6]
success_count = sum(1 for r in salvage_results if r is not None)
check("salvage 成功率 5/6", success_count == 5, f"got: {success_count}/6")

# T2 salvage from raw
s_t2 = _salvage_decision_from_raw(
    'Based on high-risk features "primary": "active_treatment" is recommended', 2
)
check("T2 raw salvage AT", s_t2 == {"treatment_recommendation": {"primary": "active_treatment"}}, f"got: {s_t2!r}")

# T3 salvage from raw
s_t3 = _salvage_decision_from_raw(
    'The patient shows "event": 1 with "months_to_recurrence": 18.5', 3
)
check("T3 raw salvage", s_t3 == {"event": 1, "months_to_recurrence": 18.5}, f"got: {s_t3!r}")

# T3 仅 event 无 months → None
s_t3b = _salvage_decision_from_raw('"event": 1', 3)
check("T3 仅 event 无 months → None", s_t3b is None, f"got: {s_t3b!r}")

# 非法值不 salvage
check("T1 非法值 → None", _salvage_decision_from_raw('{"biopsy_decision": "maybe"}', 1) is None)
check("T2 非法值 → None", _salvage_decision_from_raw('{"primary": "xyz"}', 2) is None)


# ── 5. _has_decision / _default_judgment / _merge_salvaged_into_schema ──
print("== 用例 5: _has_decision / _default_judgment / _merge_salvaged_into_schema ==")

# _has_decision
check("has_decision T1 yes", _has_decision({"biopsy_decision": "yes"}, 1) is True)
check("has_decision T1 missing", _has_decision({"confidence": "clear"}, 1) is False)
check("has_decision T2 yes", _has_decision({"treatment_recommendation": {"primary": "active_treatment"}}, 2) is True)
check("has_decision T2 missing", _has_decision({"treatment_recommendation": {}}, 2) is False)
check("has_decision T2 no rec", _has_decision({"confidence": "clear"}, 2) is False)
check("has_decision T3 yes", _has_decision({"event": 0, "months_to_recurrence": 36.0}, 3) is True)
check("has_decision T3 missing months", _has_decision({"event": 0}, 3) is False)

# _default_judgment
d1 = _default_judgment(1)
check("default T1 biopsy=no", d1["biopsy_decision"] == "no")
check("default T1 confidence=uncertain", d1["confidence"] == "uncertain")
check("default T1 variable_weights={}", d1["variable_weights"] == {})
check("default T1 repeat_test=None", d1["repeat_test"] is None)
check("default T1 free_text non-empty", len(d1["free_text"]) > 0)

d2 = _default_judgment(2)
check("default T2 primary=AS", d2["treatment_recommendation"]["primary"] == "active_surveillance")
check("default T2 modalities=[]", d2["treatment_recommendation"]["modalities"] == [])
check("default T2 confidence=uncertain", d2["confidence"] == "uncertain")

d3 = _default_judgment(3)
check("default T3 event=0", d3["event"] == 0)
check("default T3 months=36", d3["months_to_recurrence"] == 36.0)
check("default T3 repeat_test=None", d3["repeat_test"] is None)

# _merge_salvaged_into_schema
m1 = _merge_salvaged_into_schema({"biopsy_decision": "yes"}, 1)
check("merge T1 salvaged decision", m1["biopsy_decision"] == "yes")
check("merge T1 has confidence", m1["confidence"] == "uncertain")
check("merge T1 has variable_weights", m1["variable_weights"] == {})
check("merge T1 has free_text", len(m1["free_text"]) > 0)
check("merge T1 has repeat_test", m1["repeat_test"] is None)

m2 = _merge_salvaged_into_schema({"treatment_recommendation": {"primary": "active_treatment"}}, 2)
check("merge T2 salvaged primary", m2["treatment_recommendation"]["primary"] == "active_treatment")
check("merge T2 has confidence", m2["confidence"] == "uncertain")
check("merge T2 has modalities", m2["treatment_recommendation"]["modalities"] == [])

m3 = _merge_salvaged_into_schema({"event": 1, "months_to_recurrence": 24.0}, 3)
check("merge T3 salvaged event", m3["event"] == 1)
check("merge T3 salvaged months", m3["months_to_recurrence"] == 24.0)
check("merge T3 has free_text", len(m3["free_text"]) > 0)

print()
print(f"L0 结果: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
