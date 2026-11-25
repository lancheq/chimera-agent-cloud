# GEval 评估报告 (rationale alignment)

> 生成时间: 2026-08-30 02:24:18
> Judge 模型: gemma4:e4b
> 预测目录: test/output.t1_full_v2_20260829/
> 评估方式: Ollama judge 模型近似评估（最终以 GC 提交为准）

=== Task1 GEval 评估（91 case）===

平均 rationale alignment: 0.754
  - 高分(≥0.8): 67 case (73.6%)
  - 中分(0.5-0.8): 15 case (16.5%)
  - 低分(<0.5): 9 case (9.9%)
  - 解析失败: 0 case

典型低分 case（前 5）:
  - PT-pseudo_0169468160c6: score=0.00, missing=[PIRADS, PSAD, DRE, cspca, age]
  - PT-pseudo_0930885168c9: score=0.00, missing=[PIRADS, PSA, PSAD, DRE, cspca]
  - PT-pseudo_d7d26761c714: score=0.00, missing=[PIRADS, PSA, PSAD, DRE, cspca]
  - PT-pseudo_b9aa0df93f2a: score=0.10, missing=[PIRADS, PSA, PSAD, DRE, cspca]
  - PT-pseudo_2cd88bcdf81c: score=0.20, missing=[PIRADS, PSAD, DRE, cspca, age]

典型高分 case（前 3）:
  - PT-pseudo_11574b12444b: score=1.00, covered=[PIRADS, PSA, PSAD, cspca, prostate_volume]
  - PT-pseudo_3477ea7065bd: score=1.00, covered=[PIRADS, PSA, PSAD, age, prostate_volume]
  - PT-pseudo_36c92fb424ac: score=1.00, covered=[PIRADS, PSA, PSAD, cspca, previous_biopsy]

=== Task2 GEval 评估（0 case）===

  无有效评分，跳过

=== Task3 GEval 评估（仅 0 case，全量待重跑）===

  无有效评分，跳过

=== 验收标准 ===

| 指标 | 当前值 | 目标 | 状态 |
|---|---|---|---|
| task1 GEval | 0.754 | 建立基线，P0-B 后提升 ≥0.05 | 基线已建立 |
| task2 GEval | 0.000 | 建立基线，P0-B 后提升 ≥0.05 | 基线已建立 |
| task3 GEval | 0.000 | 建立基线，P0-B 后提升 ≥0.05 | 基线已建立 |
| Ollama judge 可用率 | 0 错误 / 91 总 | 100% 跑通 | ✅ |
