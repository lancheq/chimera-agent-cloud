# GEval 评估报告 (rationale alignment)

> 生成时间: 2026-08-30 13:30:18
> Judge 模型: gemma4:e4b
> 预测目录: test/output.pro_v1_20260816/
> 评估方式: Ollama judge 模型近似评估（最终以 GC 提交为准）

=== Task1 GEval 评估（5 case）===

平均 rationale alignment: 0.900
  - 高分(≥0.8): 1 case (100.0%)
  - 中分(0.5-0.8): 0 case (0.0%)
  - 低分(<0.5): 0 case (0.0%)
  - 解析失败: 4 case

典型高分 case（前 3）:
  - PT-pseudo_033f1a8e1503: score=0.90, covered=[PIRADS, PSA, PSAD]

=== Task2 GEval 评估（5 case）===

平均 rationale alignment: 0.950
  - 高分(≥0.8): 2 case (100.0%)
  - 中分(0.5-0.8): 0 case (0.0%)
  - 低分(<0.5): 0 case (0.0%)
  - 解析失败: 3 case

典型高分 case（前 3）:
  - T2-003: score=1.00, covered=[PIRADS, PSA, PSAD, Gleason_primary, ISUP_grade]
  - T2-001: score=0.90, covered=[PIRADS, ISUP_grade, PSAD, PSA]

=== Task3 GEval 评估（仅 4 case，全量待重跑）===

  无有效评分，跳过

=== 验收标准 ===

| 指标 | 当前值 | 目标 | 状态 |
|---|---|---|---|
| task1 GEval | 0.900 | 建立基线，P0-B 后提升 ≥0.05 | 基线已建立 |
| task2 GEval | 0.950 | 建立基线，P0-B 后提升 ≥0.05 | 基线已建立 |
| task3 GEval | 0.000 | 建立基线，P0-B 后提升 ≥0.05 | 基线已建立 |
| Ollama judge 可用率 | 11 错误 / 14 总 | 100% 跑通 | ⚠️ |
