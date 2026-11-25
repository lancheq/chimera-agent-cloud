# G2-CASE-PILOT · T2/T3 首批接续

2026-11-19，北京时间。限本地规划审查。执行范围为冻结 T2、T3 各一例，顺序固定。T1 只离线回读。 [任务卡](../discussion/tasks/20261119_G2_CASE_PILOT_T2_T3_RESUME.md) · [上一轮报告](ROUND2_G2_CASE_PILOT_INTAKE_CONTINUATION_20261119.md)

## 回读修正与零生成验收

监督器从前序 `tool_request` 读取工具名及参数，以唯一请求编号、请求摘要、journal 顺序及时间绑定 `tool_return`。另核对正式 MCP 注册白名单、SQLite 快照、预检账本前缀、结算交易、缓存值与关闭回执。缺失、重复、早于请求、错绑、越权、在途和错账本均关闭式拒绝。冻结 T1 实际轨迹离线复核通过，6 次已发送请求、3 笔工具结算及两份 Socket 保持原样。11 项合成绑定与调度回归、4 项 SQLite 导出回归通过。 [T1 封存回读](../output/g2_case_pilot_20261119/remaining/T1_sealed_reader_replay.json) · [绑定回归](../output/g2_case_pilot_20261119/remaining/receipt_regression.log) · [账本回归](../output/g2_case_pilot_20261119/remaining/ledger_regression.log)

独立核对 T2、T3 冻结原始文件、投影、配置、许可和正式源码指纹。执行器只挂载两份批准视图，直接调度 T1 会在建立病例进程前拒绝。7 项正式入口零生成检查通过，含 T2/T3 各自的实际 MCP 白名单、T1 禁调度、漏挂、错任务、输入改写、数据根可读的合成反例。GT、原数据根和扣留栏在正式病例进程不可见。未改动正式推理代码、病例视图和准入合同。B/C02、Reflection、predictor、few-shot、额外影像与诊断覆写均按冻结配置排除。 [锁定清单](../output/g2_case_pilot_20261119/remaining/batch_lock.json) · [零生成门槛](../output/g2_case_pilot_20261119/remaining/dry/gate.json)

## 真实试跑

T2 及 T3 按锁定顺序完成，每例均由正式入口写出两份 Socket，指纹逐一通过回读。实际 SDK 载荷、HTTP 发送、后端原生计数和 usage 逐次匹配。两例均发生一次非空重规划，随后由策略正常停止。诊断所见的工具结果与已结算回执完全对齐。 [阶段证据](../output/g2_case_pilot_20261119/remaining/layer_summary.json) · [T2 回读](../output/g2_case_pilot_20261119/remaining/live/T2/case_readback.json) · [T3 回读](../output/g2_case_pilot_20261119/remaining/live/T3/case_readback.json)

| 任务 | 北京时间 | 真实请求 | 工具受理／信息单位 | 输入／输出 token | 预留 token | RAG |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| T2 | 18:57:51–19:00:58 | 7 | 4／3 | 27,657／5,703 | 40,969 | 触发，检索结果进入诊断请求 |
| T3 | 19:00:58–19:02:38 | 6 | 3／3 | 11,276／5,358 | 23,564 | 未触发 |
| 本次合计 | 两例连续执行 | 13 | 7／6 | 38,933／11,061 | 64,533 | 单例证据 |
| 首批累计含既有 T1 | 三例 | 19 | 10／9 | 50,436／15,798 | 88,324 | 按实际触发登记 |

T2 的诊断请求含 4 条授权观察，其中一条为 RAG 返回。T3 的诊断请求含 3 条授权工具观察。没有打开 GT 或参考推理，结论限于工程执行和证据消费。

### T3 随访与预测时间

实际取得的工具证据中没有术后随访来源，观察随访和观察复发仍为 UNKNOWN。诊断草稿明确提示缺少随访资料，并讨论了预测风险时域。诊断中识别出的时域与最终预测月份数值不同。该正式数值首次出现在第 6 次请求的填表响应，随后与决策 Socket 的对应字段逐值相符。独立回读未识别出把无随访资料写成已观察复发或阴性随访的句子。预测时间与观察随访分别登记。 [私有时域核对](../output/g2_case_pilot_20261119/remaining/T3_time_semantics_review.json)

## 预算、资源与停止

本次只重建一次同资产 35B。新增 8 次非生成身份、模板及分词检查，和新增 13 次正式原生计数分账。首批正式原生计数累计 19 次，低于 37 次上限。首批累计 19 次真实请求、9 信息单位、88,324 预留 token。两例的每案请求、工具、策略轮和预留 token 均在冻结限额内。

北京时间 18:56:33–19:02:39，宿主流程耗时 6 分 06 秒，包含启动前资源及身份检查、模型启动、两例与收尾。单张 RTX 3090、单 slot、16,384 上下文和八 CPU 亲和核验通过。GPU 0 采样最高 21,696 MiB，服务进程峰值 RSS 21,835,824 kB。服务退出码 0，两卡恢复到启动前闲置占用，端口无监听，无本卡后台任务。 [服务回执](../output/g2_case_pilot_20261119/remaining/live/live_status.json) · [设备采样](../output/g2_case_pilot_20261119/remaining/gpu_samples.log)

首批三例完成，未添加或重跑病例。上一轮 T1 误停、原始证据、两路 Socket 和离线纠正原样保全。产物只留本地，未启动正式 validation。 [私有交接](../output/g2_case_pilot_20261119/remaining/handoff.json)
