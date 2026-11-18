# A-SMOKE · Adaptive 官网冒烟前置检查

[公开版省略逐例记录；汇总与机制结论保留。]

## 依赖与执行

1. 从 G0 完成度、冻结 S4 v2-r1 的版本和配置、G1R/G1T 合同及两份 B 规划验收恢复既定设计。使用此前 P01-PACK/GIT 审计，聚焦当前基线新增阻断。历史材料只作查阅，特别是 [P01 机制卡](20261117_P01_MECHANISM_WIRING_AND_E2E.md)、[G1T SDK 报告](../../round2/reports/ROUND2_P01_G1T_SDK_REQUEST_BOUNDARY_20261118.md) 和 [B-MCP-SDK 规划验收](../../round2/reports/ROUND2_B_MCP_SDK_PLANNING_REVIEW_20261118.md)。
2. 从根 `inference.py` 逐层证明每个任务实际配置、控制器 build/调用点及工具观察后的修订和停止路径。将冻结 Adaptive 控制器与当前正式入口的 `planned_react` 等路径分别对照。已有回执优先。必要时用最小无患者本地探针确认调度身份，明确证据级别。若未接入，报告首个分歧、最小接线位置、可复用部件、依赖和验收标准，交规划冻结后再改。不得用同名开关间接证明运行身份，不重跑 91 例，不改策略。
3. 只读检查资源、已有端点和 35B 的实际进程与版本身份。仅当现有端点身份、权限、完整模板/分词路径和非生成接口都能核对时，最多 8 个无患者、单并发的计数请求，使用既有 SDK 最终 JSON 对照服务器原生模板、分词及计数。覆盖普通消息、工具结果和结构化填表的关键形状，报告服务软件和模板证据；独立列出 schema/grammar 是否可证，缺安全计数路径即记阻断。模型不可用时列出待批准恢复动作及 GPU、显存、CPU、内存和成本，不启停服务、不加载权重、不发生成请求、不向未识别端点送请求。
4. 交付待审 T1/T2/T3 提交 profile 清单。逐项写明 Adaptive、Reflection 及其来源和最终覆写、predictor/few-shot/影像权限、C02/B、成本与审计、最终字段落点的必要性和实证。待定机制明确记录及其阻断，不为了测试静默关闭或切换。给出 T1/T2/T3 正式输入、工具及决策请求、填表、后处理、两个输出的证据或缺口。复核模型启动、依赖、离线资源、磁盘、设备和非 root 路径，仅给出实际离线检查所需的最小步骤。
5. 区分宿主 G2、离线镜像、官网 Try-out 与 challenge phase validation。参照 [Grand Challenge Try-out](https://grand-challenge.org/documentation/try-out-your-algorithm-and-publish-a-test-case/)、[正式提交](https://grand-challenge.org/documentation/making-a-challenge-submission/) 和 [CHIMERA 二轮说明](https://chimera-agent.grand-challenge.org/awards/)，只读确认当前第二轮可用路径、是否影响本账号的 validation 机会、来源与时间；公开说明不足即标 UNKNOWN。若平台动作计入首次正式 validation，B 正式范围完成是前置。此卡不触发平台请求、账号操作、上传、建镜像、正式提交。
6. 建议最短宿主 G2 协议。无患者最小入口覆盖为先 T1 单例，再视情况 T2/T3 各一例。写明既有服务复用、冻结样例、最多请求和总 token、失败停点、成本估算、可追踪触发和两路输出。协议需规划及既定授权，执行在下一轮。

## 资源、验收和停止

现有环境，小型只读/无患者探针最多 2 个 CPU worker；0 真实生成、GPU 启动、训练、患者批次、镜像及平台操作。无生产源码、部署配置、冻结数据或共享队列的写入。交付以真实路径和证据可回查为验收；对每项明确通过、未运行或阻断，分列最小解除步骤。若已有服务无可信身份或 Adaptive 未接入，完成独立检查并返回阻断，无需等待外部条件。按卡交付后停止。

交付 `reports/ROUND2_A_SMOKE_READINESS_20261118.md`、`output/a_smoke_readiness_20261118/handoff.json` 和同目录的 `readiness_matrix.json`、`g2_proposal.md`。交接列出来源与指纹、证据级别、真实和合成调用数、资源、北京时间起止、后台状态，以及下一步最低成本。
