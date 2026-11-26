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

---

## 10 C2a 执行结果：BLOCKED（准入权限）

按批准范围执行 C2a（单例 T3-001，真实 35B，硬上限 12 次）。**未产生任何宿主内模型调用**，在第 0 步被准入检查拦下。

### 10.1 环境就绪情况

| 项 | 状态 |
| --- | --- |
| 真实端点 `127.0.0.1:8765` | 存活，模型 `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf` |
| 进程 | PID 3287478，`llama-server`，双 3090 共约 23 GB |
| 直连可用性 | 已验证（一次 "reply with OK" 探测返回 OK） |
| 病例输入 `data/task3/agent_input/T3-001` | 4 个文件齐备 |
| 宿主路径 | 探针已验证可跑通（OFF/ON 双路径） |

### 10.2 阻塞点

`src/chimera_agent_baseline/models/__init__.py:37-39`：

```python
# G1A only approves synthetic test profiles; tests supply fake models.
# No production model initialization/download or network is authorized.
raise RuntimeFatal("Synthetic entry approval cannot load a real model")
```

真实模型加载只有两条被批准的入口：

1. **Case Pilot**（`case_pilot.py`）：需要 `/pilot/input`、`/pilot/sockets`、`/pilot/control` 挂载、`host_namespaces.json` 与 `/pilot/backend/count.sock` 计数套接字。**本机无 `/pilot` 目录**，这套准入属于容器化试点部署。
2. **Host Smoke**（`host_smoke.py`）：只批准一个固定夹具，`NN = {1:'F01', 2:'F05', 3:'F06'}`，且 `inference.py:506` 要求 `adaptive` workflow + v2 profile 才能启用 —— 那是 Adaptive Plan 的实验通道，不属于本轮 Reflection。

没有 env 级旁路（`grep CHIMERA_ALLOW|ALLOW_REAL|real_model` 无结果）。

### 10.3 预算账

| 项 | 次数 |
| --- | --- |
| 宿主内模型调用 | **0** |
| 端点连通性探测（直连，非宿主） | **1** |
| 剩余可用 | 11（若探测计入 12 次上限） |

探测调用发生在准入拦截之前，用于确认端点存活，不产出任何病例数据。如实计入。

### 10.4 三个可选处置（均需你决定）

| 方案 | 做法 | 代价 | 风险 |
| --- | --- | --- | --- |
| A 进试点容器跑 | 在带 `/pilot` 挂载的部署里执行同一脚本 | 部署成本 | 最合规，但脱离当前服务器 |
| B 授权一个受限真实模型准入 | 新增 case-scoped approval：仅 loopback、仅指定 case、`request_meter` 计量、硬上限 12 | 需改 `models/__init__.py` 一处门禁 | 等于放宽安全门，必须由你显式批准并记录 |
| C 复用 Host Smoke 的 F06 夹具 | 用 `HostSmokeApproval` 直接调用 `run_agent`，绕过 `inference.py` 的 adaptive 前置 | 低 | 属于把别的实验通道挪用于 Reflection，越界 |

我倾向 B，但**不会自行改动这条门禁**。B 的最小改法是在 `load_model` 里增加一个显式 approval 分支，其校验条件为：`loopback_only`、`case_id` 在白名单、`request_meter` 已挂、上限 12 次；这样就把"放宽"变成"可审计的新准入"，而不是把门打开。

### 10.5 判定

C2a = `BLOCKED_ADMISSION`。工程侧（G0）结论不变：`INTEGRATION_PASS / REAL_VALIDATION_PENDING`，宿主 ON/OFF 已通过桩模型验证，真实模型行为仍未知。

---

## 11 C2a 第二次尝试：BLOCKED_PROTOCOL

新增 `ReflectionPilotApproval` 后已越过准入门（8 项正负对照通过），真实模型调用成功发起，但在第二个请求被计量层拒绝。

### 11.1 已打通的部分

