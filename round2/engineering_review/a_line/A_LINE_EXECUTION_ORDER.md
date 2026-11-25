# A 线真实执行顺序（源码级还原）

本文不是从报告推断，而是直接读 `output/s4_shared_plan_performance_20261115_v2r1/` 下的逐例事件账本得出。

## 一、两臂的真实事件序列

同一病例（`PT-pseudo_033f1a8e1503`，task1），两臂逐事件对比：

```
fixed   (12 事件):
plan_bound → tool:req/ret → tool:req/ret → tool:req/ret → acquisition_stop
           → model:req/ret → model:req/ret

adaptive (21 事件):
plan_bound → tool:req/ret → model:req/ret → policy_decision
           → tool:req/ret → model:req/ret → policy_decision
           → tool:req/ret → model:req/ret → policy_decision
           → acquisition_stop → model:req/ret → model:req/ret
```

**差异只有一处**：adaptive 在每个 `tool_return` 之后插入一组 `model_request → model_return → policy_decision`，由策略决定下一步取什么；fixed 则按 plan 一次性走完 3 次取证再停。

这解释了为什么 adaptive 的 `policy_calls_adaptive = 288`（对应 91 例）而固定臂没有：每一轮取证都多一次决策调用。

## 二、这推翻了报告里的功能链假设

报告假设的顺序是「Planning → 状态控制 → ReAct 循环 → 工具调用 → …」。实测顺序是：

1. **Plan 先绑定，再取任何证据**。事件 `initial_plan_bound` 在 `seq=0`，且 `initial_plan.json` 里已有 `plan_id` 与 `binding.upfront_context_sha256`——即计划绑定发生在看见任何隐藏观测之前。
2. **工具调用可以不等模型**。fixed 臂 plan 绑定后连续 3 次工具调用之间没有任何 model 事件。所以"模型计划结束后立即执行工具"不是 bug，是设计：plan 已是可执行的动作序列。
3. **`acquisition_stop` 是独立事件**，不是隐式结束。
4. **最终诊断与填表在取证全部结束之后**，两次 model 调用串行。

## 三、Plan 到底是什么

**是模型生成的 JSON，受严格 schema 约束，不是自然语言，也不是程序内置动作集。**

证据在 `common_plans/task1/<case>/model_calls.jsonl` 第一条 `model_request` 的 system prompt：

```
[SHARED_INITIAL_PLAN] Plan evidence acquisition before any hidden observations.
Return ONLY JSON {"additionalProperties": false, "properties": {"actions": {
  "items": {"anyOf": [
    {"properties": {"purpose": {...}, "query": {"type": "null"},
                    "tool": {"const": "get_psa_trend"}}, ...},
    {... "tool": {"const": "get_lab_results"} ...},
    ...
  ]}}, ...}}
```

关键点：

- `tool` 是 **`const` 枚举**——模型不能自选工具名，只能从预定义集合里挑，这是 schema 层的硬约束。
- `purpose` 有 `minLength: 1 / maxLength: 256` 上限。
- `additionalProperties: false`，输出不能带多余字段。
- `planner_max_tokens: 1024`，`planner_reasoning_tokens: 512`。

所以「Plan 是自然语言还是动作集合」这个二选一本身就设错了：**它是被 schema 压缩成动作集合的自然语言决策**，模型的自由度只在"选哪个工具、purpose 怎么写"。

## 四、预算怎么算

`initial_plan.json` 的 binding 字段直接给出：

```json
"budget_units": 3,
"max_attempts": 6,
"options": {
  "diagnosis_attempts": 2,
  "max_actions": 6,
  "max_policy_turns": 5,
  "control_format_retries": 2,
  "max_consecutive_failed_pairs": 3,
  "context_limit": 16384,
  "context_reserve": 128,
  "seed": 20261113
}
```

- **信息预算** = `budget_units: 3`（本例），对应 `accounting.json` 里的 `information_units`
- **尝试预算** = `max_attempts: 6` / `max_policy_turns: 5`
- 诊断最多 2 次尝试，格式错误最多重试 2 次，连续失败对上限 3

## 五、91 例这个数字怎么来的

`candidate_execution_config.json`：

```json
"cases": 46,
"arm_instances": 92,
"actual_llm_calls": 0
```

**46 例 × 2 臂 = 92 次实例**，其中 91 次有完整记录——"91 例"不是 91 个病人，是 92 个臂实例里有效的那 91 个。`accounting.json` 的 `risk_groups: 91` 与 `holdout_eligibility.json` 的 `t1_cases: 91` 同源。

## 六、`actual_llm_calls: 0` 的含义（已澄清）

`candidate_execution_config.json` 里写 `actual_llm_calls: 0`，但事件账本有大量 `model_request/model_return`，`model_calls.jsonl` 也有真实 token 计数（`input_tokens: 2828`）。

原因是这个配置文件由 `tools/prepare_shared_plan_study.py` 写入，而该脚本 docstring 第一行是：

> `"""Freeze S4/P7 for review. No generation, target decoding, or training."""`

即该字段描述的是**准备/冻结阶段自身**没有发起生成，不代表后续运行没有模型调用。看到 `actual_llm_calls: 0` 就推断"这次实验成本为零"是误读——真实调用量应看 `ledger.json` 的 `model_calls_by_stage` 与 `accounting.json` 的 `policy_calls_adaptive`（288 次）。

## 七、可公开性分层

| 文件 | 含患者临床值 | 处置 |
|---|---|---|
| `candidate_events.jsonl` | 否（仅 kind/plan_id/sha256/时间戳） | **已同步** |
| `initial_plan.json` | 否（binding + 预算 + options） | **已同步** |
| `candidate_execution_config.json` | 否（resolved 配置） | **已同步** |
| `model_calls.jsonl` | **是**（含 upfront context 与工具返回原文） | 未同步 |
| `candidate_checkpoint.json` | **是**（21 KB 状态快照） | 未同步 |
| `information_access_snapshot.json` | **是**（已取栏目内容） | 未同步 |

后三类如需公开，要先做值域替换（PSA 数值、影像分级等换成占位符），再逐例复核。
