# 官方 evaluate.py 完整评分（0831 · N1-P3）

> 生成: 2026-08-30（TASK-0831-FULL N1-P3）
> 评估器: `CHIMERA-agent/evaluation/evaluate.py`（官方，本地直跑，非 Docker）
> 输入: convert_to_gc_format.py 转换的三任务 predictions.json + 自建 GT
> （data/taskN GT → 官方 pathologist_response 格式，scripts/prepare_official_gt.py，236 记录）
> 判分: 确定性组件官方口径；GEval/rationale 组件官方 deepeval 路径本机不可用
> （deepeval 4.x 与 evaluate.py 2.x 时代 API 不兼容卡死），改用项目自建
> gemma4:e4b 判分（scripts/geval_eval.py），口径已标注

## 三任务 6 组件总表

| 组件 | Task1 (91) | Task2 (72) | Task3 (73) |
|---|---|---|---|
| **decision gate** | acc 0.703（64/0/27）| acc 0.653（47/7/18）；含部分分 0.701 | event acc 0.781 |
| **confidence**（加权 kappa） | 0.019 | -0.019 | — |
| **weights**（加权 kappa） | 0.449 | 0.316 | — |
| **important/decisive** | 并入 weights（decisive 命中由 kappa 体现） | 同左 | — |
| **tool efficiency** | 0.000 ⚠️ | 0.000 ⚠️ | — |
| **grounding**（section 对齐） | 0.895 | 0.952 | — |
| **GEval**（rationale）* | 0.805 | 0.607 | 0.806 |
| **final DAG score** | 0.449 | 0.423 | 0.667 |
| 附加 | F1_yes 0.794 | macroF1 0.480 | C-index 0.728 / event1 MAE 14.23 月 / mean time 0.553 |

\* GEval 为项目自建 gemma4:e4b 判分：Task1/Task3 沿用 0830 产物
（t1_full_v2_20260829=0.805、t3_full_tau_v2b_20260829=0.806）；Task2 为本会话
P6 新输出（t2_full_p6_20260831）重跑=0.607（旧 0830 输出为 0.618）。

## 逐 task 明细

### Task1（test/output.t1_full_v2_20260829，91 case）
- DAG 0.449；decision acc 0.703（正确 64 / 部分 0 / 错 27）；F1_yes 0.794
- confidence kappa 0.019（pred 大量 uncertain：22 borderline/25 clear/25 uncertain vs GT 58 clear/14 borderline）
- weights kappa 0.449；grounding 0.895；tool 0.000（见下方说明）

### Task2（test/output.t2_full_p6_20260831，72 case）
- DAG 0.423；exact acc 0.653（47/72）；含部分分 acc 0.701（AS/CS 部分计分）
- macroF1 0.480；confidence kappa -0.019；weights kappa 0.316；grounding 0.952

### Task3（test/output.t3_full_tau_v2b_20260829，73 case）
- DAG 0.667；event acc 0.781；mean event 0.781；mean time 0.553
- C-index 0.728；event=1 的 MAE 14.23 个月

## 说明与口径

1. **tool efficiency 0.000**：自建 GT 的 reveal_sequence 为空（data/taskN GT
   reasoning 未含真实点击序列），官方 tool 组件无从对比 → 0。非模型缺陷，
   是 GT 映射局限（官方评审 GT 含点击 reveal）。
2. **GEval 用项目判分替代**：官方 deepeval GEval 在本机不可用（deepeval 4.2.0
   与 evaluate.py 编写时代的 2.x API 不兼容，判分挂起 15min 无输出）。已按
   「不强行适配」原则记录差异；GEval 数字以项目 gemma4:e4b 判分为准。
3. **决策口径**：Task2 exact acc 0.653 与 eval_train_gt.py 一致（47/72）——
   交叉验证通过；0.701 为官方含部分分口径（AS/CS 相似类 0.5 分）。
4. **GT 覆盖**：236 记录（91+72+73），task3 文件名按官方约定
   （prostate-time-to-recurrence-or-last-follow-up.json）。
5. 预测侧经 convert_to_gc_format.py 转 GC sockets（T1 91/T2 72/T3 76 jobs，
   T3 多出的 3 个无 GT 不参与评分）。

## 产物

- 结果: test/eval_official_20260831/{task1,task2,task3}/metrics.json + per_case_results.csv + evaluation_results_summary.json
- 转换: test/input_gc_t1p6|t2p6|t3p6/
- GT: test/official_gt_20260831/
