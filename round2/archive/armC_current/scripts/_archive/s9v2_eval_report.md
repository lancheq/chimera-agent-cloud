# Step9-v2 自动评估报告

> 生成: 2026-08-29 18:33:59
> 预测目录: test/output.t3_full_tau_v2b_20260829

## 1. Run 统计
- 完成 case: 76 / 76（全池，含 3 无 GT）
- 失败: 11
- 末 5 行日志:
```
2026-08-29 18:20:54,990 chimera_agent_baseline.agent.graph INFO plan_node: T3-mock-001 executed 2 tools: ['get_mri_report', 'get_surgical_pathology_report']
2026-08-29 18:20:59,956 chimera_agent_baseline.agent.form_fill INFO form_fill: case=T3-mock-001 task=3 called_tools=['get_mri_report', 'get_surgical_pathology_report'] eligible_vars=[] predictor=None self_refine=False atomic_check=False
2026-08-29 18:22:04,036 __main__ INFO Task 3: case T3-mock-001 done in 88.3s
2026-08-29 18:22:04,039 __main__ INFO Task 3: wrote 76 predictions (0 skipped, 0 failed) under test/output.t3_full_tau_v2b_20260829/task3
2026-08-29 18:22:04,040 __main__ INFO Done. Wrote 76 predictions across 1 task(s).
```

## 2. Monitor 统计

## 3. 反泄漏护栏（4 项）
- a. event=1 months 非 round 计数:（应为 0）
  event=1 总数: 17
  非 round: 0 
- b. 日志 'Predictor loaded' 命中: 0（应为 0）
- c. warnings 'decision override' 计数:（应为 0）
  override=0  tau触发=26  event_calibrated=2  months_fallback=2
- d. 启动参数（ps 已验证: enabled=false + decision_override_tasks=[]）

## 4. 官方 GT 评估

[报告已写入] /root/shared-nvme/chimera-agent-baseline/scripts/eval_train_gt_report_t3_full_tau_v2b_20260829.md
[错误 case 列表已写入] /root/shared-nvme/chimera-agent-baseline/scripts/decision_errors_t3_full_tau_v2b_20260829.json
  Task1 错误: 91 case
  Task2 错误: 72 case
  Task3 错误: 16 case

完成。

## 5. τ 不变式
  event=1: 17 case; 不变式违反: 0 

## 6. 偏置专项（混淆矩阵 + FP率 + GT0 months 分布）
  混淆: TP=9 FP=6 TN=48 FN=10 (n=73)
  EventAcc: 78.1%
  FP率(GT0=54): 11.1%
  GT0中pred=1的months: [12.0, 12.0, 18.0, 18.0, 18.0, 18.0]

## 7. Docker 3 门槛裁定（重放预期 FP13.0%/EventAcc76.7%/C-index 0.705）
(以官方 eval 报告数字对照)
