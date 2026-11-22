# 实验与过程索引

2026-11-22。R11 在 T1 Direct ReAct 隔离副本中通过独立合成验收，27/27。截止后的工具正文保持私有，已获准的正文、原结算及双 Socket 保留。正式接线及实机仍待验收。 [规划验收](../round2/reports/B12_R11_VIEW_FIX_PLANNING_REVIEW_20261122.md)

2026-11-22。B12 隔离候选通过最小验收。两套既有正式入口回归各通过 163 项，独立矩阵 83/84。合成反例复现既有模型视图中的时间截止缺口，真实模型与正式接线仍待验收。 [规划验收](../round2/reports/B12_ZERO_GENERATION_PLANNING_REVIEW_20261122.md)

2026-11-22。B12-RCA 独立验收通过。冻结双臂的首次语义失配可复现，结构臂的已确认冲突在模型原答中变为 UNKNOWN，解析保留原值。方案 A 获准进入隔离候选实现。 零新模型调用。 [规划验收](../round2/reports/B12_RCA_PLANNING_REVIEW_20261122.md)

2026-11-22。B12 短格式以一个无患者合成案例进行两次真实 35B 诊断回放。格式 2/2 通过，完整语义 0/2 通过。结构臂保留趋势未知，仍遗漏已确认的同次观察冲突。 [B12 规划验收](../round2/reports/ROUND2_B12_SHORT_OUTPUT_PLANNING_REVIEW_20261122.md)

2026-11-21。登记 Phase 2 新训练发布的传输完整性与输入字段变更。未运行训练或评测。[接收与 schema 记录](../round2/reports/ROUND2_PHASE2_SCHEMA_INTAKE_20261121.md)

2026-11-20。个人 WorkBuddy 5.11.3 界面确认 DeepSeek-V4.1-Flash（1017）。Codex 增加跨设备交接卡和按派发指纹回收结果的入口。个人版真实冒烟卡待返回。[个人桌面版交接](WORKBUDDY_DISPATCH_20261120.md)

2026-11-20。派发工程 WB-SMOKE 支持 Codex 按任务卡显式选择 WorkBuddy 托管智能体，读回 DeepSeek V4.1 Flash 型号后确认完成。本地 HTTP/ACP 冒烟通过，真实执行待 WMA 配置。无医学批次。[实现与复跑](WORKBUDDY_DISPATCH_20261120.md)

2026-11-19。A-F06-RECHECK 经原《主会话》桌面端 SSH 实际执行，预算动作在派发前停止、真实诊断和填表接续、两路 Socket 独立验收。T3 数值是预测，观察随访仍未知，时域依据和措辞单列。已放行 G2-CASE-PILOT 首批。[真实验收](../round2/reports/ROUND2_A_F06_BUDGET_RECHECK_LIVE_PLANNING_REVIEW_20261119.md)

2026-11-19。登记正式推理入口的隔离验收、工具回执绑定与错账停止合同。公开材料仅包含工程流程。[试跑运行与回执合同](../round2/docs/ROUND2_PILOT_RUNTIME_CONTRACT_20261119.md)

2026-11-19。A-F06-RECHECK 的两个自动前置轮均因回环和 GPU 访问受限停止，均未发真实请求。结论是执行环境阻断，随后改用桌面端 SSH。[宿主复核](../round2/reports/ROUND2_A_F06_BUDGET_RECHECK_PLANNING_STATUS_20261119.md)

2026-11-19。A-BUDGET-CLOSE 通过规划零生成验收。原 F06 前五条控制请求与对照逐项一致，收费工具在发送前封存，诊断、填表和双 Socket 通过合成验证。201 项回归通过。[规划验收](../round2/reports/ROUND2_A_BUDGET_CLOSE_PLANNING_REVIEW_20261119.md)

