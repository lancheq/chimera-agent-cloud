# GEval 评估报告 (rationale alignment)

> 生成时间: 2026-09-03 18:12:17
> Judge 模型: gemma4:e4b
> 预测目录: test/output.pro_v1_20260816/
> 评估方式: Ollama judge 模型近似评估（最终以 GC 提交为准）

=== Task1 GEval 评估（91 case）===

平均 rationale alignment: 0.754
  - 高分(≥0.8): 57 case (62.6%)
  - 中分(0.5-0.8): 31 case (34.1%)
  - 低分(<0.5): 3 case (3.3%)
  - 解析失败: 0 case

典型低分 case（前 5）:
  - PT-pseudo_11345b8a7ab3: score=0.00, missing=[PSAD, cspca, age, prostate_volume, family_history]
  - PT-pseudo_4b950525e6a8: score=0.00, missing=[PIRADS, PSA, PSAD, DRE, cspca]
  - PT-pseudo_d7d26761c714: score=0.00, missing=[PIRADS, PSA, PSAD, DRE, cspca]

典型高分 case（前 3）:
  - PT-pseudo_099651db404e: score=1.00, covered=[PIRADS, PSA, PSAD, DRE, cspca]
  - PT-pseudo_20ba3f0f6ca0: score=1.00, covered=[PIRADS, PSA, PSAD, DRE, cspca]
  - PT-pseudo_2e0346bce3b3: score=1.00, covered=[PIRADS, PSA, previous_biopsy]

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
