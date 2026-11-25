# B12-SHORT-OUTPUT-LIVE · 短格式双臂消费复验

2026-11-21 任务卡，2026-11-22 北京时间执行。**一对无患者合成双臂已完成。两臂均通过请求绑定核验，输出格式 2/2 通过，完整语义 0/2 通过。整体为部分通过。** [任务卡](../discussion/tasks/20261121_B12_SHORT_OUTPUT_LIVE_PAIR.md) · [冻结输入](../output/b12_short_output_live_20261121/frozen_inputs.json)

## 冻结绑定与真实发送

独立规划验收记录已登记为 27 用例 × 2 臂通过。核对零生成交付、上一轮的 21 项工件及上游合成夹具指纹。两条新请求只处理 B12-S1，同用冻结短格式、同一 JSON schema、既有 Qwen3.6-35B-A3B-UD-Q4_K_M、采样、种子、1,024 输出上限与 512 思考预算。臂间唯一输入差异为原 B1/B2 注记。 [项目验收记录](../discussion/CURRENT.md:13) · [零生成报告](ROUND2_B12_SHORT_OUTPUT_OFFLINE_20261121.md)

在空闲 GPU1 启动一次本机 35B。实际二进制、GGUF、模板及 16,384 单 slot 身份通过。八次非生成检查包含两臂的原生计数、模板及分词校验。故意无效的 schema 经同一非生成解析路由以 400 拒绝。两次实际调用各重新计数，SDK、发送的 HTTP JSON 字节、响应 usage 和审计摘要均逐条绑定。响应在 SDK 解析前原样落盘。 [实机预检](../output/b12_short_output_live_20261121/native_preflight.json) · [非生成 schema 反例](../output/b12_short_output_live_20261121/native_schema_negative.json)

| 双臂 | SDK 发送 | 原生输入 tokens | 输出 tokens | 预留 tokens | finish_reason | 格式 |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| 原样 | 1 | 1,553 | 672 | 2,577 | stop | 通过 |
| B12 结构 | 1 | 1,621 | 669 | 2,645 | stop | 通过 |
| 合计 | 2 | 3,174 | 1,341 | 5,222 | 均未触顶 | 2/2 通过 |

SDK 重试、人工重试和临床工具请求均为 0。后执行臂 usage 报告 1,057 个缓存输入 token，前臂为 0。该执行顺序不用于比较速度。 [原样臂原始响应](../output/b12_short_output_live_20261121/baseline/raw_response.json) · [B12 臂原始响应](../output/b12_short_output_live_20261121/B12_structure/raw_response.json)

## 语义逐项验收

| 读数 | 原样 | B12 结构 |
| --- | --- | --- |
| 原值与来源 | 通过。保留 5→S1 和 6→S2/S3，无缺失或错绑 | 同左 |
| 重复 lab 回执呈现 | 同属一个测量行 | 同左 |
| 证据组数，夹具为 2 | 报 1，首个语义失配 | 报 2，通过 |
| 独立观察数，夹具为 1 | 报 1，通过。与同一输出的不同观察分类不一致 | 报 1，通过 |
| 同次观察的未裁冲突 | 报不同观察，有误 | 报 UNKNOWN，首个语义失配。合成凭证已能证明同次观察未裁冲突 |
| 时间趋势，夹具为 UNKNOWN | 报上升，超出证据 | 报 UNKNOWN，通过 |
| 选真、风险和指南 | 保留两值并推迟决策。未出现选择真值、风险数字或指南归因 | 同左 |

结构臂在这对短格式输出中报对组数，并避免了无依据的上升趋势。两臂都未通过完整语义。结构臂剩余失配是对已证明的同次未裁冲突作出正确分类。首个错误与未经改写的两份结果均保留，无补填。 [双臂逐字段回读](../output/b12_short_output_live_20261121/paired_review.json)

上轮长格式的结构臂触顶并截断，本轮短格式两臂均正常结束。两轮共同格式和提示不同，仅登记描述性变化。单对合成结果限于所见模型输出。C03、B3、真实病例、完整 Adaptive、填表、Socket、生产及正式 validation 未运行。

## 释放与交接

北京时间 2026-11-22 10:37:47–10:39:16 完成宿主流程。单 slot、单并发，CPU 亲和限制 8 核。自启服务的 GPU 墙时 26.816 秒，退出码 0。GPU1 恢复到 12 MiB 闲置占用，回环端口已释放。GPU0 既有任务的 PID 和采样显存前后一致。无本卡后台任务。 [运行及释放回执](../output/b12_short_output_live_20261121/runtime_status.json) · [私有交接](../output/b12_short_output_live_20261121/handoff.json)
