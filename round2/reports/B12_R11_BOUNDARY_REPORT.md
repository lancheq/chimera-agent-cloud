# B12-R11-BOUNDARY · 时间截止与模型可见视图

**定向复验和设计完成，接线 HOLD。** R11 的问题出在原始工具正文和 B 注记走两条路径。B 注记按截止排除了迟到行，原始 ToolMessage 仍被第 2 次 SDK 请求发送给替身。原生文本块和字符串两条路径都复现，三条请求的 SDK 载荷与 HTTP 字节指纹逐条一致。只在 SDK 末端改写无法覆盖以原始观察历史为输入的其他消费者。

## 已证链路

| 环节 | T1 合成 R11 结果 |
| --- | --- |
| 工具与账本 | 两份封存的真实本地 MCP stdio 运行，独立对照沿原 Guard，唯一调用 `call-fixture-lab`。同一结算请求有预留和 `returned`，1 次准入、1 个合成信息单位、无在途。时点扣留未改变结算。 |
| MCP 正文与结构 | native 回执保留一个规范化 `TextContent`、匹配的 `structuredContent.result` 与 LangChain 原 artifact。文本与账本的原文文档摘要可重算。字符串复验复用同一 FastMCP handler 和账本，在适配器边界产生字符串，无 stdio 子进程。旧封存件无线上 stdio 帧字节，证据止于规范化原生结构及准确 UTF-8 文本。 |
| B 注记 | `EvidenceSession._project()` 以文档摘要、字段路径和回执关联两行。合格实验室值 6 的可得日为 2020-01-04。迟到行的可得日为 2020-08-02，晚于合成截止 2020-07-01，被记 `after_cutoff`，不出现在 B 注记。这个状态属于 B 审计；原信息账本状态仍为 `returned`。 |
| Graph 与 SDK | ToolNode 的原始消息写进 Graph。native 路径由 `sdk_messages()` 核验单块后把原文复制成字符串，string 路径直接通过。两路在第 2 次 agent 请求的 `messages[3]` 以同一 ID 发送同一原文，G1T 计量和 HTTP MockTransport 回执明示哨兵在内。第 1 次 agent 与第 3 次填表请求均无哨兵。 |
| 草稿和表单 | Direct ReAct 的第 2 次 agent 回复是固定合成草稿，没有回吐哨兵。填表输入的内部 `messages` 仍含原文本，`tool_order` 包含成功工具。实际填表请求从最后一条合成 assistant 文本构建，本次请求无哨兵。未启用 Reflection 和 atomic，不能以本例证明它们已隔离。 |
| 输出 | 正式入口写出决策、理由两 Socket，字节指纹逐份复算。两路请求中实际的临床影响、真实模型是否会复述迟到值及真实病例覆盖均为 UNKNOWN。 |

[逐请求与模型可见路径](/home/hedongze/CHIMERA-agent/output/b12_r11_boundary_20261122/flow_map.json) · [对照回读与账本、源码指纹](/home/hedongze/CHIMERA-agent/output/b12_r11_boundary_20261122/boundary_readback.json)

## 适用范围

- **Direct ReAct T1** 已证工具原文触达和填表状态持有。T2 共享实现是静态发现，本卡未用 T2 跑截止。T1/T2 初始资料的模板与完整 JSON 附录，以及 `patient` 独立来源，是实施须封堵的入口。冻结 R11 的哨兵只在工具来源，这些初始路径没有同类动态证据。
- **Adaptive 与共同诊断** 在 R11 无适用入口。现有 `shared_plan` 静态可见独立的工具调用、原始 journal/检查点与 `observation_view()`，策略、共同诊断和表单读取该分支的状态。需要接在观察持久化前，并另证原生调用 ID 与结算关联。旧 planned ReAct 的预取和预算直达下游不经同一 SDK 转换，也需明确纳入或在受控 profile 拒绝。
- **审核与表单** 已证表单输入含原工具消息，实际第 3 请求没有哨兵。Graph Reflection 汇集含工具消息的历史，其他审核与程序化使用要逐路径验收。T3 无批准的纵向 adapter 保持 SKIP，旧工具拒绝和现有合格初始视图继续使用。此次无 T3 模型请求或输出测试。

## 实施建议

在原权限核验及真实结算后、写入 Graph 或 Adaptive 观察历史前发布统一安全视图。初始任务输入在渲染前使用同一规则。可证的完整行保留原值，已结算但无可证安全片段时保留工具角色与 ID 并用固定占位，错绑或完整性失败停止。原始证据继续私有持久化，费用照实记录，决策资格单独登记。G1T 仍以投影后的最终请求计量。由规划冻结最小接线与九组零生成验收，然后才可修改源码。

[修复合同](/home/hedongze/CHIMERA-agent/output/b12_r11_boundary_20261122/repair_contract.md) · [零生成验收协议](/home/hedongze/CHIMERA-agent/output/b12_r11_boundary_20261122/acceptance_protocol.json)

## 复跑和交接

先复用执行与规划两份 native 回执，仅新增对应字符串定向复验及一次同范围复跑，未重跑 326 项。前置核对识别了一处规划文档版本更新，预登记 R01–R16 文本一致。规划验收的 `source_report_sha256` 实际锚定回归 `handoff.json`，报告本身按 handoff 的独立条目核对。两处均保留原文件与实测记录，详细差异见交接。

单 CPU 亲和、一个测试 worker。模型调用为 SDK MockTransport，合成工具费用单独记录。真实模型、GPU、临床工具、真实信息单位和病例均为 0。无后台任务。未实施共享源码、配置或权限修改。

~~~bash
bash /home/hedongze/CHIMERA-agent/output/b12_r11_boundary_20261122/check.sh --check
~~~

[交接回执](/home/hedongze/CHIMERA-agent/output/b12_r11_boundary_20261122/handoff.json)