2026-11-19。A-F06 获批单例已执行。5 次实际生成均通过已到达请求的字段准入，余额归零后预算拒绝，诊断、填表和双 Socket 未到达。G2-CASE-PILOT 保持条件队列。[A-F06 规划验收](../round2/reports/ROUND2_A_F06_PLANNING_REVIEW_20261119.md)

2026-11-19。B-DATA 和 P01-UPLOAD-PREP 完成只读调查并经规划复核。真实时间资格仍未知，历史 Mac builder 已定位，现时构建环境和目标 phase 待证。P01-PROFILE 第一版已更新，正式组合待首批病例证据。[当前队列](CURRENT.md)

2026-11-19。A-T3-INPUT 已经规划验收。130 项回归通过，T3 歧义时间字段被隔离，T1/T2 合法同名字段保留。无新增真实生成。修复后的真实模型、生产 profile、builder 及目标 phase 待证。[A-T3-INPUT 规划验收](../round2/reports/ROUND2_A_T3_INPUT_UPLOAD_PLANNING_REVIEW_20261119.md)

2026-11-19。A-G2 的 T1/T2/T3 宿主工程冒烟经规划验收。三例、15 次生成、6 路输出，T2/T3 自然修订剩余计划。T3 合成时间字段经整份 JSON 到达请求并被误释为观察随访，修复前不晋级。上传准备已交，正式镜像、候选 profile 和平台条件待验。[A-G2 规划验收](../round2/reports/ROUND2_A_G2_ADAPTIVE_HOST_SMOKE_PLANNING_REVIEW_20261119.md)

2026-11-18。B-TIME 完成并经规划验收。真实合格纵向对数量未知，生产前仍需可信截止和历史可得证明。接受在数据进入决策状态前建立独立视图的设计。[B-TIME 规划验收](../round2/reports/ROUND2_B_TIME_PLANNING_REVIEW_20261118.md)

2026-11-18。A-SMOKE 由原《主会话》恢复并交付。规划接受前置调查。正式默认 `planned_react`，冻结 Adaptive 处于独立研究入口，35B 未监听。15 项交付、53 项实际源码及 4 个冻结夹具指纹核对一致。[前置调查](../round2/reports/ROUND2_A_SMOKE_READINESS_20261118.md)

2026-11-18。B-MCP-SDK 接续原《主会话》，`d20261118203925-ff11b6` 结束并经规划验收。T1/T2 的真实 stdio 工具回执、计量发送、MockTransport 消费与双输出均匹配，T3 跳过。226 项回归通过，真实模型、服务器模板计数及病例时间资格待核。[规划验收](../round2/reports/ROUND2_B_MCP_SDK_PLANNING_REVIEW_20261118.md) · [已执行接续卡](tasks/20261118_B_MCP_SDK_COMPATIBILITY.md)

2026-11-18。B-EVIDENCE-RECONCILIATION-BUILD 合成工程已验收。T1/T2 的未裁冲突状态经正式入口进入合成消费者，T3 显式跳过。346 项回归通过，其中 B 专项 37 项。真实 stdio 带工具分支在 G1T 请求格式校验处阻断，真实数据覆盖仍未知。[规划验收](../round2/reports/ROUND2_B_EVIDENCE_RECONCILIATION_PLANNING_REVIEW_20261118.md) · [已执行任务卡](tasks/20261118_B_EVIDENCE_RECONCILIATION_BUILD.md)

2026-11-18。C01 召回深度对照通过 `codex-dispatch` 自动接续原会话并完成（`d20261118172600-65caa3`）。6 个配对有效，前 40 补回 1 条但失去 1 条，最终前五覆盖仍为 3/6。规划核验 16 项交付并接受前 40 不晋级结论，保留前 20 基线。[对照报告](../round2/reports/ROUND2_C01_RECALL_DEPTH_COMPARISON_20261118.md) · [已执行接续卡](tasks/20261118_C01_RECALL_DEPTH_COMPARISON.md)

