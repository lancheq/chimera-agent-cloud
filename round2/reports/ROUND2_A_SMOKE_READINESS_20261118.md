# A-SMOKE · Adaptive 官网冒烟前置检查

2026-11-18，北京时间。**前置检查已交付。Adaptive 宿主 G2 当前阻断。** 三个正式任务仍经 `run_agent → create_graph` 使用 `planned_react`。本机两个登记的 35B 端口均未监听。完成了三个任务的实际配置加载探针、历史证据和资源核查。真实生成、合成模型调用、新 MCP 调用、非生成 HTTP 探针、GPU 作业、镜像和平台账号操作均为 0。

基线 HEAD `7301642d0eedda2f143ba44b93d42f948249d813`，使用含未提交修改的本地工作树。HEAD、暂存区和本次索引的源码、配置、回执校验一致。A-SMOKE 任务卡、本次配置探针、源码实际指纹、历史合成回执及案头检查时间见交接和待审 profile 与证据矩阵。

## 正式入口和冻结控制器

1. `inference.py:109–135,379–472` 从 `/input/inputs.json` 认临床 Socket，物化官方输入，临时输出树按 `gc-case` 组织。配置从 `/opt/app/configs/config.yaml` 加载，入口写入单任务与数据、输出、资源、模型和 embedding 路径。若提供 `CHIMERA_BASE_URL`，它最后覆盖 YAML 端点地址。入口随后核对可信 profile 的文件、有效配置、环境和已加载开关。当前 `entry_profile.py:34` 的生产白名单为空；没有实际批准的 Adaptive profile 或全链精确计数。正式入口不经 Hydra CLI 的 experiment 合并。后续旧分支仍读取导入时或调用时的开关，例如 T1 决策层、权重表、旧内层 self-refine、成本对齐和 predictor 路由，冻结时一并登记。
2. 使用已验收的 conda `chimera` 环境实际调用 `_detect_task`、`_load_config` 和 `verify_profile`。T1/T2/T3 均得 singleton task、`planned_react`、guard=false、predictor=true、atomic=true；`max_model_turns=null`、图递归限 25。测试中临时替换配置路径读取仓库 YAML，人工端点值只经过 loader，未联网。默认 provider 是 OpenAI-compatible，模型为 Qwen3.6-35B-A3B Q4_K_M，端点 `127.0.0.1:8765/v1`。项目自带 `.venv` 缺 `omegaconf`，首次探针失败并保留日志，随后使用既有 conda 环境通过。
3. `run.py:416–434,470–519` 将 `workflow` 传给 `create_graph`。默认在病例循环前构建图，guarded 模式完成病例级权限绑定后仍在同一个 builder 构建。两处都没有到 `shared_plan` 的分派。`agent/graph.py:385–` 的 planned_react 先生成工具列表并执行规划工具，包含旧工具补选、任务约束和回退；后续模型可再选工具或结束 agent 回合，路由到 Reflection、predictor/填表和输出。显式 `react` 省去独立初始 plan。两种图都没有 S4 的 `PolicyPlan.remaining_plan` 替换和空计划终止合同；有后续工具调用只能证明 ReAct 行为。
4. 冻结 S4 v2-r1 源码、研究入口和配置共四项现场指纹均匹配协议，两个 worker 的运行回执单独登记。`tools/run_shared_plan_candidate.py` 走 base + v2 overlay、`run_pair → run_arm`；`shared_plan.py:257,339–419,432–519` 走冻结的初始计划、首观察绑定、带 `PolicyPlan` 的改订、空清单 STOP 及取证终止原因，然后诊断和共用填表。冻结每组 3 信息单位、6 工具请求/获准尝试、5 个策略回合、2 次控制格式补救、2 次诊断尝试；控制界面 v2，SDK 重试 0；研究设定显式排除 predictor、旧反思和 atomic。历史 T1 两组各 64/91，Adaptive 少取 10.33% 信息单位、分支 token 约 2.22 倍。只证明已冻结研究入口的行为和成本，当前三任务正式入口仍待接线。 [S4 v2-r1 执行结果](ROUND2_S4_V2_RESULTS_20261115.md)

