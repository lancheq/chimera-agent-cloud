# ROUND2 G0 最终验收

2026-11-26 · 分支 `refl-g0b` · 代码已归档至 `c67e554` 之后的提交
真实 35B 生成：0 次。本报告只做验收，不启动 C2。

## 结论

**G0 = 部分通过。可交付的工程件齐备，但生产入口只有代码路径、没有一次宿主级执行，因此 C2 不具备启动条件。**

三项判定：

| 项 | 判定 | 依据 |
| --- | --- | --- |
| Producer / Schema / 多轮 / 契约桥接 | PASS | 50 项零生成测试，含 `resolve()` 端到端 |
| 生产入口接线 | **WIRED / NOT EXERCISED** | 代码已接，但从未在真实 graph 上执行过 |
| Medical Reviewer | 实现 PASS / 效果 UNVERIFIED | 有真实调用实现，仅桩模型验证过 |
| Calibration 仲裁 | PASS（策略冻结） / UNVERIFIED（覆盖行为） | 配置冻结且加载校验，未在真实用例上跑 |
| C2 启动条件 | **BLOCKED** | 见 §4 |

## 1 生产入口的真实链路（本轮新接线）

本轮之前，`authority` 只在合成测试注入时存在：

- `form_fill.py:213` 原为 `authority = runtime.reflection_test`；
- `runtime.reflection_test` 仅在 `inference.py:553` 由 `_reflection_test` 赋值，且 `inference.py:527` 强制 `type(...) is SyntheticReview` 与 `profile.purpose == 'synthetic_test_only'`。

即：**生产路径的 authority 恒为 `None`，G1R 钩子从未执行。** 此前 49 项测试确实是用 driver 构造 `Context` 后直接调 `review()`，不构成生产入口验收——你的判断成立。

本轮接线三处（共 21 行）：

| 文件 | 改动 |
| --- | --- |
| `run.py`（`_run_task_impl`，`current_runtime()` 之后） | flag 开启且无合成注入时，装配 `runtime.reflection_authority = StructuredReflectionAuthority(model, max_iterations=1)` |
| `form_fill.py:213` | `authority = _resolve_authority(runtime)`，改为可回落生产实现 |
| `runtime_audit.py:187` | 新增 `self.reflection_authority = None` 槽位 |

链路：`run.py` 装配 → `PrivateRuntime` → `form_fill` 解析 → J0（`form_fill.py:320/410`）→ `authority.review(context)` → Producer → Medical Review → `ReviewBatch` → `resolve()` → 消费事件 → `finalize_reflection` → 最终文件。

`resolve_authority()` 语义：合成注入优先，其次生产实现，两者皆无则 `None`。默认 `CHIMERA_STRUCTURED_REFINE` 未设置时行为与历史完全一致。

## 2 已验证与未验证的边界

已验证（零生成）：

- 50 项测试：模块 25 + 桩模型集成 8 + authority 入口 11 + 仲裁 5 + 接线优先級 1
- `resolve_authority()` 优先级与旧 runtime 缺属性时的兼容
- `py_compile` 通过；`git diff` 仅 3 个既有文件被改，其余为新增

未验证：

- 上述链路**没有任何一次在真实 graph 上跑通**。测试调的是 `review()`，不是 `create_graph()` 产出的节点执行。
- `output` 包在 worktree 中缺失（未跟踪），导致 `import form_fill` 都无法完成——这本身说明宿主环境不可从版本库重建。

## 3 Medical Reviewer 实现核对

`StructuredReflectionAuthority._medical()` 走同模型独立调用，四问（事实一致性 / 必要性 / 证据支撑 / 整体一致），输出 `accept|reject|uncertain`。

拒绝路径均已覆盖：解析失败 → `reject + medical_review_unparseable`；非法取值 → `reject + medical_review_invalid_verdict`；`uncertain` → 非 accepted；异常 → `ReviewBatch('error')` → 下游保留 J0。

`method = structured_refine_independent_llm_review`，测试断言不含 "expert"。**不是 `UnavailableMedicalReviewer` 占位**，但只在桩模型上跑过。

## 4 C2 启动条件逐项核对

| 条件 | 状态 | 说明 |
| --- | --- | --- |
| 生产入口走实际运行路径 | **BLOCKED** | 已接线，未执行 |
| Structured Refine 默认关闭 | PASS | `CHIMERA_STRUCTURED_REFINE` 未设置即关闭 |
| 医学 Reviewer 已实现且失败不默认接受 | 实现 PASS / 效果 UNVERIFIED | — |
| 仲裁冻结且覆盖可追踪 | 策略 PASS / 行为 UNVERIFIED | `reflection_arbitration_v1.json` |
| 3 个合规 T3 病例 | **BLOCKED** | 仅 2 例有可复用 J0，第 3 例需新生成；病例合规性未核实 |
| J0 来源/版本/合法证据完整 | 部分 | 试点 2 例有 `judgment_pre_calibration.json`，未核证据账本 |
| 单轮配置 | PASS | manifest 已定 |
| 12 次硬上限 | PASS | 9 基础 + 1 新 J0 + 2 重试 |
| GT 不进推理与审核 | 设计 PASS / 未实证 | 代码不传 GT，未做运行时校验 |
| run_id / 调用记录 / 版本哈希 | 部分 | trace 有 `run_manifest.json`；run_id 沿用现有 runtime |
| 停止条件 | PASS | manifest 已列 |

## 5 建议的下一步（二选一，需你指定）

**路径 A：补一次宿主级零生成**
用桩模型跑真实 `create_graph` 节点链，先补齐 worktree 缺失的 `output` 包与 case fixture。能证明"接线确实被触发"，但工作量大且仍不证明真实模型行为。