C01 冻结回放已验收。6 条查询的 48 个返回位置复现一致，8 个目标正文机会在召回、RRF 截断、重排结果窗的损失分别为 5、2、1。[回放报告](../round2/reports/ROUND2_C01_FROZEN_RETRIEVAL_REPLAY_20261118.md)

2026-11-18 最新。C01 首轮已交付并获限定验收。6 条旧查询中 3 条命中完整目标正文，3 条的目标正文在冻结索引中但未返回。独立核对 11 项交付和 48 个诊断请求送达位置。下一步待交已有《定位 C01 检索损失》会话，做原样 CPU 回放。[汇总报告](../round2/reports/ROUND2_C01_RETRIEVAL_LOSS_LOCALIZATION_20261118.md) · [唯一接续卡](tasks/20261118_C01_FROZEN_RETRIEVAL_REPLAY.md)

《主会话》交付 P01-G1T 的 SDK 请求计量接续。规划接受固定版本下的合成工程，核对 53 项交付产物和 198 项通过的测试记录。真实计数和服务端 schema 待验。[SDK 接续报告](../round2/reports/ROUND2_P01_G1T_SDK_REQUEST_BOUNDARY_20261118.md)

当前接收会话、唯一入口卡和交付文件见 [CURRENT 顶部的派发清单](CURRENT.md)。下文保留各阶段的历史记录。

2026-11-18。G1T 调查已交付，33 项产物指纹一致，实际精确计数接线 `BLOCKED`。G1R 有限工程已经验收。151 项测试记录、18 条入口事件链及 30 个输出文件核对一致，4 条末端覆写由回执标记为消费合同失败。[G1R 规划验收](../round2/reports/ROUND2_P01_G1R_PLANNING_REVIEW_20261118.md) · [G1T 调查报告](../round2/reports/ROUND2_P01_G1T_EXACT_TOOL_CONTEXT_20261118.md)

## 2026-11-18 G1A/A02规划验收与手动接续

| Task | 当前结论/状态 | 材料 |
|---|---|---|
| G1A | PLANNING_ACCEPTED_LIMITED_ENGINEERING：330产物/69源码身份、81项日志、42事件合成链/两socket核对；无live工具模板或真实端到端通过声明 | [规划验收](../round2/reports/ROUND2_G1A_A02_PLANNING_REVIEW_20261118.md) |
| A02 | PLANNING_ACCEPTED_BOUNDED_AUDIT：11交付身份、25历史绑定、4链12版本核查；未找到医学正确改判未消费的真实正例，18项回归矩阵仍仅设计 | 同上 |
| P01-G1R | MANUAL_HANDOFF_READY，用户转交唯一执行会话；已授权结构候选消费/唯一赋值/最终回执，0真实生成 | [完整任务](tasks/20261118_P01_G1R_REFLECTION_CONSUMPTION.md) |
| P01-G1T | QUEUED_MANUAL_HANDOFF；G1R审查后同一写入者另接，工具/结构模板精确计数及非生成探针，不并发 | [后继任务](tasks/20261118_P01_G1T_EXACT_TOOL_CONTEXT.md) |

本轮仅规划只读审查和任务编制，未自动派发/执行工程；机制整体NOT_READY，生产Reflection未启用、G2/构建/上传/平台仍冻结。下文旧在途状态以本条为准。

2026-11-18 10:31更新：A02 CLI已turn.completed并返回，11声明产物SHA一致，待规划验收；G1A改为用户批准的独立唯一代码写入会话，CLI d20261118103121-783ba8在途、尚无turn.completed，原主会话保持等待。0新临床批次，无工程/医学通过声明。[回执](CLI_DISPATCH_RECEIPTS_20261118.md)。

2026-11-18最新调度回执：G1A CLI请求因原主会话active writer失败；A02 CLI已建立独立thread但尚待turn.completed。不能将thread.started记为派发成功/机制验收。[完整回执](CLI_DISPATCH_RECEIPTS_20261118.md)。本轮无临床实验，旧MCP通道禁用。

