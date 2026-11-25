# 本地 GT 评估报告 (eval_train_gt.py)

> 生成时间: 2026-09-04 18:19:01
> 预测目录: /root/shared-nvme/chimera-agent-baseline/test/output.s11_t1_newrag
> 评估方式: 直接 JSON 字段对比（不依赖 evaluate.py）

⚠️ 评估口径：predictor 在全量 GT 训练后评估同一批 case，分数为乐观上界。绝对能力以 GC 提交或 LOOCV 为准。

=== Task1 GT 评估（91 case）===

decision 正确率: 15/28 = 53.6%
  - yes 正确率: 10/17 (pred 16)
  - no 正确率: 5/11 (pred 12)
macro F1: 0.520
  - yes F1: 0.606
  - no F1: 0.435

confidence 匹配率: 12/28 = 42.9%
  - GT:  {'clear': 19, 'borderline': 6, 'uncertain': 3}
  - Pred: {'borderline': 3, 'clear': 15, 'uncertain': 10}
weights kappa: 0.326
reveal 非空率: 28/28 = 100.0%
free_text 非空率: 28/28 = 100.0%

=== Task2 GT 评估（72 case）===

  无匹配 prediction，跳过

=== Task3 GT 评估（仅 0 case，全量 73 case 待重跑）===

  无匹配 prediction，跳过

> T3 仅评 6 case，全量 75 case 评估待 P1-E Cox 改造 + task3 重跑后再做。当前 T3 评估价值在验证脚本正确性，不在指标数字。

=== 验收标准对比 ===

| 指标 | 当前值 | 目标 | 状态 |
|---|---|---|---|
| task1 macro F1（91 case） | 0.520 | ≥0.794 | ❌ |
| task2 macro F1（72 case） | 0.000 | ≥0.35 | ❌ |
| task3 C-index（仅 6 case） | 0.000 | ≥0.82 | ❌ |
