# GEval 评估报告 (rationale alignment)

> 生成时间: 2026-08-21 22:38:44
> Judge 模型: modelscope.cn/Qwen/Qwen2.5-7B-Instruct-GGUF:latest
> 预测目录: test/output.pro_v1_20260816/
> 评估方式: Ollama judge 模型近似评估（最终以 GC 提交为准）

=== Task1 GEval 评估（90 case）===

平均 rationale alignment: 0.382
  - 高分(≥0.8): 43 case (47.8%)
  - 中分(0.5-0.8): 0 case (0.0%)
  - 低分(<0.5): 47 case (52.2%)
  - 解析失败: 0 case

典型低分 case（前 5）:
  - PT-pseudo_0020cfca66c8: score=0.00, missing=[psa_trend]
  - PT-pseudo_0169468160c6: score=0.00, missing=[PSAD, family_history, previous_biopsy, psa_trend]
  - PT-pseudo_01b643727538: score=0.00, missing=[radiology_report]
  - PT-pseudo_033f1a8e1503: score=0.00, missing=[PSA_trend]
  - PT-pseudo_0615b2bd8504: score=0.00, missing=[family_history]

典型高分 case（前 3）:
  - PT-pseudo_062a1125899d: score=0.80, covered=[PIRADS, PSA, PSAD, DRE, cspca]
  - PT-pseudo_06b319f42fcb: score=0.80, covered=[PIRADS, PSA, PSAD, PSA trend, prostate_volume]
  - PT-pseudo_0930885168c9: score=0.80, covered=[PIRADS, PSA, PSAD, age, prostate_volume]

=== Task2 GEval 评估（70 case）===

平均 rationale alignment: 0.639
  - 高分(≥0.8): 55 case (78.6%)
  - 中分(0.5-0.8): 1 case (1.4%)
  - 低分(<0.5): 14 case (20.0%)
  - 解析失败: 0 case

典型低分 case（前 5）:
  - T2-003: score=0.00, missing=[comorbidity, family_history, cspca]
  - T2-010: score=0.00, missing=[comorbidity, family_history, cspca]
  - T2-011: score=0.00, missing=[cspca, comorbidity, family_history]
  - T2-012: score=0.00, missing=[PSAD, cspca, comorbidity, family_history]
  - T2-026: score=0.00, missing=[Gleason_primary, ISUP_grade, clinical_T_stage, age, comorbidity]

典型高分 case（前 3）:
  - T2-001: score=0.80, covered=[PIRADS, PSA, PSAD, Gleason_primary, ISUP_grade]
  - T2-002: score=0.80, covered=[PIRADS, PSA, Gleason_score, ISUP_grade, clinical_T_stage]
  - T2-004: score=0.80, covered=[PIRADS, PSA, Gleason_primary, ISUP_grade, clinical_T_stage]

=== Task3 GEval 评估（仅 4 case，全量待重跑）===

平均 rationale alignment: 0.000
  - 高分(≥0.8): 0 case (0.0%)
  - 中分(0.5-0.8): 0 case (0.0%)
  - 低分(<0.5): 4 case (100.0%)
  - 解析失败: 0 case

典型低分 case（前 5）:
  - T3-001: score=0.00, missing=[PSA_density, PI-RADS, probability_of_clinically_significant_prostate_cancer]
  - T3-002: score=0.00, missing=[Gleason_primary, ISUP_grade]
  - T3-003: score=0.00, missing=[Gleason_primary, ISUP_grade, pathological_stage, radiology_report]
  - T3-004: score=0.00, missing=[PI-RADS, radiology_report]

=== 验收标准 ===

| 指标 | 当前值 | 目标 | 状态 |
|---|---|---|---|
| task1 GEval | 0.382 | 建立基线，P0-B 后提升 ≥0.05 | 基线已建立 |
| task2 GEval | 0.639 | 建立基线，P0-B 后提升 ≥0.05 | 基线已建立 |
| task3 GEval | 0.000 | 建立基线，P0-B 后提升 ≥0.05 | 基线已建立 |
| Ollama judge 可用率 | 0 错误 / 164 总 | 100% 跑通 | ✅ |
