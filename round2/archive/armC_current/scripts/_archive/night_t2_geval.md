# GEval 评估报告 (rationale alignment)

> 生成时间: 2026-08-30 14:12:07
> Judge 模型: gemma4:e4b
> 预测目录: test/output.t2_full_v2_20260830/
> 评估方式: Ollama judge 模型近似评估（最终以 GC 提交为准）

=== Task1 GEval 评估（0 case）===

  无有效评分，跳过

=== Task2 GEval 评估（72 case）===

平均 rationale alignment: 0.618
  - 高分(≥0.8): 31 case (43.1%)
  - 中分(0.5-0.8): 24 case (33.3%)
  - 低分(<0.5): 17 case (23.6%)
  - 解析失败: 0 case

典型低分 case（前 5）:
  - T2-056: score=0.00, missing=[PIRADS, PSA, PSAD, Gleason_primary, ISUP_grade]
  - T2-010: score=0.10, missing=[PIRADS, PSA, PSAD, Gleason_primary, ISUP_grade]
  - T2-009: score=0.20, missing=[PIRADS, Gleason_primary, clinical_T_stage, age, cspca]
  - T2-012: score=0.20, missing=[PIRADS, PSA, PSAD, Gleason_primary, clinical_T_stage]
  - T2-027: score=0.20, missing=[PIRADS, PSAD, Gleason_primary, ISUP_grade, clinical_T_stage]

典型高分 case（前 3）:
  - T2-001: score=1.00, covered=[PIRADS, PSA, PSAD, Gleason_primary, ISUP_grade]
  - T2-002: score=1.00, covered=[PIRADS, PSA, ISUP_grade, clinical_T_stage]
  - T2-003: score=0.90, covered=[PIRADS, PSA, PSAD, ISUP_grade]

=== Task3 GEval 评估（仅 0 case，全量待重跑）===

  无有效评分，跳过

=== 验收标准 ===

| 指标 | 当前值 | 目标 | 状态 |
|---|---|---|---|
| task1 GEval | 0.000 | 建立基线，P0-B 后提升 ≥0.05 | 基线已建立 |
| task2 GEval | 0.618 | 建立基线，P0-B 后提升 ≥0.05 | 基线已建立 |
| task3 GEval | 0.000 | 建立基线，P0-B 后提升 ≥0.05 | 基线已建立 |
| Ollama judge 可用率 | 0 错误 / 72 总 | 100% 跑通 | ✅ |
