# 本地 GT 评估报告 (eval_train_gt.py)

> 生成时间: 2026-08-28 23:05:01
> 预测目录: /root/shared-nvme/chimera-agent-baseline/test/output.t3_l3light_tau_20260828
> 评估方式: 直接 JSON 字段对比（不依赖 evaluate.py）

⚠️ 评估口径：predictor 在全量 GT 训练后评估同一批 case，分数为乐观上界。绝对能力以 GC 提交或 LOOCV 为准。

=== Task1 GT 评估（91 case）===

  无匹配 prediction，跳过

=== Task2 GT 评估（72 case）===

  无匹配 prediction，跳过

=== Task3 GT 评估（仅 19 case，全量 73 case 待重跑）===

event 正确率: 13/19 = 68.4%
months MAE: 20.79
C-index: 0.643
reveal 非空率: 19/19 = 100.0%
free_text 非空率: 19/19 = 100.0%

> T3 仅评 6 case，全量 75 case 评估待 P1-E Cox 改造 + task3 重跑后再做。当前 T3 评估价值在验证脚本正确性，不在指标数字。

=== 验收标准对比 ===

| 指标 | 当前值 | 目标 | 状态 |
|---|---|---|---|
| task1 macro F1（91 case） | 0.000 | ≥0.794 | ❌ |
| task2 macro F1（72 case） | 0.000 | ≥0.35 | ❌ |
| task3 C-index（仅 6 case） | 0.643 | ≥0.82 | ❌ |
