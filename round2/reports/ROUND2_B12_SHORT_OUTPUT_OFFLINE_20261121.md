# B12 短结构化决策零生成验收

2026-11-21 任务卡，2026-11-22 北京时间完成。B12-S1 双臂通过离线请求边界与输出合同验收，待规划独立验收。本轮的输出来自合成 HTTP 桩，真实 35B 消费待验证。 [当前任务卡](../discussion/tasks/20261121_B12_SHORT_OUTPUT_OFFLINE.md) · [机器交接](../output/b12_short_output_20261121/handoff.json)

## 请求冻结与复用

逐项核对旧交接索引中的 21 项工件和 3 项上游合成依赖。旧冻结请求与实际 wire、HTTP、原生计数及模型响应在各自的审计链内一致。历史原样臂以 `stop` 结束，历史结构臂以 `length` 触顶结束。两条旧响应保留在原工件中，新载荷只由冻结请求、原 B12 注记和本轮共同合同构成。 [上一轮规划验收](ROUND2_BC_DIAGNOSIS_REPLAY_PLANNING_REVIEW_20261121.md) · [历史回读指纹](../output/b12_short_output_20261121/historical_readback.json)

新合同由一段附于原 user 消息末尾的共有指令和一个共有 JSON schema 组成。两臂先保留各自的冻结 user 内容，再加同一指令。撤去这两处共同增量后，可逐字段还原旧 HTTP JSON。原 system 内容、`system → user` 顺序、诊断模型、采样、种子、1,024 输出上限与 512 思考预算均经核对。唯一臂间差异仍是原 B1/B2 注记。沿用 OpenAI SDK 2.48.0 的计量 raw parse 通路，SDK 自动序列化 `stream: false`，并在 MockTransport 上核对实际请求字节、预算输入与有效 JSON 摘要。 [最小冻结载荷](../output/b12_short_output_20261121/frozen_payload.json) · [共有 schema](../output/b12_short_output_20261121/response_format_v1.json)

## 逐项离线验收

| 检查 | 结果 |
| --- | --- |
| 正例与来源顺序 | 输出先列原值和全部来源 ID，再列证据组数、独立观察数、冲突状态、趋势状态和一句决定理由。夹具中的 3 条来源指针对应 2 组证据和 1 项有证观察。两条 lab 回执的值可以同列，所有指针均保留。 |
| 完整未裁分歧 | 两个不同原值及归属均保留，冲突明确为 `same_observation_conflict_unresolved`，趋势为 UNKNOWN。一次观察内的差异无法支持时间趋势。 |
| 合法 UNKNOWN | 用同一份捕获响应做单独的解析测试，只在核验端撤去 lab 的合成观察身份凭证。该条件下 UNKNOWN 通过，编造观察数被拒绝。在原冻结凭证仍然成立的核验中，缺证版本的答案无法通过。 |
| 截断与格式 | 覆盖 `length` 下的完整 JSON 和半截 JSON、`stop` 触顶、`stop` 下的残缺 JSON、缺字段、重复键及字段乱序。截断直接记不完整，保留原 HTTP 响应、停止原因和适用的 SDK 异常，零补填、零重试。 |
| 溯源与越界 | 覆盖错误或遗漏来源、重复声明、原值错绑、精度改写、并成范围、回执多计、无据选真、把 UNKNOWN 改写成稳定、伪造上升趋势，以及未证指南和风险数字。 |
| 请求拒绝 | 单侧 schema 和共同事实变动在 SDK 调用前拦截。模拟超预算在计量阶段拒绝，三项均为零发送。 |

共 **27 个用例 × 2 臂**，54 次进程内 HTTP 捕获。逐项回执分别记录格式、源证据核验和实际模型消费状态。最后的决定理由采用三条通用短句构成的闭合集合，涵盖待核对、资料缺口和仅记录证据的处置。schema 及共有指令均未写入夹具值、来源 ID、正确计数或预选状态。 [用例](../output/b12_short_output_20261121/cases.py) · [解析协议](../output/b12_short_output_20261121/protocol.py) · [逐项回执与原始合成响应](../output/b12_short_output_20261121/acceptance_receipts.json)

## 实机接续门槛与资源

待规划验收和实机授权后，在可用宿主核对同一 GGUF、llama-server、原生模板、单 slot 和回环服务身份。对两份新载荷确认原生 schema 路径、字段顺序及精确模板分词，再核对实际 HTTP 请求和 usage。单臂预留上限 16,256，双臂 32,512。每臂一次，零重试。响应先留原件和停止原因，格式及语义分列。新合同在真实端的 grammar 行为、精确 token 开销及模型消费仍待该阶段验证。 [实机条件接续卡](../discussion/tasks/20261121_B12_SHORT_OUTPUT_LIVE_PAIR.md)

本轮回放限制一核。真实生成、原生端点请求、GPU、临床工具、病例和 GT 读取、下载均为零。实际回放耗时、峰值内存和用例及报告指纹记录在交接。C03 真实病例、B3、实机双臂及生产均保持 HOLD。无后台任务。 [资源及指纹回执](../output/b12_short_output_20261121/handoff.json)

从仓库根目录复核。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src taskset -c 0 \
  /home/hedongze/venvs/chimera_access/bin/python -B \
  output/b12_short_output_20261121/replay.py --check
```
