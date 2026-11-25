# 本地 GT 评估报告 (eval_train_gt.py)

> 生成时间: 2026-08-27 22:21:30
> 预测目录: /root/shared-nvme/chimera-agent-baseline/test/output.final_20260826
> 评估方式: 直接 JSON 字段对比（不依赖 evaluate.py）

⚠️ 评估口径：predictor 在全量 GT 训练后评估同一批 case，分数为乐观上界。绝对能力以 GC 提交或 LOOCV 为准。

=== Task1 GT 评估（91 case）===

decision 正确率: 62/91 = 68.1%
  - yes 正确率: 46/56 (pred 65)
  - no 正确率: 16/35 (pred 26)
macro F1: 0.642
  - yes F1: 0.760
  - no F1: 0.525

confidence 匹配率: 48/91 = 52.7%
  - GT:  {'clear': 58, 'borderline': 18, 'uncertain': 15}
  - Pred: {'borderline': 12, 'uncertain': 18, 'clear': 61}
weights kappa: 0.304
reveal 非空率: 91/91 = 100.0%
free_text 非空率: 91/91 = 100.0%

=== Task2 GT 评估（72 case）===

decision 正确率: 49/71 = 69.0%
  - active_surveillance: tp=14, gt=25, pred=21
  - active_treatment: tp=26, gt=30, pred=38
  - continued_surveillance: tp=9, gt=14, pred=12
  - watchful_waiting: tp=0, gt=2, pred=0
macro F1: 0.516
  - active_surveillance F1: 0.609
  - active_treatment F1: 0.765
  - continued_surveillance F1: 0.692
  - watchful_waiting F1: 0.000

confidence 匹配率: 27/71 = 38.0%
  - GT:  {'clear': 58, 'borderline': 13}
  - Pred: {'borderline': 30, 'clear': 29, 'uncertain': 12}
weights kappa: 0.255
reveal 非空率: 71/71 = 100.0%
free_text 非空率: 71/71 = 100.0%

=== Task3 GT 评估（仅 73 case，全量 73 case 待重跑）===

event 正确率: 24/73 = 32.9%
months MAE: 26.90
C-index: 0.525
reveal 非空率: 73/73 = 100.0%
free_text 非空率: 73/73 = 100.0%

> T3 仅评 6 case，全量 75 case 评估待 P1-E Cox 改造 + task3 重跑后再做。当前 T3 评估价值在验证脚本正确性，不在指标数字。

=== 验收标准对比 ===

| 指标 | 当前值 | 目标 | 状态 |
|---|---|---|---|
| task1 macro F1（91 case） | 0.642 | ≥0.794 | ❌ |
| task2 macro F1（72 case） | 0.516 | ≥0.35 | ✅ |
| task3 C-index（仅 6 case） | 0.525 | ≥0.82 | ❌ |
