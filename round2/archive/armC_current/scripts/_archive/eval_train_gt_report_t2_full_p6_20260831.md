# 本地 GT 评估报告 (eval_train_gt.py)

> 生成时间: 2026-08-30 19:20:52
> 预测目录: /root/shared-nvme/chimera-agent-baseline/test/output.t2_full_p6_20260831
> 评估方式: 直接 JSON 字段对比（不依赖 evaluate.py）

⚠️ 评估口径：predictor 在全量 GT 训练后评估同一批 case，分数为乐观上界。绝对能力以 GC 提交或 LOOCV 为准。

=== Task1 GT 评估（91 case）===

  无匹配 prediction，跳过

=== Task2 GT 评估（72 case）===

decision 正确率: 47/72 = 65.3%
  - active_surveillance: tp=15, gt=25, pred=25
  - active_treatment: tp=24, gt=31, pred=33
  - continued_surveillance: tp=8, gt=14, pred=14
  - watchful_waiting: tp=0, gt=2, pred=0
macro F1: 0.480
  - active_surveillance F1: 0.600
  - active_treatment F1: 0.750
  - continued_surveillance F1: 0.571
  - watchful_waiting F1: 0.000

confidence 匹配率: 28/72 = 38.9%
  - GT:  {'clear': 58, 'borderline': 14}
  - Pred: {'borderline': 22, 'clear': 25, 'uncertain': 25}
weights kappa: 0.279
reveal 非空率: 72/72 = 100.0%
free_text 非空率: 72/72 = 100.0%

=== Task3 GT 评估（仅 0 case，全量 73 case 待重跑）===

  无匹配 prediction，跳过

> T3 仅评 6 case，全量 75 case 评估待 P1-E Cox 改造 + task3 重跑后再做。当前 T3 评估价值在验证脚本正确性，不在指标数字。

=== 验收标准对比 ===

| 指标 | 当前值 | 目标 | 状态 |
|---|---|---|---|
| task1 macro F1（91 case） | 0.000 | ≥0.794 | ❌ |
| task2 macro F1（72 case） | 0.480 | ≥0.35 | ✅ |
| task3 C-index（仅 6 case） | 0.000 | ≥0.82 | ❌ |
