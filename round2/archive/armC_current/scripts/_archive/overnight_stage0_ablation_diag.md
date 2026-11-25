# Stage 0C · 消融 trace 审计（Phase A 诊断）

- 日期: 2026-08-29
- 层级: L1（离线审计，0 推理）
- 数据源: `test/output.ablation_selfrefine/trace/*/critique_iter1.json` (旧消融, 8/26 运行)

## 1. thinking 污染率

| 指标 | 结果 |
|------|------|
| 污染率 | **100% (30/30)** |
| 污染定义 | critique_iter1.json 内容含 `<think>` 标签 |

- 30/30 个 critique_iter1.json 均包含 `<think>` 标签 → **100% 污染**
- 结论: `_parse_critique_json` 未能剥离模型思考标签, critique 解析被污染

## 2. issues_count / refine_executed

- critique_iter1.json 中未见 `issues_count` / `refine_executed` 顶层字段
- 需在 Stage 3 smoke 中确认解析结构（trace.json 的相应字段）

## 3. 旧消融 server 启动方式确认

- `ablation_run.log`: 仅含编排记录（8/26 21:49 Config A 启动, 22:37 完成; Config B 至 23:38），无 server 启动命令
- `data_backup/logs_archive/full_run.log` (8/11): `Loading OpenAI-compatible model Qwen3.6-35B-A3B-UD-Q4_K_M.gguf from http://127.0.0.1:8765/v1`
- `scripts/start_qwen_server.sh.disabled`: 同样使用 `python -m llama_cpp.server --n_gpu_layers -1 --n_ctx 16384`，与当前正确启动方式一致
- **判定**: 旧消融 server 与当前 server 为**同一启动方式**（llama_cpp.server:8765，相同参数）。critique 100% 污染**不能归因于 server 差异**（start_qwen_server.sh 与 llama_cpp.server 均指向同一 llama_cpp.server 栈）

## 4. Stage 3 gate 影响

- gate 判据: 污染<50% 且 refine 触发率>0 → 继续 3b
- 预期: 因污染源为模型输出中的 `<think>` 未被解析剥离（server 无关），**正确 server 预计无法解决 critique 污染**
- 但需实测验证（smoke 3 case）——若污染仍 ≥50%，记录"正确 server 仍未解决 critique 污染，需修 _parse_critique_json（deferred）"，跳至 Stage 4

## 5. 声明

- 未改任何代码 / 未改 τ / 未改 config 默认值