**首个接线缺口**在正式的病例级权限及知识准备完成后、`run.py` guarded `create_graph` 调用前。最小方案是在规划冻结的可信 per-task profile 下，分派到复用冻结 `load_or_generate_plan/acquire` 控制语义的单例路径，沿用正式病例账本、批准的知识来源、现有决策/填表消费者及 `inference.py` 最终输出。无需调用 `run_pair` 或双分支。只在 G1T 的最终 SDK 请求设权威全链预算和计量，保留 S4 控制事件及旧预算语义，避免用旧的 pre-SDK 近似计数作最终守卫或给同一 HTTP 重复收费。先在根入口合成证明初始计划绑定、观察后替换、空计划停止、拒绝行为、出处和双文件，再交规划决定是否准入真实 G2。此轮只提出接点。

## T1、T2、T3 待审 profile

当前证据分两级。配置行为已在无患者探针运行。下表历史 B 回执使用 **正式入口 + 注入式 guarded ReAct**，T1/T2 的 MCP 为真实 stdio 合成子进程，HTTP 为 SDK MockTransport，填表响应预设。T3 有正常跳过记录。旧证据的六个最终文件在本轮逐个核对 SHA-256。 [B 规划验收](ROUND2_B_EVIDENCE_RECONCILIATION_PLANNING_REVIEW_20261118.md) · [B-MCP-SDK 规划验收](ROUND2_B_MCP_SDK_PLANNING_REVIEW_20261118.md)

| 任务 | 入口与两路正式输出 | 已有入口证据 | 待审专项 |
|---|---|---|---|
| T1 | `prostate-biopsy-decision-clinical-data`；`prostate-biopsy-decision.json` 为 yes/no；同名 `-reasoning.json` 为理由、置信、权重、实际披露顺序 | 1 次 tool、1 信息单位、3 次 SDK/模拟 HTTP；B 消费、填表和两文件 | T1 专属 decision v2 能改 `biopsy_decision`；few-shot/灰区取证与 predictor 权限确认；C02 T1 有限定载体验收，缺正式 Adaptive 消费 |
| T2 | `prostate-treatment-decision-clinical-data`；`prostate-treatment-decision.json` 为 primary 四值；同名 `-reasoning.json` 同类结构 | 1 次 tool、1 信息单位、3 次 SDK/模拟 HTTP；B 消费、填表和两文件 | predictor 覆写 primary、知识适用域和 C02 逐任务部署待裁；B 正式截止和迟到视图待实现 |
| T3 | `prostate-time-to-recurrence-or-last-follow-up-clin`；`prostate-time-to-recurrence-or-last-follow-up.json` 为 event/months；同名 `-reasoning.json` 为 free_text 字符串 | 0 次 tool、0 信息单位、2 次 SDK/模拟 HTTP；B 正常跳过、填表和两文件 | predictor、事件/月数校准和理由补写分别定权威；正式无可信时序时 B 显式跳过；不得以结局或其他任务工具作为输入 |

共用输入为 `structured-prompt`、本任务临床 Socket 及按权限开启的 neural representations。T2 primary 的四个枚举为 `active_surveillance`、`active_treatment`、`continued_surveillance`、`watchful_waiting`。T1/T2 的 `inference._reasoning_value` 经 `reasoning_align` 对齐 confidence、权重，再投影允许的字段和披露段。T3 分开确认 `agent/form_fill.py` 中 event/months/free_text 的来源。当前 G0 分项继续按规划冻结：

