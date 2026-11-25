# P01-G1T SDK 请求计量接续

2026-11-18 · GPT-6.2 Sol High · 本地交付，待规划审查

| 验收项 | 结果 |
| --- | --- |
| SDK 请求与预算等价 | **PASS（固定版本，合成）**。G1T 留存的 8 种形状中，7 种可接纳形状的计数输入与实际 SDK 编码 JSON 相等。12→17 碰撞在计数和 HTTP 发送之前拒绝 |
| 结构请求 SDK 路径 | **PASS（仅已证原生 raw parse）**。保留原生 `response_format` 字典、完整 schema 和解析器，涵盖同步和异步。结构与实际服务 grammar 待活体核验 |
| 真实精确计数与生产 | **BLOCKED**。本轮无真实生成、真实 tokenizer 探针、模型服务操作、模型权重加载、生产 profile 启用或 GPU 任务 |

## 计量边界

- 固定 OpenAI 2.48.0、HTTPX 0.28.1、LangChain OpenAI 1.4.1、LangChain Core 1.5.1、Pydantic 2.13.4。所读 SDK 的 `_base_client.py` 构造请求时将 `extra_body` 合并到顶层，并允许后者覆盖。适配只把已核、无重叠的附加参数展开到预算输入，保留原 SDK 调用参数。JSON schema 请求原生 parse 会生成 `stream=false`。预算输入包含这一实际值。
- `PrivateRuntime` 记录 SDK 参数摘要、归一化有效请求摘要、唯一有效输出上限并一次预留。OpenAI 发送前的 HTTPX 请求钩子核对方法、URL、Content-Type、无重复 JSON 键、有效载荷摘要，并记录真实序列化字节 SHA-256。计量器修改有效请求、HTTP 客户端或校验钩子被改、两摘要不符都拒绝。禁用 SDK 隐式重试及重定向。正常预留之后的编码或传输失败仍收费一次。非法参数、超限或预检拒绝不提交。
- 普通 `create` 及其 raw response 路径继续走同一账本。结构代理只暴露 `chat.completions.with_raw_response.parse`，原样委托所装 SDK 的同一个原生方法。验证保留正常内容、拒答、格式不合法的响应内容、长度截断、内容过滤和 HTTP 400 的结果或异常。字典 schema 的 malformed content 仍按原生行为返回给后续解析，并不视为通过 schema 校验。
- 形状覆盖工具首轮、带工具返回的历史、显式并行和选择设置、填表前缀及回退、planner 和 policy 的 schema。拒绝项包含顶层/附加参数冲突、多输出别名、`n_predict`、未知选项、未核模板参数、流式、未知角色和孤儿工具 ID。Pydantic 类、`json_object`、非严格 wrapper、schema 加 tools 和 Responses API 仍拒绝。

## 验证范围和保全

最终回归 198 项通过，含本轮专项 47 项和 G1A/G1R 及相关回归 151 项，保留 7 条既有序列化警告。专项用例覆盖冻结形状、模拟计数的可核预留、同步/异步原生解析对照和故障计量。计数数字均为测试夹具。HTTP 请求只到进程内 MockTransport。G1A/G1R 原始回执和 G1T 调查交付保留，Git HEAD 与 staged 状态保留。本次范围内的两份既有 SDK 源码受仓库 `models/` 忽略规则影响，已在独立源码 diff 中显式记录。

真实计数仍待现有服务身份可证后，用同一最终 JSON 对照 native input-token 路由以及渲染和分词回执。服务端 schema/grammar 支持也须独立核验。交付后停在本卡，无后台任务。

[交接](../output/p01_g1t_sdk_request_boundary_20261118/handoff.json) · [原始合成 HTTP 对照](../output/p01_g1t_sdk_request_boundary_20261118/http_comparisons.json) · [最终回归](../output/p01_g1t_sdk_request_boundary_20261118/final.log) · [源码 diff](../output/p01_g1t_sdk_request_boundary_20261118/source_diff.patch) · [版本及产物清单](../output/p01_g1t_sdk_request_boundary_20261118/manifest.json)
