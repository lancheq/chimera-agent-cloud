# TASK-0831-NIGHT Pro 口径全量报告

> 生成时间: 2026-08-31
> 配置: self_refine=true + atomic_check=true (S2 +7.4pp 判定)
> 模型: Qwen3.6-35B-A3B-UD-Q4_K_M (llama.cpp server, GPU)
> 总 case 数: 236 (T1=91 + T2=72 + T3=73)

## 1. 三任务 Pro 全量数字 + vs false 口径对比

### 本地 GT 评估 (eval_train_gt.py)

| 指标 | T1 (91) | T2 (71) | T3 (73) |
|---|---|---|---|
| Decision/Event accuracy | 60.4% (55/91) | 59.2% (42/71) | 82.2% (60/73) |
| Macro F1 | 0.577 | 0.444 | n/a |
| C-index | n/a | n/a | 0.786 |
| Confidence kappa | 0.293 | n/a | n/a |
| Failed cases | 0 | 1 (T2-106) | 0 |

### vs false 口径对比

| 任务 | false acc | Pro acc | 变化 | false C-idx | Pro C-idx | 变化 |
|---|---|---|---|---|---|---|
| T1 | 61.5% | 60.4% | -1.1pp | n/a | n/a | n/a |
| T2 | 48.0% | 59.2% | +11.2pp | n/a | n/a | n/a |
| T3 | n/a | n/a | n/a | 0.717 | 0.786 | +6.9pp |

### T1 混淆矩阵

| | Pred yes | Pred no |
|---|---|---|
| GT yes (56) | TP=39 | FN=17 |
| GT no (35) | FP=19 | TN=16 |

## 2. Refine 生效全景

| 任务 | Total | Critique 触发 | Critique 率 | 收敛 | 无问题 | 污染 | τ触发 |
|---|---|---|---|---|---|---|---|
| T1 | 91 | 25 | 27.5% | 11 | 66 | 0 | n/a |
| T2 | 72 | 36 | 50.0% | 10 | 35 | 0 | n/a |
| T3 | 73 | 4 | 5.5% | 2 | 69 | 0 | 26 |
| 合计 | 236 | 65 | 27.5% | 23 | 170 | 0 | 26 |

Critique 发现的主要问题类型:
- fabricated_facts（编造 PSA 值/病灶大小）
- internal_inconsistency（PSAD 计算矛盾）
- confidence_miscalibration（过度自信）
- treatment_biopsy_confusion（T2 任务类型混淆）
- under_treatment（PI-RADS 4 推荐监测而非活检）
- unsupported_weights（PSA velocity 误算）
- guideline_misinterpretation

## 3. 官方 evaluate.py 6 组件完整表

| 组件 | T1 (91) | T2 (71) | T3 (73) |
|---|---|---|---|
| Decision accuracy | 0.604 | 0.648 (42+8 partial) | n/a |
| Decision F1 | yes: 0.684 | macro: 0.444 | n/a |
| Confidence kappa | -0.015 | -0.030 | n/a |
| Variable-weight kappa | 0.499 | 0.317 | n/a |
| Section grounding | 0.910 | 0.961 | n/a |
| Event accuracy | n/a | n/a | 0.822 |
| Time score | n/a | n/a | 0.562 |
| Final case score | 0.375 | 0.367 | 0.692 |

## 4. Docker 3 门槛复核 (T3)

| 门槛 | 阈值 | 实际 | 状态 |
|---|---|---|---|
| FP rate | <30% | 17.8% (13/73) | ✅ PASS |
| Event accuracy | >60% | 82.2% | ✅ PASS |
| C-index | >0.6 | 0.786 | ✅ PASS |

## 5. 反泄漏护栏

| 任务 | "Predictor loaded" 命中 | 状态 |
|---|---|---|
| T1 | 0 | ✅ PASS |
| T2 | 0 | ✅ PASS |
| T3 | 0 | ✅ PASS |

## 6. 遗留项

1. Docker 构建冒烟（下一步）
2. Q3 方案（打包期）
3. Confidence kappa≈0（提交后优化）
4. GEval (gemma4:e4b) 未跑（gemma server 未启动，确定性评分已覆盖核心指标）
5. T2-106 失败（form_fill parse 失败，1.4% < 15% 阈值）

## 7. 声明

- 未改 prompt / τ / schema.py / P5/P6/S1/self_refine.py 代码
- 未 push git
- 各 output 目录独立 (test/output.t{1,2,3}_full_pro_20260831/)
- commit 历史干净 (43a2fb2: enable self_refine=true + atomic_check=true)
- 35B server 启动方式正确 (llama_cpp.server, 非 start_qwen_server)
- predictor 双关闭 (enabled=false + decision_override_tasks=[])
- 全新 output 目录, CHIMERA_OUTPUT_DIR 显式导出
- critique 污染 0% (S1 修复生效)
