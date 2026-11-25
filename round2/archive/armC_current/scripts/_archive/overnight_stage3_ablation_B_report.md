# Stage 3 · 消融B 报告 (self_refine=true, atomic_check=true)

- 时间: 2026-08-29 13:38:01
- 输出: test/output.ablation_B_selfrefine_20260829 (30 case: T1:12+T2:10+T3:8)
- smoke gate: PASS (污染 0%, critique 机制正常) -> 执行全量
- HARD 告警: 2 case (PSAD 数值转录偏差, 已记录)
    [HARD] PT-pseudo_1dc32184cab6: PSAD 声称 0.02 但 PSA/36.0 = 0.021
  [HARD] T2-039: PSAD 声称 0.19 但 PSA/112.15 = 0.187

## 关键统计 (来自 trace.json reflect_warnings)
  traces=30
  critique_executed=30
  polluted=0
  no_issues=30
  refined=0
  other=0
  30x self_refine: no issues found (1 iteration)

## 结论
- 详见 scripts/eval_train_gt_report_ablation_B_selfrefine_20260829.md
- A vs B 对比见最终汇总 scripts/overnight_final_summary.md
