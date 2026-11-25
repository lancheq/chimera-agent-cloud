# 本地 GT 评估报告 (eval_train_gt.py)

> 生成时间: 2026-08-29 10:16:16
> 预测目录: /root/shared-nvme/chimera-agent-baseline/test/output.ablation_A_atomic_20260829
> 评估方式: 直接 JSON 字段对比（不依赖 evaluate.py）

⚠️ 评估口径：predictor 在全量 GT 训练后评估同一批 case，分数为乐观上界。绝对能力以 GC 提交或 LOOCV 为准。

=== Task1 GT 评估（91 case）===

decision 正确率: 7/11 = 63.6%
  - yes 正确率: 3/6 (pred 4)
  - no 正确率: 4/5 (pred 7)
macro F1: 0.633
  - yes F1: 0.600
  - no F1: 0.667

confidence 匹配率: 3/11 = 27.3%
  - GT:  {'clear': 8, 'uncertain': 2, 'borderline': 1}
  - Pred: {'uncertain': 4, 'clear': 4, 'borderline': 3}
weights kappa: 0.360
reveal 非空率: 11/11 = 100.0%
free_text 非空率: 11/11 = 100.0%

=== Task2 GT 评估（72 case）===

decision 正确率: 5/9 = 55.6%
  - active_surveillance: tp=2, gt=2, pred=6
  - active_treatment: tp=2, gt=4, pred=2
  - continued_surveillance: tp=1, gt=2, pred=1
  - watchful_waiting: tp=0, gt=1, pred=0
macro F1: 0.458
  - active_surveillance F1: 0.500
  - active_treatment F1: 0.667
  - continued_surveillance F1: 0.667
  - watchful_waiting F1: 0.000

confidence 匹配率: 1/9 = 11.1%
  - GT:  {'clear': 7, 'borderline': 2}
  - Pred: {'uncertain': 6, 'clear': 1, 'borderline': 2}
weights kappa: 0.335
reveal 非空率: 9/9 = 100.0%
free_text 非空率: 9/9 = 100.0%

=== Task3 GT 评估（仅 8 case，全量 73 case 待重跑）===

event 正确率: 5/8 = 62.5%
months MAE: 23.18
C-index: 0.705
reveal 非空率: 8/8 = 100.0%
free_text 非空率: 8/8 = 100.0%

> T3 仅评 6 case，全量 75 case 评估待 P1-E Cox 改造 + task3 重跑后再做。当前 T3 评估价值在验证脚本正确性，不在指标数字。

=== 验收标准对比 ===

| 指标 | 当前值 | 目标 | 状态 |
|---|---|---|---|
| task1 macro F1（91 case） | 0.633 | ≥0.794 | ❌ |
| task2 macro F1（72 case） | 0.458 | ≥0.35 | ✅ |
| task3 C-index（仅 6 case） | 0.705 | ≥0.82 | ❌ |
