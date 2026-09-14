"""Assert the task-1/2 reasoning socket satisfies the platform's live schema.

GC run 0e45f139-4235-43b4-a6cf-59f0c9fde848 accepted the decision value and the
file names but rejected the reasoning socket:

    The output file 'prostate-biopsy-decision-reasoning.json' is not valid.
    JSON does not fulfill schema: instance is not one of
    ['family_history','previous_notes','laboratory_results','psa_trend',
     'radiology_report']

Cause: the run's internal ``reveal_sequence`` is a list of rich trace dicts,
while the socket types the field as the union of the five clinical segment
names. The vendored fixtures carry dicts too, so local evaluation never
noticed.

This test therefore checks THREE independent things:
  1. every emitted reveal_sequence value is inside the platform vocabulary;
  2. the whole reasoning object still matches the documented keys
     (free_text / confidence / variable_weights / reveal_sequence);
  3. task 3 still emits a bare free-text string (its socket shape is different).

It also replays REAL reveal sequences captured from an actual agent run, so a
transform that only works on toy input cannot pass.

Run inside the image:

    docker run --rm --network=none -v "$PWD/.probe_mount:/mnt:ro" \
      --entrypoint python3 chimera-agent:submit /mnt/reasoning_schema_test.py
"""
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, "/opt/app")

shim = types.ModuleType("src.chimera_agent_baseline.utils")
shim.setup_logging = lambda *a, **k: None
sys.modules["src.chimera_agent_baseline.utils"] = shim

import inference  # noqa: E402

# The platform's live vocabulary, transcribed from the two GC error messages.
# Task 1 has exactly five members: run a7d7744d explicitly refused
# 'pathology_report' for task 1 ("instance 'pathology_report' is not one of
# [...]"), even though the project docs mention it as a task-2 extra.
PLATFORM_VOCAB = {
    "family_history", "previous_notes", "laboratory_results",
    "psa_trend", "radiology_report",
}
TASK2_EXTRA = {"pathology_report"}
VOCAB_BY_TASK = {1: PLATFORM_VOCAB, 2: PLATFORM_VOCAB | TASK2_EXTRA}

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  PASS  {msg}")
    else:
        print(f"  FAIL  {msg}")
        failures.append(msg)


# The transform under test. Reported as a FAIL (not a crash) when absent, so the
# suite still runs to completion against an older image.
_transform = getattr(inference, "_reveal_sequence_for_platform", None)


def transform(seq, task=1):
    """Return the mapped segments, or None when the fix is not usable.

    Distinguishes three states so the test output says which one applies:
      None with _transform None        -> function absent entirely
      old 1-arg signature              -> accepts seq, ignores task (task-scope bug)
      TypeError from a 2-arg call      -> signature patched but call failed
    """
    if _transform is None:
        return None
    try:
        return _transform(seq, task)
    except TypeError:
        try:
            out = _transform(seq)  # old signature: task scoping unavailable
        except Exception:
            return None
        print("    (note: 函数仍是旧的一参签名 -> 无 task 作用域)")
        return out


print("=== 0) 被测函数是否存在 ===")
check(_transform is not None,
      "inference._reveal_sequence_for_platform 存在（缺失=修复未进镜像）")


# --- 1. real reveal_sequence captured from an actual run ---------------------
fixture = Path("/mnt/real_reveal_sequences.json")
print("\n=== 1) 真实运行捕获的 reveal_sequence 回放 ===")
if fixture.exists():
    real = json.loads(fixture.read_text())
    for name, seq in real.items():
        task = int(name.split("_")[0].replace("task", ""))
        got = transform(seq, task)
        print(f"  {name}: {len(seq)} dicts -> {got}")
        check(got is not None and all(isinstance(v, str) for v in got),
              f"{name}: 全部为字符串")
        if task in VOCAB_BY_TASK:
            check(got is not None and set(got) <= VOCAB_BY_TASK[task],
                  f"{name}: 取值全在 task{task} 平台词表内")
        else:
            # Task 3 has no reveal_sequence: its reasoning socket is a bare
            # free-text string, so nothing may be emitted here.
            check(got == [], f"{name}: task{task} 不输出 reveal_sequence（其 socket 结构不同）")
        check(got is not None and len(got) == len(set(got)), f"{name}: 无重复")
else:
    print("  (fixture 缺失，跳过回放)")