| 环节 | 结果 |
| --- | --- |
| 受限准入（病例/模型/端点/摘要/计量器） | 通过，`meter_openai` 生效 |
| SDK 零重试 | 已强制，一次 invoke = 一次 HTTP POST = 一次额度 |
| HTTP 边界计量 | 生效（`runtime.wire` 的 `attempts >= max_attempts` 即硬上限） |
| embedding 服务 | 补齐（原只在 `run.py:749` 的 main 里启动，直接调 `run_agent` 会漏） |
| 真实 35B 首请求 | 成功，模型返回带推理的 plan 文本 |

### 11.2 阻塞点：消息链不符合 OpenAI 协议

`request_meter.py:129-134` 校验：每个 `tool` 消息的 `tool_call_id` 必须对应前一条 assistant 的 `tool_calls`。

实测该用例：

- 首次响应 `finish_reason=stop`，`tool_calls=None`，内容是 "Plan: 1. Retrieve surgical pathology report…"
- 但 LangGraph 之后仍路由到 `tools`，第二个请求携带了 tool 结果
- 第二个请求因此被判 `Unmatched OpenAI tool result ID`（`request_rejected`，reason=`unverified_sdk_request`）

即：**assistant 没有声明 tool_calls，却出现了 tool 结果**。这是协议层的非法消息链。llama.cpp 对这种形态宽容，所以未计量的生产运行不会暴露；一旦走计量层就被拦。

配置为 `workflow: planned_react`，plan 节点"规划 + 预执行"，工具由节点侧执行而非模型 tool_calls 声明，是这条链的结构性成因。

### 11.3 预算账（累计）

| 项 | 次数 |
| --- | --- |
| 端点探测 | 1 |
| 第一次运行（14:03:36） | 1 |
| 第二次运行（14:05:35） | 1 |
| **累计已用** | **3 / 12** |
| 剩余 | **9** |

（此前几次运行在准入/计量/embedding 处失败，未产生模型调用。脚本内 meter 每轮从 PREUSED 重置，已记下需改为累计传入。）

### 11.4 两条可选处置

| 方案 | 做法 | 是否改变实验变量 | 成本 |
| --- | --- | --- | --- |
| A 改用 `workflow: react` | 模型自己发 tool_calls，消息链天然合规 | **是**，换了宿主工作流 | 单例约 5–9 次 |
| B 修 `planned_react` 的消息链 | 让 plan 预执行的工具结果以合法 assistant.tool_calls 形式回填 | 否（工程修复） | 需改 graph，风险高于本轮范围 |

我倾向 A 做一次，把"Reflection 在真实模型下能否工作"这件事先回答掉，并明确标注它与 `planned_react` 基线不可直接比较；B 留作后续工程债。

但 A 违反你冻结的"不引入其他实验变量"，所以停在这里等你定。

### 11.5 判定

C2a = `BLOCKED_PROTOCOL`。已用 3/12 次真实调用，剩余 9。

---

## 12 C2a 第三次尝试：BLOCKED_PROTOCOL（已定位到具体消息）

按 A 方案切到 `workflow: react`，协议预检通过，但真实运行在第二个请求被拒。**已用 8/12，剩 4。**

### 12.1 Direct ReAct 本身是通的

模型行为正常：`finish_reason=tool_calls`，一次发出两个带 id 的调用（`get_surgical_pathology_report`、`get_previous_notes`），参数为 `{"case_id":"T3-001"}`。工具也确实返回了真实病理（Gleason 4+3 / pT4b）。

协议预检（零生成）：

| 形态 | 结果 |
| --- | --- |
| assistant(tool_calls) + tool + assistant | **accepted** |
| assistant(无 tool_calls) + tool（planned_react 形态） | rejected: Unmatched OpenAI tool result ID |

### 12.2 真正的违例：tool 消息 content 是列表

`request_meter.py:125-128` 要求 `content` 为 `str`（assistant 且带 tool_calls 时可为 `None`）。实测：

```
3 tool  content_type=list  tool_call_id=sK7T2...
   [{'type': 'text', 'text': '{"case_id": "T3-001", "surgical_pathology_report": "..."}'}]
4 tool  content_type=list  tool_call_id=MPOh9...
```

MCP 工具返回的是 `[{"type":"text","text":...}]` 块，LangChain 原样保留，计量层不接受。

这是**第三条**宿主消息链与 OpenAI 协议不一致之处（前两条：planned_react 的 tool 结果无声明；本条 tool content 为块数组）。三条都在宿主消息构造侧，不在 Reflection 侧。

