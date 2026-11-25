# T1 拯救计划 · 1D — _fact_check_flags schema bug 静态审计

> 日期: 2026-08-29 ｜ 层级: L1 静态审计（0 推理）｜ 结论: **已确认**，附最小修复方案（不实施，需 L2 验证）

---

## 1. 审计项与结论

| # | 审计项 | 证据 | 结论 |
|---|---|---|---|
| 1 | fact_check.py 仅 has_false 时写 `_fact_check_flags` | [fact_check.py L664-674](file:///root/shared-nvme/chimera-agent-baseline/src/chimera_agent_baseline/agent/fact_check.py#L664-L674): `if result.has_false: judgment["_fact_check_flags"] = [...]` | ✅ 确认 |
| 2 | 全源码无读取方 | `grep -rn "_fact_check_flags" src/` → 仅 [fact_check.py:666](file:///root/shared-nvme/chimera-agent-baseline/src/chimera_agent_baseline/agent/fact_check.py#L666) 1 处（写入） | ✅ 确认（0 处读取） |
| 3 | Task1/2/3Output 均 extra="forbid" | [schema.py L253](file:///root/shared-nvme/chimera-agent-baseline/src/chimera_agent_baseline/output/schema.py#L253) / [L269](file:///root/shared-nvme/chimera-agent-baseline/src/chimera_agent_baseline/output/schema.py#L269) / [L285](file:///root/shared-nvme/chimera-agent-baseline/src/chimera_agent_baseline/output/schema.py#L285)（类定义在 L252/268/284） | ✅ 确认 |
| 4 | 失败 case 报错原文与该链路吻合 | `.failed` + trace.json: `ValidationError: 1 validation error for Task1Output / _fact_check_flags / Extra inputs are not permitted [type=extra_forbidden, input_value=[{'text': 'a PSA density ...category': 'threshold'}]]` | ✅ 确认 |

## 2. 报错链路（静态追踪）

```
form_fill.py L335  atomic_fact_check(task, case_id, transcript, judgment, ...)
  └─ fact_check.py L665-674   has_false=True → judgment["_fact_check_flags"] = [FALSE claims]
form_fill.py L388  full = assemble_full_output(task, case_id, patient, reveal_sequence, judgment)
  └─ schema.py L466  model = TASK_OUTPUT_MODELS[task](**full)   # Task1Output extra="forbid"
                     → ValidationError: _fact_check_flags extra_forbidden
```

触发条件（AND）:
1. `agent.form_fill.atomic_check=True`（否则 L334 块不执行）
2. 该 case 的 fact_check 分解出 ≥1 条 FALSE claim（`has_false=True`）

**为何 0827 生产全量未暴露**: 生产 T1 为纯 LLM 无反思，`auto_heal_task1.sh` 已移除 self_refine/atomic_check → `_fact_check_flags` 从不写入 → 无 extra 字段。
**为何消融 A 暴露**: atomic_check=True，PT-pseudo_4bfd4ec864d8 检出 1 条 FALSE claim（PSA density 阈值类）→ 写入 → 校验崩溃。

## 3. 最小修复方案（只写方案，不实施，需 L2 验证）

**修复点**: [form_fill.py](file:///root/shared-nvme/chimera-agent-baseline/src/chimera_agent_baseline/agent/form_fill.py#L388) `assemble_full_output` 调用之前（或在 atomic_check 块 L340 后），插入:

```python
# atomic_fact_check 会向 judgment 注入内部字段 _fact_check_flags，
# 该字段无任何下游读取方，且 Task1/2/3Output extra="forbid" 会拒绝它。
judgment.pop("_fact_check_flags", None)
```

**安全性论证**:
- 无读取方（审计项 2）→ pop 零信息损失；
- fact_check.py L664 注释声称 "Collect fact-check flags for downstream free_text rewriting"，但该 downstream 从未实现（grep 无调用）→ 可同步清理该段或保留占位；
- 修复对 decision/confidence/weights/free_text 均无影响（只移除一个内部标记字段）。

**L2 验证要求**:
- 重跑消融 A 中失败的 1 case（PT-pseudo_4bfd4ec864d8）应成功产出 Task1Output；
- 抽查 6-10 case 确认无回归（尤其 has_false 的 case: confidence 仍按原逻辑降级，仅不再携带 flags）；
- 若方案 D（T1 启用 atomic_check）进入实施，必须先合并本修复。

## 4. 关联材料

- 失败 case 完整报错: [.failed](file:///root/shared-nvme/chimera-agent-baseline/test/output.ablation_A_atomic_20260829/task1/PT-pseudo_4bfd4ec864d8/.failed) 与 [trace.json](file:///root/shared-nvme/chimera-agent-baseline/test/output.ablation_A_atomic_20260829/task1/PT-pseudo_4bfd4ec864d8/trace.json)（status=failed, error_type=ValidationError）
- 该 case 在 0827 final 为 TP（yes/clear），修复后预期恢复为成功 case（决策大概率 yes，confidence 可能被降级）

---
*声明: 本次审计未修改任何文件、未运行任何代码变更，仅静态阅读 src/ 与既有输出。*