# --- 2. edge cases ----------------------------------------------------------
print("\n=== 2) 边界情形 ===")
cases = {
    "空列表(文档允许[])": ([], []),
    "None": (None, []),
    "已是合法字符串": (["psa_trend", "radiology_report"], ["psa_trend", "radiology_report"]),
    "未知 key 应被丢弃": ([{"key": "section_s3-unknown"}], []),
    "未知字符串应被丢弃": (["not_a_segment"], []),
    "按 tool 名映射": ([{"tool": "get_mri_report"}], ["radiology_report"]),
    "key 映射": ([{"key": "section_s3-prev"}], ["previous_notes"]),
    "去重": ([{"key": "section_s3-psa"}, {"key": "section_s3-psa"}], ["psa_trend"]),
}
for label, (inp, want) in cases.items():
    got = transform(inp, 1)
    check(got == want, f"{label}: {got} == {want}")

# The regression that cost run a7d7744d: task 1 must never emit pathology_report.
print("\n=== 2b) task 作用域（run a7d7744d 的回归点）===")
path_seq = [{"key": "section_s3-path", "label": "Pathology report"},
            {"tool": "get_surgical_pathology_report"},
            "pathology_report"]
t1 = transform(path_seq, 1)
t2 = transform(path_seq, 2)
print(f"  task1 -> {t1}")
print(f"  task2 -> {t2}")
check(t1 == [], "task1 不输出 pathology_report（平台已明确拒绝）")
check(t2 == ["pathology_report"], "task2 保留 pathology_report（文档允许）")
mixed = transform([{"key": "section_s3-mri"}, {"key": "section_s3-path"}], 1)
check(mixed == ["radiology_report"], f"task1 混合序列只留合法项: {mixed}")

# The whole-object contract, per docs/CHIMERA-agent赛事整理.md lines 86-99.
print("\n=== 3) 整个 reasoning 对象的结构 ===")
pred = {
    "free_text": "PI-RADS 5 ...",
    "confidence": "uncertain",
    "variable_weights": {"psa": "important", "pirads": "decisive"},
    "reveal_sequence": [
        {"key": "section_s3-mri", "label": "Radiology / MRI report"},
        {"key": "section_s3-fh", "label": "Family history (anamnesis)"},
    ],
}
for task in (1, 2):
    obj = inference._reasoning_value(task, pred)
    check(set(obj) == {"free_text", "confidence", "variable_weights", "reveal_sequence"},
          f"task{task}: 键集合正确 {sorted(obj)}")
    check(isinstance(obj["reveal_sequence"], list)
          and all(isinstance(v, str) for v in obj["reveal_sequence"]),
          f"task{task}: reveal_sequence 是字符串列表 {obj['reveal_sequence']}")
    rs = obj["reveal_sequence"]
    check(all(isinstance(v, str) for v in rs)
          and set(rs) <= PLATFORM_VOCAB | TASK2_EXTRA,
          f"task{task}: 取值合法（全部为平台词表内字符串）")
    # Must be JSON-serialisable with no dicts sneaking back in.
    blob = json.dumps(obj, ensure_ascii=False)
    check('"page"' not in blob and '"ts"' not in blob,
          f"task{task}: 无内部 trace 字段泄漏")

t3 = inference._reasoning_value(3, {"free_text": "no reference reasoning"})
check(isinstance(t3, str), f"task3: 仍是裸字符串（其 socket 结构不同）")

# --- 3b. variable_weights: platform key set (run f698486a regression) --------
print("\n=== 3b) variable_weights 键集合 ===")
ALLOWED = {
    1: {"psa", "age", "dre", "comorbidity", "bx", "pirads", "psad", "vol", "cspca", "fh"},
    2: {"psa", "age", "ct", "comorbidity", "pirads", "psad", "cspca",
        "bx_gl_prim", "bx_gl_sec", "bx_isup", "fh"},
}
check(set(ALLOWED[1]) != set(ALLOWED[2]), "task1/task2 键集合确实不同（非同一套）")

# The root cause: the padding dict must no longer contain bx_gl_tert, because
# normalise_to_full_shape() copies every key it lists into EVERY task-2 output.
try:
    from src.chimera_agent_baseline.output.schema import TASK2_VARIABLES
    check("bx_gl_tert" not in TASK2_VARIABLES,
          f"TASK2_VARIABLES 已移除 bx_gl_tert（现 {len(TASK2_VARIABLES)} 个）")
    check(set(TASK2_VARIABLES) == ALLOWED[2],
          "TASK2_VARIABLES 与平台 11 个键完全一致")
except Exception as e:
    check(False, f"无法导入 TASK2_VARIABLES: {e}")