## 最新接续（2026-11-17）

| 项 | 实际状态/门槛 | 材料 |
|---|---|---|
| B01 | DELIVERED_AWAIT_PLANNING_REVIEW，22:34:02完成；66标记/59记录/29风险组，认证消解0，无后台 | [报告](../round2/reports/ROUND2_EVIDENCE_RECOVERY_FEASIBILITY_20261117.md) |
| B02-LIT | DELIVERED_AWAIT_PLANNING_REVIEW，22:34:22完成；5论文/3仓库，建议HOLD、真实纵向资格UNKNOWN，无后台 | [报告](../round2/reports/ROUND2_B02_LITERATURE_FEASIBILITY_20261117.md) |
| P01-G1A | 用户已批准；本会话发送旧MCP端点失败，尚无送达回执。仅公共执行基础/0真实生成 | [Goal](tasks/20261117_P01_G1A_COMMON_RUNTIME.md) |
| A02 | 用户已批准；本会话创建旧MCP端点失败，无成功threadId。仅有界只读归因/最小修复契约 | [Goal](tasks/20261117_A02_REFLECTION_PATH.md) |
| P01-G1R | 待G1A接口+A02契约规划审查；由原主会话统一消费链修复，不开并发代码分支 | [调度与转交文本](REFLECTION_DISPATCH_20261117.md) |

当前没有新临床批次/生产Reflection启用/构建/上传/平台操作。下文旧运行状态按最新条目更新，不重复旧审计。

| 当前正式调度（2026-11-17 22:19） | 状态 | 证据/范围 |
|---|---|---|
| P01-MECH-G0 | 正常完成，有限交付规划接受；机制总体NOT_READY，G1/G2未启动 | [规划核查](../round2/reports/ROUND2_P01_G0_PLANNING_REVIEW_20261117.md) |
| B01 | 正式派发并RUNNING，既有会话01a1e9da-451c-7113-8b39-1fbf5c93b801，sol High | [任务](tasks/20261117_B01_EVIDENCE_RECOVERY.md) |
| B02-LIT | 正式派发并RUNNING，新会话01a1e9e0-c5da-7570-9291-b97e8f08670e，sol High | [任务](tasks/20261117_B02_LITERATURE_AND_FEASIBILITY.md) |
| P01-G1A | PROPOSED_NOT_DISPATCHED，拟复用主会话的共用基础修复，0真实生成 | [接续Goal](tasks/20261117_P01_G1A_COMMON_RUNTIME.md) |

| 最新接续（2026-11-17 22:12） | 状态与证据 | 材料 |
|---|---|---|
| Codex通道恢复 | 官方MCP刷新；实际创建/消息回读通过，完整保留历史4消息/2创建错误；非医学实验 | [诊断](../round2/reports/ROUND2_CODEX_SESSION_CHANNEL_REPAIR_20261117.md) |
| B01接收确认 | 已创建01a1e9da-451c-7113-8b39-1fbf5c93b801，sol High；仅读任务卡/回执，66条分析未启动 | [任务卡](tasks/20261117_B01_EVIDENCE_RECOVERY.md) |
| P01-MECH-G0 | 主会话已交付，待规划独立审查，G1/G2未开始；本轮只读确认交付不等于验收 | [完成度报告](../round2/reports/ROUND2_P01_MECHANISM_COMPLETENESS_20261117.md) |


最新调度（2026-11-17）：先P01-MECH机制验收；正式打包/上传/平台准备冻结。规划不承担工程，原有《主会话》执行。仅G0可启动，G1/G2待规划冻结，不是新实验结果。

| Task | 状态与依赖 | 指令 |
|---|---|---|
| P01-MECH | HANDOFF_READY_DISPATCH_FAILED：跨会话HTTP传输失败、未取得送达回执，待用户转交；仅G0具备启动条件→规划审查→G1接线→G2有限真实模型→规划验收→再决定构建 | [完整Goal](tasks/20261117_P01_MECHANISM_WIRING_AND_E2E.md) |


