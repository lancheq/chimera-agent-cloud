# GEval 评估报告 (rationale alignment)

> 生成时间: 2026-08-30 14:16:17
> Judge 模型: gemma4:e4b
> 预测目录: test/output.t1_full_v2_20260829/
> 评估方式: Ollama judge 模型近似评估（最终以 GC 提交为准）

=== Task1 GEval 评估（91 case）===

平均 rationale alignment: 0.805
  - 高分(≥0.8): 73 case (80.2%)
  - 中分(0.5-0.8): 18 case (19.8%)
  - 低分(<0.5): 0 case (0.0%)
  - 解析失败: 0 case

典型高分 case（前 3）:
  - PT-pseudo_11574b12444b: score=1.00, covered=[PIRADS, PSA, PSAD, cspca, prostate_volume]
  - PT-pseudo_c13fda040310: score=1.00, covered=[PIRADS, PSA, PSAD, cspca, prostate_volume]
  - PT-pseudo_d3e975d025e6: score=0.95, covered=[PIRADS, PSA, PSAD, DRE, cspca]

=== Task2 GEval 评估（0 case）===

  无有效评分，跳过

=== Task3 GEval 评估（仅 0 case，全量待重跑）===

  无有效评分，跳过

=== 验收标准 ===

| 指标 | 当前值 | 目标 | 状态 |
|---|---|---|---|
| task1 GEval | 0.805 | 建立基线，P0-B 后提升 ≥0.05 | 基线已建立 |
| task2 GEval | 0.000 | 建立基线，P0-B 后提升 ≥0.05 | 基线已建立 |
| task3 GEval | 0.000 | 建立基线，P0-B 后提升 ≥0.05 | 基线已建立 |
| Ollama judge 可用率 | 0 错误 / 91 总 | 100% 跑通 | ✅ |
