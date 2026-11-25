# 本地 GT 评估报告 (eval_train_gt.py)

> 生成时间: 2026-08-31 11:28:46
> 预测目录: /root/shared-nvme/chimera-agent-baseline/test/output.t2_full_pro_20260831
> 评估方式: 直接 JSON 字段对比（不依赖 evaluate.py）

⚠️ 评估口径：predictor 在全量 GT 训练后评估同一批 case，分数为乐观上界。绝对能力以 GC 提交或 LOOCV 为准。

=== Task1 GT 评估（91 case）===

  无匹配 prediction，跳过

=== Task2 GT 评估（72 case）===

decision 正确率: 42/71 = 59.2%
  - active_surveillance: tp=12, gt=25, pred=26
  - active_treatment: tp=23, gt=30, pred=36
  - continued_surveillance: tp=7, gt=14, pred=9
  - watchful_waiting: tp=0, gt=2, pred=0
macro F1: 0.444
  - active_surveillance F1: 0.471
  - active_treatment F1: 0.697
  - continued_surveillance F1: 0.609
  - watchful_waiting F1: 0.000

confidence 匹配率: 21/71 = 29.6%
  - GT:  {'clear': 57, 'borderline': 14}
  - Pred: {'uncertain': 28, 'clear': 19, 'borderline': 24}
weights kappa: 0.225
reveal 非空率: 71/71 = 100.0%
free_text 非空率: 71/71 = 100.0%

=== Task3 GT 评估（仅 0 case，全量 73 case 待重跑）===

  无匹配 prediction，跳过

> T3 仅评 6 case，全量 75 case 评估待 P1-E Cox 改造 + task3 重跑后再做。当前 T3 评估价值在验证脚本正确性，不在指标数字。

=== 验收标准对比 ===

| 指标 | 当前值 | 目标 | 状态 |
|---|---|---|---|
| task1 macro F1（91 case） | 0.000 | ≥0.794 | ❌ |
| task2 macro F1（72 case） | 0.444 | ≥0.35 | ✅ |
| task3 C-index（仅 6 case） | 0.000 | ≥0.82 | ❌ |
