# P01-PROFILE 三任务生产候选裁定

2026-11-19。唯一受控离线候选为 **P01-PROD-CANDIDATE-V1**。独立 staging 可执行。正式生产和 validation 仍需准入证据。

| 门槛 | 候选范围 | 待验 |
| --- | --- | --- |
| D01 控制 | Adaptive v2、共同诊断、MCP 权限与预算耗尽前停止 | 与实际平台启动模式和非 root 环境一致 |
| D02 审核 | Reflection/atomic 关闭 | 结构化纠正、采纳、写回的独立批准 |
| D03 取材 | 按任务及来源约束首屏和官方 MCP。predictor、few-shot 和额外影像关闭 | T3 目标字段持续排除，逐请求检查 |
| D04 检索 | 冻结指南 RAG。C02 和 B 关闭 | RAG 适用性复核，B 正式范围及真实消费待验 |
| D05 输出 | 模型表单提供决策与理由。正式投影负责 schema，关闭后验置信度及诊断覆写 | T3 保持官方 Socket 结构，预测时间数值逻辑 HOLD |
| D06 计量 | 原生完整模板计数与实际 HTTP 请求对照 | 容器内版号、模板、slot 和资源验收 |
| D07 审计 | 保存最小许可、工具回执、请求到表单及双 Socket 的私有指纹 | 错绑、越权、异常的实际停止与证据保全 |

T1 决策用 `biopsy_decision`，T2 用 `treatment_recommendation.primary`。T1/T2 理由、置信度、变量权重和工具使用顺序按官方 schema 投影。T3 有 `event`、`months_to_recurrence` 及理由输出。观察随访、预测时间和正式字段分别核验。允许无患者夹具检查 T3 形状，预测数值晋级须独立确认口径、来源和跨层一致性。[官方输入与结果合同](https://chimera-agent.grand-challenge.org/data-sources-and-imaging/)

候选锁定 Adaptive v2 工作源码、Qwen3.6-35B-A3B-UD-Q4_K_M、llama-server、SDK 模板及冻结指南索引。工作树基线是 `7301642d0eedda2f143ba44b93d42f948249d813`，独立锁定清单另记工作树指纹。历史 v15 仅作构建环境和路径对照。实际候选 image digest、资产许可和安装路径须从独立 staging 核实。

当前 trace 复核及 staging 零新增生成。待核准的有限离线测试共三个固定无患者夹具，单 GPU、单并发。每任务最多 12 次真实请求、3 信息单位及 195,072 预留 token。正式 validation 依赖 T3 语义裁定、B 正式范围、同一镜像与模型配对的离线验收和目标 phase 授权。训练集结果和逐例证据保留本机。[赛事规则](https://chimera-agent.grand-challenge.org/rules/)