| Gate | 状态与最小解除条件 |
|---|---|
| D01 Adaptive | **阻断**。批准可信 profile 及单例接线，明确 T2/T3 工具集合、观察后计划语义和表单契约。 |
| D02 Reflection / 核验 | **部分合成通过，生产阻断**。G1R 已验证受信合成结构在实际填表赋值及最终字段回执。外层 Reflection 缺完整结构提议、可认证来源和绑定本次草稿/初始判断的审核；旧内层 self-refine 仍需写回授权，atomic 另需来源、触发和成本合同。默认图配置的 atomic=true 不能代替审批。 [G1R 验收](ROUND2_P01_G1R_PLANNING_REVIEW_20261118.md) |
| D03 患者权限 | **阻断**。默认 predictor=true、通用覆写覆盖 T2/T3，T1 决策层另管。guard 当前拒绝 predictor 启用或覆盖；影像工具和 predictor 配置有关，旧 T1 few-shot 另有取材路径。按任务决定各项实际输入、获得条件与允许写回，验收权威权限和拒绝。 |
| D04 C02 / B | **部分合成通过，正式阻断**。按任务冻结 C02/RAG 来源、正常未知和合法回退。B 的回执、未裁冲突与单文本 SDK 有 T1/T2 合成证据。B-TIME 另确认真实合格覆盖未知；需认证任务/病例/快照的时间截止并隔离初始和工具的迟到原文。现有 B 仅过滤注记，不代表原文已隔离。T3 缺来源时显式跳过。 [C02 验收](ROUND2_C02_PLANNING_ACCEPTANCE_20261117.md) · [B-TIME 验收](ROUND2_B_TIME_PLANNING_REVIEW_20261118.md) |
| D05 最终字段 | **部分合成通过，权威待裁**。为填表、外部核验、旧 predictor、决策层、校准及正式 Socket 建逐字段允许改写顺序。G1R 已记录部分覆写及失败状态；JSON 成功落盘的情况也须检查最终消费合同。 |
| D06 成本 | **SDK 合成通过，真实计数阻断**。所有模型发送尝试按最终 JSON 和活跃服务模板计入。患者信息单位、受理/拒绝工具请求、官方工具分、GPU 墙时分列。 [G1T SDK 报告](ROUND2_P01_G1T_SDK_REQUEST_BOUNDARY_20261118.md) |
| D07 审计 | **合成通过，正式环境待验**。保存绑定的配置、权限、模型请求、消费者、最终文件及失败记录，审计须越过临时目录清理并实测 non-root 可写。 |

待审 profile 目前是清单，运行配置没有授权值变更。G0、G1A、G1R、G1T、P01-PACK、GIT、C02 的合格部分沿用，各自证据等级保留。 [G0 机制清单](ROUND2_P01_MECHANISM_COMPLETENESS_20261117.md) · [G0 规划验收](ROUND2_P01_G0_PLANNING_REVIEW_20261117.md) · [G1A/A02 规划验收](ROUND2_G1A_A02_PLANNING_REVIEW_20261118.md)

## 35B、离线镜像与最低资源动作

