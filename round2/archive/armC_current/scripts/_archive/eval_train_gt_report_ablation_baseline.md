# 本地 GT 评估报告 (eval_train_gt.py)

> 生成时间: 2026-08-26 23:38:01
> 预测目录: /root/shared-nvme/chimera-agent-baseline/test/output.ablation_baseline
> 评估方式: 直接 JSON 字段对比（不依赖 evaluate.py）

⚠️ 评估口径：predictor 在全量 GT 训练后评估同一批 case，分数为乐观上界。绝对能力以 GC 提交或 LOOCV 为准。

=== Task1 GT 评估（91 case）===

decision 正确率: 8/12 = 66.7%
  - yes 正确率: 5/7 (pred 7)
  - no 正确率: 3/5 (pred 5)
macro F1: 0.657
  - yes F1: 0.714
  - no F1: 0.600

confidence 匹配率: 5/12 = 41.7%
  - GT:  {'clear': 9, 'uncertain': 2, 'borderline': 1}
  - Pred: {'borderline': 4, 'clear': 5, 'uncertain': 3}
weights kappa: 0.368
reveal 非空率: 12/12 = 100.0%
free_text 非空率: 12/12 = 100.0%

=== Task2 GT 评估（72 case）===

decision 正确率: 8/10 = 80.0%
  - active_surveillance: tp=2, gt=2, pred=4
  - active_treatment: tp=4, gt=5, pred=4
  - continued_surveillance: tp=2, gt=2, pred=2
  - watchful_waiting: tp=0, gt=1, pred=0
macro F1: 0.639
  - active_surveillance F1: 0.667
  - active_treatment F1: 0.889
  - continued_surveillance F1: 1.000
  - watchful_waiting F1: 0.000

confidence 匹配率: 2/10 = 20.0%
  - GT:  {'clear': 7, 'borderline': 3}
  - Pred: {'uncertain': 3, 'clear': 2, 'borderline': 5}
weights kappa: 0.337
reveal 非空率: 10/10 = 100.0%
free_text 非空率: 10/10 = 100.0%

=== Task3 GT 评估（仅 8 case，全量 73 case 待重跑）===

event 正确率: 4/8 = 50.0%
months MAE: 18.00
C-index: 0.591
reveal 非空率: 8/8 = 100.0%
free_text 非空率: 8/8 = 100.0%

> T3 仅评 6 case，全量 75 case 评估待 P1-E Cox 改造 + task3 重跑后再做。当前 T3 评估价值在验证脚本正确性，不在指标数字。

=== 验收标准对比 ===

| 指标 | 当前值 | 目标 | 状态 |
|---|---|---|---|
| task1 macro F1（91 case） | 0.657 | ≥0.794 | ❌ |
| task2 macro F1（72 case） | 0.639 | ≥0.35 | ✅ |
| task3 C-index（仅 6 case） | 0.591 | ≥0.82 | ❌ |
