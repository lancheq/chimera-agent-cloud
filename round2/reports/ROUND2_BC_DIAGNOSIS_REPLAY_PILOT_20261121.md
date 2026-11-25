# BC-DIAG-REPLAY · 两分支准入与零生成验收

2026-11-21，北京时间。限服务器本地审查。**C03 的两例真实历史请求停在字段准入，B12-S1 的无患者双臂通过离线验收，真实回放停在宿主门槛。** 本轮没有真实 35B 生成、原生计数、临床工具调用、GPU 任务或服务启动。 [任务卡](../discussion/tasks/20261121_BC_DIAGNOSIS_REPLAY_PILOT.md) · [私有冻结清单](../output/bc_diagnosis_replay_pilot_20261121/frozen_manifest.json)

## C03 正文投影

按冻结 C01 查询编号升序扫描六条记录。选中两条原诊断请求，各自含经原文目录校勘确认的导航和完整推荐正文，保守风险组互异。两个版本记录与 11-04 Phase 2 的初始和临床快照指纹逐项匹配。核对历史诊断请求摘要、工具窗口、账本回执及 C01 重放定位。选择依据为来源、顺序、比较材料和去重，未使用 GT、旧模型正确性或新的 11-21 发布。

两条历史请求合计 16 个有序返回位置。只读影子投影各移出一条经核实的纯目录，旧排名和文本指纹留在 `NAVIGATION_NON_EVIDENCE` 审计槽位。保留 14 个原位置，其中 2 条完整目标正文和 6 条 QA。正文含标题的反例在误标为目录时被拒绝。原正文、QA、评分字段和相对顺序保持，空位无补充。 [C03 语料审计](ROUND2_C03_CORPUS_AUDIT_20261121.md) · [C01 冻结回放](ROUND2_C01_FROZEN_RETRIEVAL_REPLAY_20261118.md) · [私有差异](../output/bc_diagnosis_replay_pilot_20261121/branch_differences.json)

**准入阻断在完整历史请求。** 两条请求均有首屏混合自由文本及其他历史字段。原运行回执和 C01 能证明这些字节曾进入诊断请求及工具返回的内容，逐字段合法可见性仍缺证。其中一条另保留项目二次任务摘要。未生成可执行病例请求，未用另一版数据替换，也未删改首屏字段来构造新的对照。后继只需审查两条已选请求的完整逐字段许可和摘要范围。若必要清洗改变 C03 的唯一改动，两例维持跳过。

## B12-S1 合成证据消费

冻结夹具按已验原型重新构造，来源、原字段、两条独立调用回执和原值与既有合成回放一致。两臂共享同一组可见事实，包含同观察两种值、两条 lab 回执及三条来源指针。处理臂只附加由冻结证据得出的未裁冲突状态和两组来源关系。无新事实、取证、旧回答或参考答案。预期分别读回两条原值、来源、未裁状态以及重复 lab 回执有无被当成两份独立证据。 [B12 冻结报告](ROUND2_B12_SOURCE_CONFLICT_20261121.md) · [双臂请求](../output/bc_diagnosis_replay_pilot_20261121/B12_frozen_requests.json)

复用现有诊断系统框架和 G1T SDK 请求边界。两臂同用登记的 Qwen3.6-35B、模板指纹、采样配置和种子。诊断输出上限固定 1,024，本轮工程设置的推理预算为 512。MockTransport 实际捕获两条合成 SDK HTTP JSON，与冻结载荷 SHA 和模拟计数输入逐条匹配。改写冻结请求和超上下文均在发送前拒绝。桩响应和常量计数单列，35B 的原生计数、usage 和诊断响应均未实测。 [零生成门槛](../output/bc_diagnosis_replay_pilot_20261121/zero_generation_gate.json) · [仅 Mock 请求及输出](../output/bc_diagnosis_replay_pilot_20261121/mock_requests_and_outputs.json)

## 宿主停点与交接

当前会话创建本机 IPv4 回环 socket 返回 EPERM，GPU 查询退出 9，未确认可用 CUDA 设备或空闲 GPU1。模型和服务二进制路径可见，实际实例身份、实机模板和权重字节校验未启动。B12 的有界执行入口在发出任何 HTTP 请求或启动模型前停止。接续条件为原会话可核验本机回环、同资产服务与 GPU1，再对两条冻结合成请求做原生模板、分词和计数核验。任何一项不符则保持零生成。 [宿主探针](../output/bc_diagnosis_replay_pilot_20261121/host_probe.json) · [零调用停止回执](../output/bc_diagnosis_replay_pilot_20261121/live_attempt_gate.json) · [B12 受限入口](../output/bc_diagnosis_replay_pilot_20261121/b12_local_replay.py)

两分支真实模型输出均未产生。目录移除后的临床引用、适用性、诊断变化和 B12 的冲突消费均为 UNKNOWN。完整 planner、动态 MCP、填表和 Socket 未运行，无临床性能评分和生产晋级。交付为独立目录中的冻结清单、零生成证据与停点。无后台任务。 [私有交接](../output/bc_diagnosis_replay_pilot_20261121/handoff.json)
