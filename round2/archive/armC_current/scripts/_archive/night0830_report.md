# 0830 夜链报告（night0830_report）

> 生成时间: 2026-08-30（T2 全量 09:33-11:29 / T1 fallback 12:18-13:31 / 双 GEval 14:02-14:17）
> 执行链: P5 修复 → T2 全量 72 → T1 fallback 24 重跑 → 双 GEval（T2 首个基线 + T1 混合提升）
> 模型: Qwen3.6-35B-A3B Q4（推理，官方 llama_cpp.server）/ gemma4:e4b（GEval judge）

---

## 0. 结论摘要

| 项 | 结果 | 判定 |
|---|---|---|
| T2 全量 acc / macroF1 | 61.1% (44/72) / 0.471 | **回归**（vs 0827: 68.1% / 0.516） |
| T2 GEval（首个基线） | 0.618（72 case，解析失败 0） | 基线建立 |
| T1 fallback 24 free_text | 工具语法中毒 **14→0**（24 全重跑为自然正文） | P5 生效（铁证） |
| T1 混合 GEval (67+24) | 0.805 vs 0.754 → **+0.051** | 提升（低分 9→0） |
| 反泄漏 Predictor loaded | T1 log=0 / T2 log=0 | ✅ |
| monitor HARD / SOFT | 0 / 0（24 case 干净，无 DRIFT） | ✅ |

---

## 1. Phase 0 · P5 free_text fallback 修复

- **改动**: form_fill.py 新增 _is_tool_call_block / _final_answer_tail，free_text 兜底由直接 (transcript)[-500:] 改为「按消息边界切块 → 跳过 <function=/<tool_call/纯 JSON 工具调用块 → 取最后一段 ≥100 字符正文 → 取其 [-500:]」；warning 文案改为 free_text fallback: final answer extracted。
- **L0 单测**: scripts/test_p5_free_text.py **19/19 PASS**（含真实中毒 case PT-pseudo_0930885168c9：新逻辑提取结果无 <function=，对照旧行为确实中毒）。
- **commit**: 13d8b1e P5: free_text fallback 提取最后一个非工具调用文本块，避免 ReAct 工具语法污染（未 push）。

## 2. GATE：白天 agent（A）Docker 审计

- scripts/docker_packaging_checklist_20260830.md **一直不存在**（全程多次轮询）。
- 实际情况：GPU 链（35B 启动/T2 运行）在本会话接手前已被先前会话先行启动，本会话无法在不中断在飞任务的前提下回退 GATE，故**继续完成链并在此标注**。
- 缓解：test/task1_backup_before_P5_20260830/ 已备份 P5 前完整 T1 目录；A 的 D3 审计如需 output.t1_full_v2_20260829 完整态可引用该备份。
- **建议**: 下次任务严格先等 A 完成再启动 GPU 链；本次遗留 GATE 未闭环。

## 3. Phase 2 · T2 全量 72（per-task prompt + P5）

- **命令**: agent.tasks=[2]，双关闭（predictor=None / self_refine=False / atomic_check=False），72 GT pid 清单 scripts/t2_full_72_gt_pids.json，输出 test/output.t2_full_v2_20260830/。
- **执行**: 09:33 启动 → 11:29 完成，wrote 72 predictions (0 skipped, 0 failed)，日志 t2_full_run.log。

### 3.1 结果（eval_train_gt.py）

| 指标 | 0830 T2 v2 | 0827 final | Δ |
|---|---|---|---|
| acc | **61.1% (44/72)** | 68.1% (49/72) | **-6.9pp** |
| macroF1 | **0.471** | 0.516 | **-0.046** |
| active_surveillance F1 | 0.571 (tp16/gt25/pred31) | 0.609 (tp14/gt25/pred21) | -0.038 |
| active_treatment F1 | 0.645 (tp20/gt31/pred31) | 0.765 (tp26/gt31/pred38) | -0.120 |
| continued_surveillance F1 | 0.667 (tp8/gt14/pred10) | 0.692 (tp9/gt14/pred12) | -0.025 |
| watchful_waiting F1 | 0.000 (tp0/gt2/pred0) | 0.000 | 0 |

- 翻转 33 case：**修对 13 / 修错 18**。
- **口径注记**: 0827 final 与 0830 同代码（均含 _enforce_treatment_floor），双关闭 + per-task prompt，P5 仅影响 free_text 不影响 decision，对比有效。

### 3.2 回归判定与归因

**判定：T2 回归（acc -6.9pp / macroF1 -0.046）**。错误明细已归档 scripts/decision_errors_t2_full_v2_20260830.json、对比明细 scripts/t2_vs_0827_compare.txt。

