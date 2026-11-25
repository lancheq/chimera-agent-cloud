# B12-SEMANTIC · 最小关系机制

**PASS_CANDIDATE，交规划独立验收。** 按冻结方案 A 新增未接入运行链的候选模块。在同一份 B12-S1 合成记录上，程序保全「同次、可比、冲突未裁、真值未裁」，历史 35B 结构臂原答的冲突状态仍为 UNKNOWN。既有语义评分和隔离一致性检查均拒绝该答复，分别保留原答和首个失配。 [候选源码](/home/hedongze/CHIMERA-agent/src/chimera_agent_baseline/agent/b12_evidence_state.py) · [最小检验原始结果](/home/hedongze/CHIMERA-agent/output/b12_semantic_20261122/minimal_result.json)

## 权威边界和复用

候选接收已获授权的 B1/B2 快照精确字节。私有复跑配置独立提供摘要、任务、发布、病例、会话、快照和截止，选择已记录的对照关系。通过摘要和作用域检查后，再核对字段、文档摘要、回执、分组和截止。上游拒绝只保留原因和引用 ID。模型文本、指令或投影 JSON 不进入权威路径。当前可信输入限已认证的 T1 合成 B12-S1/S2/S3 和最小合成反例；生产绑定适配和三任务消费尚未接线。

直接复用既有 `Annotation`、`compare`、日期区间和组摘要计算。借已封存 B1/B2 快照复用原型的合法来源绑定、字段定位和已结算回执结果。候选不重新构造运行态 `Source`，也不重新调用 `bound_tool_sources`。这两个能力继续归上游，私有合成缺证反例由既有原型实际生成。没有把已保存的程序状态当成新的准入凭证。选择固定的私有快照边界使正式入口、原提示词、旧输出 schema、profile 和 Socket 无需改动。

## 最小验收

| 检查 | 实际结果 |
| --- | --- |
| 程序关系和原值 | 两证据组、一个观察，保全 5 与 6、三条来源。冲突 CONFIRMED_UNRESOLVED，真值 UNRESOLVED，趋势 UNKNOWN |
| 模型原答独立评分 | 旧评分首错 `conflict_status_unsubstantiated`，候选独立检查首错 `conflict_status`。旧格式通过，原始响应字节未改 |
| 缺证与已证不可比 | 两者在既有 `compare` 中均为 `not_comparable`。缺方法保留可比和冲突 UNKNOWN，已证方法不一致映射 INCOMPARABLE 和 NOT_APPLICABLE |
| 未认证及错绑 | 错病例或模型文本拒绝。错工具回执只保留 UNBOUND_SOURCE，冲突和观察数为 UNKNOWN |
| 旧枚举和来源完整性 | 同次同值投影为冻结枚举 `same_observation_consistent`，通过旧格式。严格格式可解析的重复来源 ID 在语义检查被拒，同组的两个合法别名仍通过 |
| 存取和默认关闭 | 认证原件重算后可恢复相同状态，篡改投影拒绝，默认返回 None |

旧枚举缺少独立的不可比取值，候选对这个状态拒绝生成旧字段断言。审核仅检查既有严格解析后的可比较字段。历史结构臂的原答和固定理由单独存证，独立模型解释、真实病例资格、三任务、正式下游与完整 R01–R16 回归仍待各自验收。无真值选择、趋势拟合、诊断改写或模型实机结论。

## 变更与复跑

只新增冻结清单允许的一个源文件和私有交付。84 份冻结输入复核 SHA-256 和字节数一致。单 CPU 进程、亲和 CPU 0，真实生成、GPU、临床取证和信息单位均为 0，无后台实验。配置和工件版本及差分在回执中列明。

~~~bash
bash /home/hedongze/CHIMERA-agent/output/b12_semantic_20261122/replay_minimal.sh
~~~

[字段合同](/home/hedongze/CHIMERA-agent/output/b12_semantic_20261122/field_contract.json) · [状态转换](/home/hedongze/CHIMERA-agent/output/b12_semantic_20261122/state_transitions.md) · [代码差分](/home/hedongze/CHIMERA-agent/output/b12_semantic_20261122/candidate.diff) · [交接](/home/hedongze/CHIMERA-agent/output/b12_semantic_20261122/handoff.json)
