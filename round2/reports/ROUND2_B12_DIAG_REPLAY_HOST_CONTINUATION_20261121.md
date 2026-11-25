# BC-DIAG-REPLAY · B12-S1 双臂宿主接续

限服务器本地审查。接续只涉及冻结的 B12-S1 无患者合成夹具。原候选、停点、请求、模板及回执指纹逐项核对。将封存入口和依赖复制到隔离工作目录并核对 SHA。C03 继续 HOLD。本次没有接触其病例原件。 [接续卡](../discussion/tasks/20261121_B12_DIAG_REPLAY_HOST_CONTINUATION.md) · [首次停点](ROUND2_BC_DIAGNOSIS_REPLAY_PILOT_20261121.md) · [宿主门槛](../output/bc_diagnosis_replay_pilot_20261121/b12_host_continuation/host_continuation_gate.json)

两臂保留同一组可见合成事实、两个原值及三条来源指针。处理臂只附加 B1/B2 的同次观察未裁冲突和重复来源结构。采样、种子、模板和 1,024 输出上限一致。病例读取、历史模型回答、真实患者工具请求、生产诊断、planner、填表及 Socket 均未运行。

## 真实模型与计量

复用已核 Qwen3.6-35B-A3B-UD-Q4_K_M、原 GGUF、原二进制和原模板。在空闲 GPU1 启动一次单 slot 服务。7 次非生成检查回读身份、两臂原生计数及模板和分词一致性。真实发送各一次，两个实际 native count、SDK 带入的有效 JSON、HTTP 请求摘要和后端 usage 逐条匹配。 [实机回执](../output/bc_diagnosis_replay_pilot_20261121/b12_host_continuation/live/runtime_status.json) · [请求、完整响应与 usage](../output/bc_diagnosis_replay_pilot_20261121/b12_host_continuation/actual_request_and_response_readback.json)

| 双臂 | 真实发送 | 原生输入 tokens | 输出 tokens | 预留 tokens | 停止状态 |
| --- | ---: | ---: | ---: | ---: | --- |
| 基线 | 1 | 1,395 | 1,006 | 2,419 | 正常结束 |
| B12 结构 | 1 | 1,463 | 1,024 | 2,487 | 达输出上限 |
| 合计 | 2 | 2,858 | 2,030 | 4,906 | SDK 重试 0，临床工具 0 |

## 诊断消费

| 读数 | 基线 | B12 结构 |
| --- | --- | --- |
| 两条原值和来源 | 都保留，两个相同 lab 值分列 | 都保留，来源表将 S2/S3 并为一组 |
| 同次观察的未裁冲突 | 可见输出未明确识别 | 可见输出写成 5.0–6.0 的范围，未明确识别。截断后的内容为 UNKNOWN |
| 重复票据与时间推断 | 写成约 6.0 的稳定趋势，夹具未提供纵向根据。是否在内部重复加权为 UNKNOWN | 写既往趋势为 UNKNOWN。可见部分未将相同 lab 值再呈现为第二份证据。内部加权未知 |
| 是否无依据选真 | 未显式裁定原值，稳定措辞偏向约 6.0 | 未显式裁定原值，范围措辞仍可能掩盖分歧性质 |

结构臂在来源表和时间不确定性措辞上出现可见变化。完整的“同次观察未裁冲突”消费未得到证实。两臂的可见建议措辞也有变化，处理臂完整建议受截断限制，无法比较。夹具没有提供用于核实输出中具体指南、风险数字和适用条件的依据。单对合成回放可界定上述文字差异，诊断正确率、医学收益及跨病例表现未测。 [逐项诊断回读](../output/bc_diagnosis_replay_pilot_20261121/b12_host_continuation/paired_diagnosis_review.json)

## 资源与收尾

北京时间 2026-11-21 23:47:50–23:49:52 完成宿主流程，实测自启服务 GPU 墙时 35.841 秒。单并发，CPU 亲和上限八核。服务退出码 0，GPU1 回到 12 MiB 闲置占用，两个相关端口均无监听。GPU0 的既有计算进程及采样显存占用前后一致。无本卡后台任务。旧工件保全，无追加病例、模型请求、调优、发表或平台操作。 [资源与释放](../output/bc_diagnosis_replay_pilot_20261121/b12_host_continuation/resources.log) · [私有交接](../output/bc_diagnosis_replay_pilot_20261121/b12_host_continuation/handoff.json)