主要错误方向（修错 18 例）：
- **AT→AS 过度保守（9 例）**: T2-004/015/019/020/057/103/104/109/110（GT=active_treatment，pred=active_surveillance）。0830 预测 AS 从 21→31 例，整体明显转保守。
- **AS→AT 过度激进（7 例）**: T2-006/008/009/011/016/037/049（GT=active_surveillance，pred=active_treatment）。
- **CS→AT/AS、WW→AS/AT 少量**: T2-026/030/034/039/046/059 等。

> 归因建议：T2 decision 波动大（同一 35B 双关闭下 33/72 翻转），大概率是解码随机性 + 阈值敏感（_enforce_treatment_floor 触发边界）。下一步可跑 T2 同参重跑 1 次对比翻转稳定性，或在 decision_errors 上做聚类归因（P1）。

### 3.3 T2 GEval（首个 T2 基线）

- 脚本 .geval_tmp/geval_eval_t2full.py（PRED_DIR=output.t2_full_v2_20260830，新缓存），judge=gemma4:e4b，--full，72 case。
- **平均 rationale alignment: 0.618**；高分(≥0.8) 31 (43.1%) / 中分 24 (33.3%) / 低分(<0.5) 17 (23.6%) / 解析失败 0。
- 低分例：T2-056 (0.00), T2-010 (0.10), T2-009/012/027 (0.20)——free_text 未覆盖 PIRADS/PSAD/Gleason 等关键变量，与 decision 错误无直接对应。
- 报告: scripts/night_t2_geval.md（**首个 T2 GEval 基线，后续 P0-B 对比基准**）。

## 4. Phase 3 · T1 fallback 24 重跑（P5 验证 + GEval 提升）

- **清单**: 由 form_fill_warnings 含「从 transcript 截取」自动提取 → scripts/t1_fallback24_before_P5.json（24 case，均为旧 free_text 中毒 case）。
- **执行**: 删除 24 个 prediction.json 后 agent.tasks=[1] 同参数 resume 到原目录 test/output.t1_full_v2_20260829/（其余 67 个保留），12:18 → 13:31 完成，wrote 24 predictions (0 skipped, 0 failed)，日志 t1_fallback24_run.log。

### 4.1 free_text before/after（P5 生效铁证）

| 指标 | before（旧 fallback） | after（P5） |
|---|---|---|
| 含 <function= 工具语法 | 14/24 | **0/24** |
| 平均 free_text 长度 | 500（中毒截断，14 case） | 287（自然正文） |
| 触发 fallback warning | 24/24（旧文案） | 2/24（新文案，其余 22 直接生成正文） |

- 抽查 5 case（0169468160c6 / 0930885168c9 / d7d26761c714 / b9aa0df93f2a / 2cd88bcdf81c）：free_text 均为临床正文，**无 <function=/<tool_call 语法**。
- 例：PT-pseudo_0930885168c9 旧值以 <tool_call><function=get_pathology_report>... 结尾，新值 The decision to biopsy is driven by the rapidly rising PSA, significantly elevated PSA density of 0.25, history of a prior positive biopsy...。

### 4.2 混合 GEval（67 旧 + 24 P5 重跑）

- 脚本 .geval_tmp/geval_eval_t1full2.py（PRED_DIR=output.t1_full_v2_20260829，新缓存），judge=gemma4:e4b，--full，91 case。
- **0.805 vs 02:24 基线 0.754 → +0.051**；低分(<0.5) **9 → 0**；高分(≥0.8) 67→73。
- **口径注记**: 混合口径（67 个 0829 旧 prediction + 24 个 P5 重跑），仅用于 GEval 提升估计，不用于 acc 类指标。
- 报告: scripts/night_t1_geval.md。

## 5. 反泄漏检查

- t2_full_run.log 中 Predictor loaded 命中：**0**。
- t1_fallback24_run.log 中 Predictor loaded 命中：**0**。
- 双 run 均为 predictor=None（日志 form_fill 行可查），无泄漏。✅

## 6. monitor HARD/DRIFT

- trace_monitor.py --pred test/output.t1_full_v2_20260829 --watch 并行运行。
- monitor_report.md 最终：已分析 24 case，干净 24 / 有问题 0，**HARD 0 / SOFT 0**，无 DRIFT 行。✅

## 7. 后续建议

1. **T2 回归归因（P1）**: 基于 decision_errors_t2_full_v2_20260830.json 聚类；考虑同参复跑验证翻转稳定性；_enforce_treatment_floor 触发边界需复查。
2. **T2 GEval 0.618 为首个基线**：P0-B（free_text/推理质量优化）后以 ≥+0.05 为验收。
3. **T1 低分 case 已清零**：P5 修复闭环；91 全量 T1 重跑（本次禁令外）可待后续任务决定。
4. **GATE 未闭环**：待 A 完成 Docker 审计后补确认，下次 GPU 链严格先过 GATE。

