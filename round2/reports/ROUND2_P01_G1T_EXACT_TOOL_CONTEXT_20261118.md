# P01-G1T 工具与结构请求精确计数核查

[公开版省略逐例记录；汇总与机制结论保留。]

**精确模板计数接线为 `BLOCKED`。** 已登记的 8765 和 8766 均拒绝 `GET /props` 连接，缺少当前服务身份及原生 token 回执。现有拒绝边界继续生效，生产配置、运行时代码和注册表保持原样。真实生成 0，非生成探针尝试 2/30，成功 0，CPU tokenizer 调用 0。

## 已复核的事实

- 本机源码的 chat、apply-template 和原生 input_tokens 路由共用模板解析。文本 chat 和原生计数启用 `add_special=true, parse_special=true`，旧 guard 两步法用 false/true。本机 GGUF 的 add_bos_token 为 false，尚无该实际部署的 token 对照回执，不能据此断言 token 数一致或存在固定差值。GGUF 的主模板摘要与历史两个端点相同，历史每 slot 16384 仅作接续依据。
- 收到的 SDK JSON 保留工具定义、assistant call 的 ID 和完整参数、tool 结果的匹配 ID、名称及正文。普通填表的 assistant `{` 前缀及无前缀回退分别留档。共享控制的初始和后续 JSON schema 也分别留档，后者保留 `enable_thinking=false` 和 0 推理预算。拟提交控制器和工具选择保持待规划。
- **结构输出还有独立的 SDK 阻塞。** 当前 LangChain 的 `response_format` 走 root parse，G1A 严格运行时在发送前明确拒绝，连计数边界也未到达。结构形状由现有旧研究路径经内存模拟 SDK 捕获。所读 llama.cpp 源码从 wrapper 读取嵌套 schema，wrapper 的 `strict` 和 `name` 没有独立执行逻辑。实际结构语义仍须核对。
- **计量摘要还须绑定 SDK 序列化后的请求。** 已复现 `extra_body` 可改写顶层字段。诊断夹具中 G1A 边界读到最大输出 12，最终 HTTP JSON 为 17。现有正常形状的推理参数也由这个合并层展开。预检须钉住有效字段，候选上限须显式冻结。当前配置的工具、填表形状捕获为 8192，控制夹具为 1024，均按各自实际请求记录。

## 验证和保全

8 种请求形状及 11 种发送前拒绝通过具名合成检查，另有 13 项已有针对性回归通过。上下文及总额超限、错误 role 和 tool ID、未知选项、身份不匹配、端点故障均在模拟发送前终止。工具 ID 用例验证现有整体拒绝，尚未验收独立的 ID 验证器。合成通过的预算数是夹具值，真实精确 count 和 fit 栏保持空。请求记录来自进程内 HTTP 模拟，未送到端点。单进程专项采样峰值 1 线程、约 353 MiB，受限回归在同一环境串行运行。

已按 G1R 清单接续，核对 66 项产物、8 项改后源码或测试、6 项改前副本和 7 项配置输入。G1A/G1R 原交付、Git HEAD 和 staged 状态均保持。运行期间观察到 AGENTS 中的派发说明更新和新派发辅助脚本，本任务复读规则并保留这些外部变动。G1T 的生产源码差异为空。未启服务或部署后端，未调用聊天生成、加载模型权重或开展 GPU 任务。工作已停止，无后台任务。

## 规划复核要点

待已有服务及进程身份可核后，用同一份完整 HTTP JSON 对照本地源码已定位的原生计数路由和 apply-template/tokenize。通过后再接序列化边界、有效最大输出和 PrivateRuntime 回执。结构 SDK 路径单独验收。现阶段不进入 G2。

[交接](../output/p01_g1t_exact_tool_context_20261118/handoff.json) · [源码定位与最小接线方案](../output/p01_g1t_exact_tool_context_20261118/source_and_adapter.md) · [形状与预算矩阵](../output/p01_g1t_exact_tool_context_20261118/shape_matrix.json) · [探针回执](../output/p01_g1t_exact_tool_context_20261118/probe_receipts.jsonl) · [版本与证据清单](../output/p01_g1t_exact_tool_context_20261118/manifest.json)