# Adapter-level boundary filter: an out-of-vocabulary key must be dropped.
for task, allowed in ALLOWED.items():
    payload = dict.fromkeys(allowed, "noted")
    payload["bx_gl_tert"] = "decisive"          # the offending key
    payload["totally_made_up"] = "decisive"
    obj = inference._reasoning_value(task, {
        "free_text": "x" * 60, "confidence": "clear",
        "variable_weights": payload, "reveal_sequence": [],
    })
    got = set(obj["variable_weights"])
    check(got == set(allowed), f"task{task}: 越界键被剔除，恰为平台键集")
    check("bx_gl_tert" not in got, f"task{task}: bx_gl_tert 不再出现")
    check(all(v in {"not_used", "noted", "important", "decisive"}
              for v in obj["variable_weights"].values()), f"task{task}: 权重取值合法")

# The real machine-authored task-2 weights (12 keys incl. bx_gl_tert) -> 11.
real_t2 = {"psa": "noted", "age": "important", "ct": "not_used", "comorbidity": "not_used",
           "pirads": "decisive", "psad": "important", "cspca": "noted",
           "bx_gl_prim": "important", "bx_gl_sec": "noted", "bx_gl_tert": "noted",
           "bx_isup": "decisive", "fh": "not_used"}
obj2 = inference._reasoning_value(2, {"free_text": "x" * 60, "confidence": "uncertain",
                                      "variable_weights": real_t2, "reveal_sequence": []})
check(len(obj2["variable_weights"]) == 11,
      f"真实 12 键产物 -> 平台 11 键（实得 {len(obj2['variable_weights'])})")

# --- 3c. v8 回归守护：T1 的 pad+filter 必须是 no-op（T1 已通过，不能弄坏）----
print("\n=== 3c) T1/T3 回归守护（v8 变更不得影响已通过的 task）===")
try:
    from src.chimera_agent_baseline.output.schema import (
        TASK1_VARIABLES, normalise_to_full_shape,
    )
    check(set(TASK1_VARIABLES) == ALLOWED[1],
          f"TASK1_VARIABLES 恰为平台 10 键（实 {len(TASK1_VARIABLES)}）")
    padded = normalise_to_full_shape(1, {"variable_weights": {}})["variable_weights"]
    filtered = inference._variable_weights_for_platform(padded, 1)
    check(set(filtered) == set(ALLOWED[1]),
          f"T1 pad+filter 后端到端仍恰为 10 键（实 {len(filtered)}）")
    check(filtered == padded, "T1 过滤是 no-op（未丢弃任何平台键）")
    # 反向：T1 若混入 T2 专有键，必须被剔除
    bad = dict(padded); bad["bx_gl_tert"] = "decisive"
    check(set(inference._variable_weights_for_platform(bad, 1)) == set(ALLOWED[1]),
          "T1 混入 bx_gl_tert 时被剔除")
except Exception as e:
    check(False, f"T1 回归检查失败: {e}")

# --- 3d. T3 socket 形状回归锁（平台已确认接受；不得再变）-------------------
print("\n=== 3d) T3 socket 形状（平台已接受：裸字符串 + {event, months}）===")
t3_pred = {"free_text": "t" * 60, "variable_weights": {"x": "noted"},
           "reveal_sequence": [{"key": "section_s3-mri"}],
           "event": 1, "months_to_recurrence": 12.5}
t3obj = inference._reasoning_value(3, t3_pred)
check(isinstance(t3obj, str) and t3obj == "t" * 60,
      "T3 reasoning 是裸 free_text 字符串（非对象）")
check("variable_weights" not in str(t3obj) and "reveal_sequence" not in str(t3obj),
      "T3 reasoning 不夹带 weights / reveal_sequence（其 socket 无这些字段）")
d3 = inference._decision_value(3, t3_pred)
check(isinstance(d3, dict) and set(d3) == {"event", "months_to_recurrence"},
      f"T3 decision 恰为 {{event, months_to_recurrence}}: {sorted(d3)}")
check(isinstance(d3["event"], int) and isinstance(d3["months_to_recurrence"], float),
      "T3 decision 类型为 (int, float)")
check(isinstance(inference._decision_value(1, {"biopsy_decision": "yes"}), str),
      "T1 decision 仍是裸字符串（'yes'/'no'）")

# --- 4. decision socket unchanged ------------------------------------------
print("\n=== 4) decision socket 未受影响 ===")
check(inference._decision_value(1, {"biopsy_decision": "yes"}) == "yes", "task1 decision == 'yes'")
check(inference._decision_value(2, {"treatment_recommendation": {"primary": "active_treatment"}})
      == "active_treatment", "task2 decision == action token")
check(inference._decision_value(3, {"event": 1, "months_to_recurrence": 12.5})
      == {"event": 1, "months_to_recurrence": 12.5}, "task3 decision == {event, months}")

print()
print("RESULT:", "ALL PASS" if not failures else f"{len(failures)} FAILED")
for f in failures:
    print("   -", f)
raise SystemExit(1 if failures else 0)