**路径 B：直接以 C2 第 1 例作为首次宿主验证**
代价 3 次调用（12 次预算内），一次同时验证接线、真实补丁产出、审核与消费。若失败，暴露的是真实问题而非 fixture 问题。

我倾向 B：宿主级桩测试要重建的 fixture 本身就是在仿真，验证价值低于直接用真实病例。但这与"前置条件未全部通过不启动 C2"冲突，所以停在这里等你定。

## 6 附带发现

- `src/chimera_agent_baseline/output` 未被 git 跟踪，worktree 中不可导入，`form_fill` 因此无法 import。宿主环境无法从版本库完整重建，这是继 `tests/` 之后第二处同类缺口。
- `authority.review(context)` 只拿得到 J0 与 upfront，拿不到 ReAct transcript。Critique 审的是判断本身，不是推理轨迹。要审轨迹需给 `Context` 加字段，属契约变更，未动。

---

## 7 生产入口宿主验证（补做，已通过）

用真实 `run_agent` 路径、仅模型调用边界打桩，跑通 T3-001 单例。工具：`tools/host_probe.py`。

### 7.1 探针为什么必须打桩 prefill

`form_fill.py` 用 assistant prefill：先发一个 `{`，再 `raw = "{" + resp_text`。桩若直接返回完整 JSON 就变成 `{{...`，解析必失败。这是前三次失败的真实原因，不是接线问题。

另有两处 schema 约束：T3 的 Dynamic 模型只有 4 个字段（`event` / `months_to_recurrence` / `free_text` / `repeat_test`），`repeat_test` 默认 `None` 但 `validate_judgment` 做 canonical 全等，不显式给出就被判非法；`free_text` 最少 40 字符。

### 7.2 结果

| 路径 | 模型调用 | 反射事件 | 最终 prediction |
| --- | --- | --- | --- |
| `CHIMERA_STRUCTURED_REFINE=1` | 7 | 3 | 含新理由，旧理由消失 |
| `CHIMERA_STRUCTURED_REFINE=0` | 7 | 0 | 保留原理由 |

ON 路径的账本事件（`audit/*/events.jsonl`）：

- `reflection_review` status=`production_authority`
- `reflection_selection` status=`accepted_candidate`
- `reflection_formfill_consumed` status=`actual_assignment`，`changed_fields` 指向新理由

即 Producer → Review → Consumer → 最终落盘这条链在真实宿主上被执行过一次，不再是 WIRED / NOT EXERCISED。

### 7.3 宿主验证抓到的两个真实缺陷

**缺陷 1：`judgment_label` UnboundLocalError（致命）**
`form_fill.py:346` 在 `if strict_output:` 分支里又做了一次 `from ... import judgment_label`。函数内局部导入会让该名字在整个函数作用域内成为局部变量，`strict_output=False` 时第 503 行访问即抛 `UnboundLocalError`。

影响：**任何 `authority is not None` 且 `strict_output=False` 的运行都会崩**。因为生产路径此前从未执行过，这个 bug 一直休眠。已删除冗余局部导入（模块顶层第 52 行本就已导入）。

**缺陷 2：审计标签错误**
`form_fill.py:496` 把 `reflection_review` 的 status 硬编码成 `synthetic_stub`，生产路径也会被标成合成。已改为按 authority 类型区分 `production_authority` / `synthetic_stub`。

### 7.4 附带：宿主可复现性

worktree 缺两个未跟踪的包，导致 `import form_fill` 直接失败：

- `src/chimera_agent_baseline/output/`：`__init__.py` + `schema.py`（提交契约，无患者数据）
- `src/chimera_agent_baseline/models/`：`__init__.py` / `openai_compat.py` / `request_meter.py` / `vllm_offline.py`（模型加载与计量，无权重）

已选择性纳入 `refl-g0b`，不触及 `data/`、`resources/`、历史运行产物。`data/` 仍不入库，探针通过 `HOST_REPO` 指向主仓读取。

## 8 C2 预算需要重算（原 12 次上限不成立）

探针在桩模型下即为单例 7 次调用：

| 环节 | 次数 |
| --- | --- |
| plan | 1 |
| ReAct agent | 1（桩模型一次收敛；真实模型需多轮工具调用） |
| form_fill（prefill J0） | 1 |
| Critique | 1 |
| Structured Refine | 1 |
| Medical Review | 1 |
| fact_check / 其他后处理 | 1 |
| **合计** | **7** |

其中反射固定占 3 次，宿主基线 4 次起。真实模型下 ReAct 轮次增加，`cfg.agent.max_model_turns` 上限更高。

结论：**12 次硬上限最多支撑 1 例真实病例（7–10 次），撑不到 3 例。** 原 manifest 的「3 例 × 3 次 = 9」建立在"初始状态可复用且 Reflection 单独计"的假设上，未计入宿主基线调用，属低估。

建议改法（未执行，需批准）：

| 方案 | 例数 | 预估调用 | 说明 |
| --- | --- | --- | --- |
| C2a 单例真实 | 1 | 7–10 | 唯一能在 12 次内完成的方案 |
| C2b 两例真实 | 2 | 14–20 | 超上限，需追加授权 |
| C2c 三例 | 3 | 21–30 | 原计划，需追加约 20 次 |

## 9 更新后的状态

`INTEGRATION_PASS / REAL_VALIDATION_PENDING`

生产入口已在真实宿主上执行并通过，OFF/ON 双路径均符合预期。C2 仍为 `BLOCKED`，原因已从"接线未验证"变为"预算上限与实际调用链不符"。