### 12.3 预算已不足以完成一例

| 项 | 次数 |
| --- | --- |
| 真实调用已用 | **8 / 12** |
| 剩余 | **4** |
| 一例完整链路估算 | ReAct 2 + Form Fill 1 + Critique 1 + Refine 1 + Medical 1 = **6** |

即使立刻修好 content 归一化，剩余 4 次也跑不完整条链。继续试只会把预算耗在同一个协议问题上。

### 12.4 另发现一个隐蔽的复现性缺陷

`site-packages/_editable_impl_chimera_agent_baseline.pth` 把 **主仓** `/home/hedongze/CHIMERA-agent/src` 挂进 `sys.path`，而 worktree 靠脚本内 `sys.path.insert(0, ...)` 抢先。实测两种解析会混用：一次 traceback 的 `rag.py` 来自主仓，`models/__init__.py` 来自 worktree。

后果：前几轮真实运行里有部分模块跑的是**没有本次改动的旧代码**，浪费了调用额度，也让失败原因难以判断。

修正方式已验证：运行时显式 `PYTHONPATH=<worktree>/src`，包解析稳定落在 worktree。

### 12.5 建议（需你定）

| 方案 | 内容 | 预算影响 |
| --- | --- | --- |
| A 追加额度 | 修 content 归一化（工具结果转字符串，约 5 行，不动 request_meter 校验），再追加 6–8 次 | 上限提到 18–20 |
| B 先修再报 | 只做归一化与零生成回归，不跑真实病例，把 C2a 推到下一轮 | 0 |

我倾向 A，但必须先说明：修完仍可能暴露第四条协议不一致。三条已发现的违例形态不同（tool_calls 声明、content 类型、可能还有 metadata），说明宿主消息链从未在严格协议下验证过，逐条试探的成本不可预计。

**另一条更省的思路**：把 tool content 的归一化做成序列化前的统一入口（一次处理掉 content 块、缺 id、多余 metadata 三类），而不是遇到一条修一条。这样只花一次额度就能知道还剩不剩第四条。

### 12.6 判定

C2a = `BLOCKED_PROTOCOL_BUDGET`。8/12 已用，剩 4。

---

## 13 B 轮：统一归一化 + 传输层零生成回归（本轮无真实调用）

真实 35B 调用：**0 次**。预算账本保持 `used=8`，未动。

### 13.1 归一化落在哪一层

新增 `src/chimera_agent_baseline/message_norm.py`，挂载点是 `MeteredEndpoint._call/_acall` 进入 `boundary.submit()` **之前**：

```
LangGraph 消息对象 → 归一化 → request_meter 协议校验 → 计量 → 实际 HTTP body
```

因此校验与计量针对的是**归一化后真正要发出的 body**，而不是上游草稿。`request_meter` 的校验规则一字未改。

只做表示格式转换：文本块数组 → 字符串（多块按原顺序以换行拼接，保留边界），工具调用 id 原样保留，`assistant.content=None + tool_calls` 原样保留。

以下情形一律显式报错，不代 Agent 补全：

| 情形 | 行为 |
| --- | --- |
| tool 结果缺 `tool_call_id` | `RuntimeFatal` |
| tool 结果找不到对应 `assistant.tool_calls` | `RuntimeFatal` |
| `assistant.tool_calls` 里某条缺 `id` | `RuntimeFatal` |
| 非文本内容块（image/audio 等） | `RuntimeFatal`，不静默丢弃 |
| 未知消息键 | `RuntimeFatal`，不静默剥离 |
| 消息顺序错（声明后未跟结果） | `RuntimeFatal` |

12 项正负对照全通过（`tests/test_message_norm.py`）。

### 13.2 传输层桩：真实客户端 + 真实计量，只伪造 HTTP 响应

`tools/transport_probe.py` 不替换 `model.invoke` 也不替换 `load_model`，而是把 `httpx.MockTransport` 装进真实 `ChatOpenAI` 的底层 client，再交给真实的 `meter_openai` 包装：

