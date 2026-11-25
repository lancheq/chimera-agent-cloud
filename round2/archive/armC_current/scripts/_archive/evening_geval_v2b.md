# GEval 评估报告 (rationale alignment)

> 生成时间: 2026-08-29 19:24:18
> Judge 模型: gemma4:e4b
> 预测目录: test/output.t3_full_tau_v2b_20260829/
> 评估方式: Ollama judge 模型近似评估（最终以 GC 提交为准）

=== Task1 GEval 评估（0 case）===

  无有效评分，跳过

=== Task2 GEval 评估（0 case）===

  无有效评分，跳过

=== Task3 GEval 评估（仅 73 case，全量待重跑）===

平均 rationale alignment: 0.806
  - 高分(≥0.8): 61 case (83.6%)
  - 中分(0.5-0.8): 11 case (15.1%)
  - 低分(<0.5): 1 case (1.4%)
  - 解析失败: 0 case

典型低分 case（前 5）:
  - T3-066: score=0.20, missing=[Gleason_primary, ISUP_grade, pathological_stage, surgical_margins, seminal_vesicle_invasion]

典型高分 case（前 3）:
  - T3-011: score=1.00, covered=[ISUP_grade, pathological_stage, surgical_margins, extraprostatic_extension, seminal_vesicle_invasion]
  - T3-015: score=1.00, covered=[PSA, Gleason_primary, ISUP_grade, pathological_stage, surgical_margins]
  - T3-025: score=1.00, covered=[PSA, Gleason_primary, ISUP_grade, pathological_stage, surgical_margins]

=== 验收标准 ===

| 指标 | 当前值 | 目标 | 状态 |
|---|---|---|---|
| task1 GEval | 0.000 | 建立基线，P0-B 后提升 ≥0.05 | 基线已建立 |
| task2 GEval | 0.000 | 建立基线，P0-B 后提升 ≥0.05 | 基线已建立 |
| task3 GEval | 0.806 | 建立基线，P0-B 后提升 ≥0.05 | 基线已建立 |
| Ollama judge 可用率 | 0 错误 / 73 总 | 100% 跑通 | ✅ |
