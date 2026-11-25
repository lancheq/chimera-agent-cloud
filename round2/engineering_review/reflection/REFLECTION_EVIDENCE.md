# Reflection 运行证据（P01-G1R）

材料来自 `output/p01_g1r_reflection_20261118/`，是**合成集成测试**产物，不是真实病例运行。它能证明工程机制是否正确，不能证明临床收益。

## 一、这批材料证明了什么

| 指标 | 结果 | 出处 |
|---|---|---|
| 合成集成场景 | **18/18 全部 verified** | `evidence/synthetic_regression_results.json` |
| 测试用例总数 | **151 passed**，7 warnings，54.36s | `tests_logs/final.log` |
| 最终文件写出并回读 | 15 次成功 | 报告口径 |
| 端到端运行 | 有（`entry_receipts.json` 112 KB 逐入口回执） | `evidence/entry_receipts.json` |

## 二、18 个场景覆盖了什么

按机制分类看，而不是按编号顺序：

**A. 候选被正常接纳（4 个）**

| ID | 场景 | 期望选择 | 终态 |
|---|---|---|---|
| S01 | 接纳夹具正确提议 | `accepted_candidate` | `finalized_retained` |
| S06 | 标签不变而理由/权重修改被接纳 | `accepted_candidate` | `finalized_retained` |
| S12 | **末端未授权覆写** | `accepted_candidate` | `finalized_retained` |
| S13 | **末端字段丢失或消费旧对象** | `accepted_candidate` | `finalized_retained` |
| S14 | **显式获批规则覆写** | `accepted_candidate` | 实际 `candidate_then_pro…`（`accepted_then_overridden`） |

S12/S13 直接回答"反思被后续节点覆盖"的问题：**未授权覆写会被拒，字段丢失或消费旧对象也会被检出**。S14 是唯一允许覆写的路径，且必须带显式获批的规则 ID。

**B. 被审核拒绝或检测到但无法执行（4 个）**

| ID | 场景 | 期望 | 说明 |
|---|---|---|---|
| S02 | 拒绝夹具错误提议 | `audit_rejected` | — |
| S03 | 检测有但无可执行纠正 | `detected_no_executable_proposal` | 外层反思的典型结局 |
| S07 | 有效格式但审核拒绝 | `audit_rejected` | 格式合法 ≠ 内容正确 |
| S08 | 提议格式错误或必填 null | `invalid_proposal` | — |

**C. 异常与降级（5 个）**

| ID | 场景 | 处理 |
|---|---|---|
| S05 | 执行但完整候选无变化 | `executed_no_delta`，保留原候选 |
| S09 | 审核异常/超时 | `error_fallback_initial`，回退初始 |
| S10 | 审核未知或缺失 | `blocked_missing_authorization`，阻断 |
| S15 | 无合法初始值且反思异常 | `failed_no_valid_initial`，**不制造 default 诊断** |
| S16 | 回执持久化失败 | `audit_persistence_failed`，**不吞异常** |

**D. 越权与冲突（4 个）**

| ID | 场景 | 断言要点 |
|---|---|---|
| S11 | 错身份、旧 base 或伪造审核摘要 | `binding_or_authority_rejected` |
| S17 | 字段覆盖不完整/来源越权 | 「不得将引用可定位当医学正确；权限不因反思免费扩展」 |
| S18 | 开关关闭与多候选冲突 | 「不拼字段、不静默偏好某机制」 |
| S04 | 合法无问题 | `no_issue`，不动作 |

## 三、反思会不会被后续节点覆盖——机制层面的答案

`evidence/contract_mapping.md` 定义了两层反思，这是理解覆盖问题的关键：

- **外层反思（DraftArtifact / external_reflection）**：产出自由文本，**单独保留摘要，不加入填表消息**。所以它永远影响不到最终字段，只能被记为 `detected_no_executable_proposal`。
- **内层反思（form_fill 内层 artifact）**：产出结构化候选，经 `reflection_review` 审核后才可能被赋值。

最终结果文件区分 **5 种终态**：

| 终态 | 含义 |
|---|---|
| `finalized_retained` | 采纳了反思候选，且末端没有再改 |
| `finalized_initial` | 反思未生效，保留初始判断 |
| `finalized_initial_with_error` | 反思过程出错，回退初始并记录错误 |
| `accepted_then_overridden` | 反思候选先被接纳，随后被显式获批规则改写 |
| `consumption_contract_failed` | 消费契约失败 |

也就是说，"反思改了但最终没用上"不是一个模糊状态，而是有明确字段记录的几种终态之一。S12/S13 证明未授权的覆写会被拦下。

## 四、一个必须说清的边界

`contract_mapping.md` 最后一句：

> **G1R 不授予任何现行后处理覆盖已审候选的生产权威。**

意思是：这套机制在 G1R 阶段被验证为"契约正确"，但**尚未接管生产路径**。当前生产的后处理（`postprocess` / `reasoning_align`）行为不受它约束。所以看到这 18 个场景全过，不等于生产里反思已经被正确消费。

`source_diff.patch`（62.9 KB）涉及 6 个已有文件 + 1 个新文件：

```
inference.py
src/chimera_agent_baseline/runtime_audit.py
src/chimera_agent_baseline/run.py
src/chimera_agent_baseline/agent/graph.py
src/chimera_agent_baseline/agent/form_fill.py
src/chimera_agent_baseline/agent/reflection_contract.py   ← 新增
tests/test_p01_g1r_reflection.py                          ← 新增
tests/test_p01_g1a_runtime.py
```

## 五、目录说明

```
reflection/
├── REFLECTION_EVIDENCE.md          本文件
├── evidence/
│   ├── synthetic_regression_results.json   18 场景预期 vs 实际（核心）
│   ├── contract_mapping.md                 契约字段与事件映射表（读懂机制必看）
│   ├── source_diff.patch                   完整修复 diff（62.9 KB）
│   ├── entry_receipts.json                 112 KB 逐入口回执
│   ├── execution_ledger.json               执行账本
│   ├── manifest.json                       交付清单（含文件哈希）
│   ├── baseline.json / after_status.txt    改动前后状态
│   └── handoff.json                        交接摘要
└── tests_logs/                     13 份测试日志与 JUnit XML
```

**未同步**：`tests/*_cases/*/private/`（21 MB 测试夹具，含临床值）与 `before/`（260 KB 改动前源码副本，与 `source_diff.patch` 内容重复）。

## 六、还缺什么

| 缺口 | 说明 |
|---|---|
| Fact Check 的运行证据 | 本批只覆盖 reflection。`fact_check.py` 源码在 `round2/src/...`，但没找到对应的合成测试产物目录 |
| 真实病例上的反思生效记录 | 需要 `self_refine: true` 的运行，A 线 91 例配置里是 `false` |