- 真实：LangChain 消息对象、OpenAI SDK 序列化、统一归一化、`request_meter` 协议校验、`runtime.wire` 计量、Reflection authority、Producer/Consumer、Calibration、最终落盘
- 伪造：只有 35B 的响应体

实测一次完整用例（T3-001，`workflow: react`）：

| 项 | 值 |
| --- | --- |
| SDK 调用 / transport 响应 | 7 / 7 |
| `runtime.attempts`（计量层计数） | 7 |
| 请求序列 | `agent_tool_calls` → `agent_final` → `form_fill_prefill` → `critique` → `refine` → `medical` → `agent_tool_calls` |
| prediction 生成 | 是 |
| prediction 含新理由 | **是**（旧理由已消失） |
| authority | `StructuredReflectionAuthority` |

即：**七个真实序列化的 HTTP 请求全部通过严格协议校验**，且 Reflection 的补丁在计量模式下被真实消费进最终输出。

`agent_tool_calls` 出现两次，是因为第一次双工具调用后模型还有一次取数回合；不影响结论。

### 13.3 包副本混用：加 fail-fast 守卫

`src/chimera_agent_baseline/run_guard.py` 在发出第一个请求前检查：

- 解释器路径、实际 `sys.path` 顺序落盘
- 11 个关键模块的 `__file__` 必须全部位于批准根内，否则列出外来源并 `RuntimeFatal`
- 关键源码 SHA256 可与冻结清单比对
- 配置摘要与准入绑定比对

正负对照：批准根收窄到 `src/chimera_agent_baseline/agent` 时正确报 `modules loaded from outside approved root`；正常根下通过并产出 `module_origin.json`。

未修改其他会话使用的共享 conda 环境，只靠运行时 `PYTHONPATH` + 守卫。

### 13.4 异常处理

`RuntimeFatal` 继承自 `BaseException`（`runtime_audit.py:89`），不能靠 `except Exception` 捕获，也不应裸捕 `BaseException`（会吞掉中断）。两处已改为显式 `except RuntimeFatal:` / `except Exception:` 并列，失败时仍能落盘实际 payload。

### 13.5 预算现状

| 项 | 值 |
| --- | --- |
| 累计真实调用 | 8 / 12 |
| 剩余 | 4（冻结，本轮未用） |
| 账本 | `output/c2_budget_ledger.json`，`used=8` |
| 传输层回归 | 使用独立 runtime（`max_attempts=40`），不写正式账本 |

### 13.6 下一步的预算估算

按本轮实测的 7 次请求序列，真实单例（`workflow: react`）预计：

| 环节 | 次数 |
| --- | --- |
| ReAct 取数回合 | 2 |
| Form Fill（J0） | 1 |
| Critique | 1 |
| Structured Refine | 1 |
| Medical Review | 1 |
| 余量（重试/额外回合） | 1–2 |
| **合计** | **7–8** |

建议把累计上限提到 20，为这一次留出 12 次余量（已用 8 + 12 = 20）。本轮不追加、不启动。

---

## 14 C2a 真实单例运行结果（上限 20，已用 14）

真实 35B 调用：本次 6 次，累计 **14 / 20**，剩余 6。**Reflection 环节仍未进入。**

### 14.1 走到了哪一步

| 阶段 | 结果 |
| --- | --- |
| 环境守卫 | 通过（模块来源、哈希、配置摘要一致） |
| 真实 ReAct 取数 | 成功，模型发出带 id 的工具调用并拿到真实记录 |
| Form Fill（J0） | **三次尝试全部被 canonical 校验拒绝** |
| Critique / Refine / Medical | 未执行 |
| 最终输出 | 未生成 |

### 14.2 三次 J0 的原始输出（未做任何清洗）

| 次数 | event | months | 关键内容 |
| --- | --- | --- | --- |
| 1 | 1 | `12`（整数） | "recurrence within 12 months…positive margins or extraprostatic extension" |
| 2 | 0 | `60`（整数） | "negative margins and no extraprostatic extension…post-operative PSA undetectable" |
| 3 | 0 | `60`（整数） | "has not yet undergone definitive treatment…recurrence not yet applicable" |

失败原因两类：