- **35B 服务阻断**。北京时间 22:02:32，8765/8766 无监听，`llama-server` 无进程。宿主候选 `llama-build/bin/llama-server` 重新哈希并匹配 G1T 原记录，短标识 `5e8d6905…`。本地 GGUF 文件为 22,134,528,992 字节，模型历史完整 SHA 及模板摘要见矩阵，当前只读 stat 及回查历史身份。历史服务 `b0-unknown`、每 slot 16384、模板 `55d49314…` 保留为已测基准，当前进程、有效模板和实际版本均未识别。
- **计数未运行**。本机源码已有 `/v1/chat/completions/input_tokens` 与 chat 共用的解析证据，文本走 `add_special=true, parse_special=true`；旧 `/tokenize` 两步计数使用 false/true。模板字节、实际 slot、工具格式、表单 assistant JSON 前缀、原生 schema/grammar 暂无 live 回执。表单当前还存在普通 JSON 提示和解析补救；控制器强制 JSON schema 属另一请求形状。服务恢复后用最终 SDK JSON 做最多 8 次单并发非生成对照。恢复动作须另经批准，优先复用已能认证的现有实例。 [G1T 模板报告](ROUND2_P01_G1T_EXACT_TOOL_CONTEXT_20261118.md) · 宿主资源快照
- **资源和模型启动**。2×RTX 3090，各 24 GiB；采样可用 24,246/1,891 MiB；驱动 535.309.01。40 逻辑 CPU，62 GiB 主机内存约 47 GiB 可用，磁盘 133 GiB 可用。候选 GGUF 约 20.62 GiB，另留 KV/compute，需用一个空闲设备实测，另一块 GPU 仍有占用。恢复动作拟为已知二进制+模型指纹、单 GPU、固定 slot、限制 CPU/并发，记录启动／稳态显存和主机内存峰值。既有磁盘空间也须按实际镜像层与模型归档测算。没有冷启动、实际计算价格或设备配额实测。
- **镜像未构建**。旧报告指出的 `reasoning_align.py` COPY 现在已有，当前 `ENTRYPOINT ["python3","inference.py"]` 仍未建立启动 35B 的合同；legacy embedding 服务单独负责向量化。宿主当前 PATH 无 Docker/Podman/Apptainer/Singularity，平台挂载路径在宿主也不存在。只在获批 profile、启动与资源合同齐备后做一次经固定依赖的离线构建。先核实独立模型挂载与镜像资产不会互相覆盖，C02 绝对引用/向量、MCP、模板、embedding/reranker 实际可读。当前宿主 GGUF 0600，容器是 `USER user`，须用实际身份检查模型可读和 `/output`、`/tmp`、审计目录可写，输入只读。固定并复测依赖：当前 SDK 合成环境的 `langchain-openai=1.4.1`、`langchain-core=1.5.1` 与 requirements 的 1.3.3/1.4.8 不同；知识包 embedding 栈也需对齐。授权后一次离线、无外网、平台规格的单例入口检查，证明本地后端加载、预算和双输出。 [P01-PACK](ROUND2_P01_PACKAGING_AUDIT_20261117.md) · [P01-GIT](ROUND2_P01_PUBLIC_RELEASE_AUDIT_20261117.md) · [Grand Challenge 运行环境](https://grand-challenge.org/documentation/runtime-environment/)
- **官网容器启动协议待确认**。当前 Dockerfile 为进程式入口，现场未找到 invoke 标签及平台健康／调用包装。Grand Challenge 当前通用教程使用 `GET /health`、`POST /invoke` 和 `org.grand-challenge.api-method=invoke`。11 月官方更新说明批处理需由 phase 启用，使用 invoke 可让模型在 batch 内复用。Round 2 是否接受现有进程式入口或使用该接口、以及具体 T4/A10G 配额均为 UNKNOWN。20.62 GiB GGUF 的全显存部署超过 T4 16 GB 容量，A10G 24 GB 仍须按实际 KV/工作区测试。离线检查前先核实获批 phase 的容器合同、模型传递方式、设备和共享进程状态隔离。如需 invoke，只在获批接点复用同一 Socket 推理入口并验证启动和退出。 [容器构建教程](https://grand-challenge.org/documentation/building-and-testing-the-container/) · [Grand Challenge 11 月更新](https://grand-challenge.org/blogs/november-2026-cycle-report/) · [CHIMERA 计算资源](https://chimera-agent.grand-challenge.org/submission/)

## 官网动作与 validation 机会

| 阶段 | 本轮结论 |
|---|---|
| 宿主 G2 | **阻断**。依赖批准的 Adaptive 正式入口和精确计数，使用无患者夹具。 |
| 离线镜像 | **未运行**。核验容器启动、权限、资源和输出。 |
| 官网 Try-out | **公开通用路径已核对**。已部署的 active image 在算法编辑器上传自备测试用例。每个 unique image 有 5 次编辑器 Try-out，额外工作按个人 credits；公开用例另有编辑动作。当前账号对应 image 和可用次数未知。 |
| Challenge phase validation | **UNKNOWN**。公开文档说明从挑战页手选 phase 提交、phase 配置限额；CHIMERA 公开 awards 页只确认二轮在 2026 年 11 月，尚无具体截止与评估流程。已知的通用 CHIMERA 5 validation/1 test 与 Try-out 的 5 次分别属于不同计数。11/15–12/15 为用户已提供的窗口，未独立核实其 phase/账号实际开关和余额。 |

北京时间 2026-11-18 核查官方公开文本。直访受限的页面用官方域名公开索引，未登录、未调用账号接口、未发起 Try-out 或 phase 作业。平台通用文档将算法自测与按 phase 手动提交分开。具体候选按钮是否消耗本账号首次正式机会，需在账户授权阶段凭明确 phase 名称、操作类型和资格回执确定。任何计入首次正式 validation 的动作都以 B 正式范围完成为前置。 [Try-out 官方说明](https://grand-challenge.org/documentation/try-out-your-algorithm-and-publish-a-test-case/) · [提交官方说明](https://grand-challenge.org/documentation/making-a-challenge-submission/) · [CHIMERA 二轮](https://chimera-agent.grand-challenge.org/awards/) · [CHIMERA 提交](https://chimera-agent.grand-challenge.org/submission/)

## 提交规划审查

最低成本的下一步是冻结 D01–D07 对各任务的明确授权，先做正式入口的无患者、零生成 Adaptive 接线验收。另行批准且完成 35B 恢复与精确计数后，G2 待审协议建议 F01/T1 一例，最多 12 次生成发送尝试及 195,072 输入加最大输出保留 token；T1 审查通过后才各添一例 T2/T3。三例合计上限 36 次、585,216 token，基于 16,384 slot 草案。本轮无后台任务。