| 工作 | 测量对象与结果 | 报告 |
|---|---|---|
| 数据池登记 | 三来源1275条记录不是独立患者数；来源／版本分账 | [登记](../round2/reports/ROUND2_DATA_POOL_REGISTRATION_20261113.md) |
| 冲突来源审查 | 171条候选，105条可解释、66条未裁；不改原值 | [审查](../round2/reports/ROUND2_CONFLICT_SOURCE_REVIEW_20261113.md) |
| 信息与成本边界 | 75项测试、423条官方输入绑定；不测医学提分 | [验收](../round2/reports/ROUND2_S2_FULL_SCOPE_ACCEPTANCE_20261113.md) |
| 固定／自主候选 | 共同计划、观察、权限，脚本＋真实MCP验收 | [候选](../round2/reports/ROUND2_SHARED_PLAN_CANDIDATE_ACCEPTANCE_20261113.md) |
| 首轮真实配对v1 | 91例，两组均60正确；自主控制失败较多 | [结果与机制](../round2/reports/ROUND2_S4_PAIRED_RESULTS_20261114.md) |
| 控制修复v2 | 接口和有限恢复工程验收，不是提分实验 | [修复](../round2/reports/ROUND2_CONTROL_REPAIR_ACCEPTANCE_20261114.md) |
| v2首次正式尝试 | 源码实际导入v1，私有分析拒绝；760次调用不作v2医学结果 | [失效取证与数据范围](../round2/reports/ROUND2_S4_V2_PROVENANCE_FAILURE_20261115.md) |
| v2-r1有效完整对照 | 182组别全成功；两组64/91，自主信息少10.33%，组内token约2.22倍；无准确率优势证据 | [有效结果与机制](../round2/reports/ROUND2_S4_V2_RESULTS_20261115.md) |
| 原始文献定位修正 | 已有相似实验及负面证据比较；S4降为工程基线，非新生成 | [文献比较](../round2/reports/ROUND2_LITERATURE_POSITIONING_20261115.md) |
| 6例／47次离线机制复盘 | 3例同临床事实但请求不同；23次早停少25单位，24次无患者信息节省；未作因果干预 | [机制复盘](../round2/reports/ROUND2_MECHANISM_REVIEW_20261115.md) |
| 两份任务规则来源／适用性审查 | 69条全覆盖；36窄义相符、17条件不全、13偏差、3未定位／说明；0新调用，资源未改 | [来源台账与单一候选](../round2/reports/ROUND2_EVIDENCE_APPLICABILITY_AUDIT_20261117.md) |
| C02来源／适用条件证据包 | 工程204测试＋真实MCP/CPU检索/stub消费通过；默认关闭，仅2原文载体；医学NO_GO、0新临床生成 | [验收与边界](../round2/reports/ROUND2_EVIDENCE_PACKAGE_ACCEPTANCE_20261117.md) |

更早试验见 reports 下的过程报告。不同患者集合／输入版本／统计环节不能连成一条提分曲线，重复调用不增加独立患者数。

工具试验（非医学实验）：[Codex → DSH 委派](DSH_DELEGATION_20261114.md)，无敏感数据冒烟通过。


## 2026-11-17 规划与任务登记（不是新实验）