1. **重复前导大括号**：`raw` 以 `{{` 开头（`form_fill.py:283-284` 无条件 `raw = "{" + resp_text`，真模型把 prefill 的 `{` 又输出了一遍）→ JSON 解析失败。
2. **整数未按 float 输出**：`months_to_recurrence` 给出 `12` / `60`，T3 schema 声明 `float`，`validate_judgment` 做 canonical 全等，pydantic 的 int→float 隐式转换被判为 coerced 字段 → 拒绝。

### 14.3 顺带发现的模型行为问题（对研究更关键）

同一病例三次尝试的**临床结论互相矛盾**：一次说 12 个月内复发，两次说无复发、随访 60 个月。第 2 次还断言"切缘阴性、无包膜外侵犯、术后 PSA 不可测"，第 3 次则说"尚未接受根治性治疗"。

这说明 35B 在这个病例上的 J0 **不稳定，且对已取回的事实给出不一致陈述**。即使 schema 问题解决，J0 本身的可信度也需要单独评估——这是 Reflection 之外的问题，但会直接影响 G2 的配对基线。

（受剩余预算限制，未能逐条核对这三次陈述与工具返回记录的对应关系，暂不断言哪一次是虚构。）

### 14.4 两个可修与不可修的区分

| 问题 | 性质 | 处理 | 是否改实验变量 |
| --- | --- | --- | --- |
| `{{` 重复前导括号 | 确定性工程缺陷 | 一行修复（前缀已存在则不重复添加） | 否 |
| `months` 整数 | 模型输出不合规 | 需 prompt 约束，或放宽 canonical 对 int→float 的判定 | **是**（prompt 变更）或**放宽校验**，均需批准 |

### 14.5 停止与预算

按预定止损规则：**不再用剩余 6 次做探索性重跑**。Reflection 一次都没执行，任何关于其医学能力的结论都不成立。

账本：`output/c2_budget_ledger.json` = `{"used": 14, "limit": 20}`，跨进程不可重置。

### 14.6 建议

1. 先修 `{{`（零成本、无变量变更），否则任何真实用例都会在 Form Fill 第一步失败。
2. `months` 整数问题二选一：在 `_SYSTEM_PROMPT` 里明确"months 必须是带小数点的浮点"，或为 T3 的 `months_to_recurrence` 增加显式的 int→float 标注容差。前者改 prompt，后者改校验强度，都需要你批准。
3. 上述任一项未解决前，不要再花真实额度——这两条都能用零生成复现（把模型输出喂给 form_fill 的解析函数即可）。

---

## 15 收尾：两项修复、离线回放与事实审计（全部零生成）

真实调用：**0 次**，预算仍为 14/20，剩 6 冻结。

### 15.1 修复一：重复前导大括号

`agent/form_fill.py` 原为无条件 `raw = "{" + resp_text`。改为兼容两种合法形态：

```python
_body = resp_text.lstrip()
raw = _body if _body.startswith("{") else "{" + resp_text
```

完整 JSON 与 prefill 续写都能解析；其他形态仍原样交给解析层失败，**未引入通用 JSON 修复器**。

### 15.2 修复二：字段级无损数值归一化

新增 `src/chimera_agent_baseline/judgment_norm.py`，只在 task 3 的 `months_to_recurrence` 上把 int 字面量改成 float，并写 `formfill_numeric_normalised` 事件留痕。`reflection_contract` 的 canonical 全等校验一字未改。

| 输入 | 处理 |
| --- | --- |
| `12` / `60` | → `12.0` / `60.0`，记录 `int_to_float_lossless` |
| `12.0` / `12.5` | 原样保留 |
| `"12"` / `true` / `null` / 负数 / NaN / inf | **不归一化，交给下游拒绝** |

### 15.3 三份真实输出的离线回放

| 文件 | 模型原文首字符 | 归一化记录 | Schema |
| --- | --- | --- | --- |
| attempt1 | `{` | `12 → 12.0` | **PASS** event=1 months=12.0 |
| attempt2 | `{` | `60 → 60.0` | **PASS** event=0 months=60.0 |
| attempt3 | `{` | `60 → 60.0` | **PASS** event=0 months=60.0 |

两处修复之后三份全部通过，**没有第四种拒绝原因**。真实模型确实把 prefill 的 `{` 又输出了一遍，且确实给整数 months。

