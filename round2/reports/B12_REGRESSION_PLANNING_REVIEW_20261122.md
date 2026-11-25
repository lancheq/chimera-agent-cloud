# B12-REGRESSION 规划验收

2026-11-22。**回归 PARTIAL，真实 35B 及正式接线 HOLD。** 独立复算 282 份冻结文件与 29 项交付，无失配。两份正式入口 xunit 结果均为 163/163，通过项和双 Socket 比较一致，各保留两条相同的序列化警告。它们覆盖的是原路径及候选存在但默认关闭的兼容性。单独预登记的 R01–R16 矩阵 83/84 通过，R11 通过 2/3。旧格式对已证不可比及独立语义字段的拒绝是已登记的测量边界。 [执行报告](B12_REGRESSION_MATRIX.md) · [验收回执](../output/b12_regression_planning_review_20261122/acceptance.json)

R11 由规划独立复跑 T1 合成入口、真实 MCP stdio 与 SDK MockTransport。审计判 `after_cutoff`，B 状态和注记排除晚到合成值，第二条 SDK agent 请求中的原始 ToolMessage 仍含该值，两路合成输出均写出。三条请求只有第二条受影响。模型网络请求、临床工具、GPU 与真实信息单位为 0。R11 的延迟正文旁路在既有正式链路可复现，B12 新模块仍未接入。模型对该值的真实消费和真实病例影响均未测。 [独立 R11 回执](../output/b12_regression_planning_review_20261122/r11_independent.json)

自动回归轮 `01a20199-5944-72d3-b440-d605dbf1efcd` 在已生成主要成果后退出，平台回执为 `turn.failed`。原《主会话》恢复轮 `01a20233-0e88-7812-98d4-0510be467023` 已完成交付。验收依据冻结工件、恢复轮及独立复跑，未重提原回归。

新增 P0 B12-R11-BOUNDARY。由原《主会话》定位字符串与原生 MCP 消息的正文路径，冻结一个不扩大权限的合法模型视图修复合同，提交最小正反例。此阶段只读，既有源码和候选保持封存。所有真实 35B、生产接线与平台 validation 继续等待。 [唯一接续卡](../discussion/tasks/20261122_B12_R11_BOUNDARY.md)
