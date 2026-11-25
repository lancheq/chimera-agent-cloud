# 本地 GT 评估报告 (eval_train_gt.py)

> 生成时间: 2026-09-27 12:38:58
> 预测目录: /home/hedongze/CHIMERA-agent/test/output.night_t1_base
> 评估方式: 直接 JSON 字段对比（不依赖 evaluate.py）

⚠️ 评估口径：predictor 在全量 GT 训练后评估同一批 case，分数为乐观上界。绝对能力以 GC 提交或 LOOCV 为准。

=== Task1 GT 评估（91 case）===

decision 正确率: 63/91 = 69.2%
  - yes 正确率: 54/56 (pred 80)
  - no 正确率: 9/35 (pred 11)
macro F1: 0.593
  - yes F1: 0.794
  - no F1: 0.391

confidence 匹配率: 48/91 = 52.7%
  - GT:  {'clear': 58, 'borderline': 18, 'uncertain': 15}
  - Pred: {'borderline': 14, 'clear': 67, 'uncertain': 10}
weights kappa: 0.279
reveal 非空率: 91/91 = 100.0%
free_text 非空率: 91/91 = 100.0%

=== Task2 GT 评估（72 case）===

  无匹配 prediction，跳过

=== Task3 GT 评估（仅 0 case，全量 73 case 待重跑）===

  无匹配 prediction，跳过

> T3 仅评 6 case，全量 75 case 评估待 P1-E Cox 改造 + task3 重跑后再做。当前 T3 评估价值在验证脚本正确性，不在指标数字。

=== 验收标准对比 ===

| 指标 | 当前值 | 目标 | 状态 |
|---|---|---|---|
| task1 macro F1（91 case） | 0.593 | ≥0.794 | ❌ |
| task2 macro F1（72 case） | 0.000 | ≥0.35 | ❌ |
| task3 C-index（仅 6 case） | 0.000 | ≥0.82 | ❌ |