### 15.4 离线事实审计（依据本次已存记录）

检索到的真实病理（`get_surgical_pathology_report` 原文）：

> robot-assisted radical prostatectomy specimen … Gleason 4+3 (ISUP GG3), **pT4b**. **Extraprostatic extension present**; **surgical margins positive**; seminal vesicles invaded; lymphovascular invasion absent; **lymph node metastasis present**.

ReAct 推理阶段的结论与此一致，并明确指出"我没有术后 PSA 值"。

| 核查项 | 记录 | J0-1 | J0-2 | J0-3 |
| --- | --- | --- | --- | --- |
| 是否接受根治术 | 是（RP 标本） | 是 ✓ | 是 ✓ | **否 ✗ 矛盾** |
| 手术切缘 | 阳性 | 阳性 ✓ | **阴性 ✗ 矛盾** | 未提 |
| 包膜外侵犯 | 有 | 有 ✓ | **无 ✗ 矛盾** | 未提 |
| 术后 PSA | 无此数据 | 未声称 ✓ | **"不可测" ✗ 无依据** | 未声称 ✓ |
| event | — | 1 | 0 | 0 |
| months | — | 12 | 60 | 60 |

三类变化必须分开看：

- **医学决策不稳定**：event 1 → 0（12 个月复发 → 无复发）
- **患者事实错误**：J0-2 的"切缘阴性/无包膜外侵犯"、J0-3 的"尚未接受根治性治疗"，都与已取回记录直接矛盾
- **无依据陈述**：J0-2 的"术后 PSA 不可测"，记录中不存在该数据（ReAct 阶段自己也承认没有）

### 15.5 Form Fill 是否在重新做决策：证据支持"是"

三次输入对比：

| 次数 | 输入长度 | 输入 sha | 与前次相同 |
| --- | --- | --- | --- |
| 1 | 2620 | cea72d81 | — |
| 2 | 2842 | ed20ad6b | 否（追加了格式重试提示） |
| 3 | 2842 | ed20ad6b | **完全相同** |

两条关键推论：

1. 第 2、3 次**输入逐字相同**，输出结论也相同（event=0/60），但措辞不同 → 这部分差异属采样随机性。
2. 第 1 次与第 2 次的唯一差别是**格式重试提示**（"Judgment is incomplete or has coerced/unknown fields…"），而结论从 event=1 变成 event=0，并新增了与记录矛盾的事实陈述。

也就是说：**一次纯格式层面的失败重试，把医学判断从"复发"改写成了"无复发"，并引入了与记录矛盾的临床事实。** 这不是单纯的采样噪声。

结论：Form Fill 在本例中并非忠实转换既有判断，而是会重新解释证据、甚至重新作出判断；格式重试会显著放大这一效应。

（限定：单例、单模型、单病例；不能外推为普遍规律，但足以否定"Form Fill 只是格式化"的假设。）

### 15.6 状态登记

| 项 | 状态 |
| --- | --- |
| C2a | `C2A_BLOCKED_INVALID_J0` |
| Reflection | `INTEGRATION_PASS / REAL_VALIDATION_PENDING` |
| 真实 Critique / Refine / Medical Review | 均未执行 |
| Reflection 医学有效性 | **既不能判定有效，也不能判定无效** |

### 15.7 对 G2 的影响与建议

1. 共享 J0 的必要性被进一步证实，但**共享 J0 不足以保证两臂可比**——若 J0 本身在重试中会漂移，必须先固定"一次采样、不回退重试"的约定。
2. 后续若重启，建议先用合法历史 J0 做**模块级** Reflection 实验（只跑 Critique/Refine/Reviewer），先把 Reflection 的独立纠错能力测出来，而不是每次重付 ReAct + Form Fill 的成本。该实验不能冒充端到端验证，需单独授权。
3. "从证据获取到结构化输出的全过程一致性"是本次暴露出的、可能比 Reflection 本身更值得研究的问题：模型可在格式重试中改写既有事实。

### 15.8 结项

本轮 Reflection 工程建设到此结束：不追加预算、不重跑真实用例、不启动 G1/G2。剩余 6 次额度冻结。
