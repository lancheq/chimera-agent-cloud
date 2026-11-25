# 本地 GT 评估报告 (eval_train_gt.py)

> 生成时间: 2026-08-27 09:29:24
> 预测目录: /root/shared-nvme/chimera-agent-baseline/test/output.lightweight_20260826
> 评估方式: 直接 JSON 字段对比（不依赖 evaluate.py）

⚠️ 评估口径：predictor 在全量 GT 训练后评估同一批 case，分数为乐观上界。绝对能力以 GC 提交或 LOOCV 为准。

=== Task1 GT 评估（91 case）===

decision 正确率: 13/20 = 65.0%
  - yes 正确率: 6/10 (pred 9)
  - no 正确率: 7/10 (pred 11)
macro F1: 0.649
  - yes F1: 0.632
  - no F1: 0.667

confidence 匹配率: 10/20 = 50.0%
  - GT:  {'clear': 13, 'borderline': 3, 'uncertain': 4}
  - Pred: {'clear': 10, 'uncertain': 9, 'borderline': 1}
weights kappa: 0.236
reveal 非空率: 20/20 = 100.0%
free_text 非空率: 20/20 = 100.0%

=== Task2 GT 评估（72 case）===

decision 正确率: 13/14 = 92.9%
  - active_surveillance: tp=5, gt=5, pred=6
  - active_treatment: tp=5, gt=6, pred=5
  - continued_surveillance: tp=2, gt=2, pred=2
  - watchful_waiting: tp=1, gt=1, pred=1
macro F1: 0.955
  - active_surveillance F1: 0.909
  - active_treatment F1: 0.909
  - continued_surveillance F1: 1.000
  - watchful_waiting F1: 1.000

confidence 匹配率: 4/14 = 28.6%
  - GT:  {'borderline': 3, 'clear': 11}
  - Pred: {'clear': 5, 'borderline': 5, 'uncertain': 4}
weights kappa: 0.258
reveal 非空率: 14/14 = 100.0%
free_text 非空率: 14/14 = 100.0%

=== Task3 GT 评估（仅 15 case，全量 73 case 待重跑）===

event 正确率: 10/15 = 66.7%
months MAE: 14.75
C-index: 0.614
reveal 非空率: 15/15 = 100.0%
free_text 非空率: 15/15 = 100.0%

> T3 仅评 6 case，全量 75 case 评估待 P1-E Cox 改造 + task3 重跑后再做。当前 T3 评估价值在验证脚本正确性，不在指标数字。

=== 验收标准对比 ===

| 指标 | 当前值 | 目标 | 状态 |
|---|---|---|---|
| task1 macro F1（91 case） | 0.649 | ≥0.794 | ❌ |
| task2 macro F1（72 case） | 0.955 | ≥0.35 | ✅ |
| task3 C-index（仅 6 case） | 0.614 | ≥0.82 | ❌ |