| ID | 状态与产出 | 指令／证据 |
|---|---|---|
| INIT-20261117 | 状态恢复完成，0新临床生成，冻结源码与历史产物复核通过 | [规划报告](../round2/docs/ROUND2_PLANNING_INITIALIZATION_20261117.md) |
| MECH-20261115 | 已完成的6例／47次只读复盘，本轮恢复并补入导航 | [机制报告](../round2/reports/ROUND2_MECHANISM_REVIEW_20261115.md) |
| LIT-20261115 | 既有比较性核查，含负面结论与适配限制，不当新机制实验 | [定位报告](../round2/reports/ROUND2_LITERATURE_POSITIONING_20261115.md) |
| A01 | PLANNING_ACCEPTED_WITH_ADDENDUM：69条交付已验收，P60理由更正在C02新补充中落实 | [验收卡](tasks/20261117_A01_SOURCE_APPLICABILITY.md) |
| A02 | 具备条件，待用户确认；Reflection检测／纠正／审核／消费分层 | [任务指令](tasks/20261117_A02_REFLECTION_PATH.md) |
| B01 | 具备条件，待用户确认；66条未裁候选的恢复与合法成本 | [任务指令](tasks/20261117_B01_EVIDENCE_RECOVERY.md) |
| C01 | 可静态准备，规范冻结依赖A01；检索／诊断利用分开评价 | [任务指令](tasks/20261117_C01_RETRIEVAL_CONTRACT.md) |
| D01 | 具备条件，待用户确认；最多3个外部来源的资格与目标映射 | [任务指令](tasks/20261117_D01_EXTERNAL_DATA.md) |

上述READY不表示已派发或已生成结果。正式临床批次、训练、批量下载和平台提交均未启动。


| 新Task | 状态／边界 | 指令 |
|---|---|---|
| A01规划验收 | 接受进入候选准备，P60理由更正为必做项；非专家医学裁定 | 本地output/planning_c02_control_20261117/a01_planning_review.json |
| C02 | PLANNING_ACCEPTED_ENGINEERING_CLINICAL_NO_GO：工程结题，真实覆盖门槛未满足 | [完整目标](tasks/20261117_C02_EVIDENCE_PACKAGE.md) |

C02交付：DELIVERED_AWAIT_PLANNING_REVIEW。工程通过与医学晋级分开；真实合法格式/条件/来源覆盖未完成，临床NO_GO。[未执行验证草案](../round2/docs/ROUND2_EVIDENCE_PACKAGE_VALIDATION_DRAFT_20261117.md)。


| 规划终验 | 已核查证据／状态 | 报告 |
|---|---|---|
| C02终验 | 独立111项通过，与执行204项重叠；271新冻结／3321既有／278旧源码保全；工程通过、医学NO_GO；184公开文件独立回读 | [规划终验](../round2/reports/ROUND2_C02_PLANNING_ACCEPTANCE_20261117.md) |


| 顺序调整 | 状态／口径 | 材料 |
|---|---|---|
| P01提交准备 | READY_AWAIT_USER；先机制触发／消费与实际离线镜像，再一次validation冒烟；0新实验／0提交 | [新计划](../round2/docs/ROUND2_SUBMISSION_SMOKE_FIRST_PLAN_20261117.md) · [任务卡](tasks/20261117_P01_TRIGGER_AND_SUBMISSION_READINESS.md) |


| 接续登记 | 状态与证据 | 材料 |
|---|---|---|
| P01-PACK | 只读报告存在，41控制文件SHA复核一致；NOT_READY，非构建验收 | [审查](../round2/reports/ROUND2_P01_PACKAGING_AUDIT_20261117.md) |
| P01-GIT | 已有仓库public；过程快照与独立构建分开，未新建仓库或上传 | [审查](../round2/reports/ROUND2_P01_PUBLIC_RELEASE_AUDIT_20261117.md) |
| B02 | INVESTIGATE／NOT_DISPATCHED；先文献、作者实现与合法纵向可行性；0实验 | [候选](../round2/docs/ROUND2_LONGITUDINAL_EVIDENCE_CANDIDATE_20261117.md) · [待派发任务](tasks/20261117_B02_LITERATURE_AND_FEASIBILITY.md) |
| 接续总控 | Mac原件未接收，SSH自动审批／消息连接阻断明确记录 | [整合报告](../round2/reports/ROUND2_P01_REVIEW_AND_B02_INTAKE_20261117.md) |
