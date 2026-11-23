# P3 来源／日期／量纲审查

不读取GT，不选真值，不改输入。以下是旧候选的解释分类，不是已清洗病例或真实冲突率。

| 来源 | 状态 | 条目数 |
|---|---|---:|
| local_train_release_v1 | arithmetic_residual_measurement_alignment_or_rounding_unresolved | 28 |
| local_train_release_v1 | old_screen_quantity_type_error | 33 |
| local_train_release_v1 | at_or_below_old_screen_tolerance_not_clinical_adjudication | 1 |
| local_train_release_v1 | old_screen_explicit_history_current_conflation | 1 |
| local_train_release_v1 | value_disagreement_time_or_source_unresolved | 1 |
| phase2_train_release_20261104 | old_screen_quantity_type_error | 33 |
| phase2_train_release_20261104 | at_or_below_old_screen_tolerance_not_clinical_adjudication | 1 |
| phase2_train_release_20261104 | old_screen_explicit_history_current_conflation | 1 |
| phase2_train_release_20261104 | value_disagreement_time_or_source_unresolved | 1 |
| server_legacy | arithmetic_residual_measurement_alignment_or_rounding_unresolved | 30 |
| server_legacy | value_disagreement_time_or_source_unresolved | 2 |
| server_legacy | density_sources_disagree_alignment_unresolved | 2 |
| server_legacy | prior_biopsy_history_vs_empty_card_requires_review | 2 |
| server_legacy | old_screen_quantity_type_error | 33 |
| server_legacy | at_or_below_old_screen_tolerance_not_clinical_adjudication | 1 |
| server_legacy | old_screen_explicit_history_current_conflation | 1 |

{
  "stage": "S1_P3_input_only_candidate_review",
  "reviewed_candidates": 171,
  "case_version_records": 156,
  "risk_groups_with_candidates": 60,
  "by_source": {
    "local_train_release_v1": {
      "arithmetic_residual_measurement_alignment_or_rounding_unresolved": 28,
      "old_screen_quantity_type_error": 33,
      "at_or_below_old_screen_tolerance_not_clinical_adjudication": 1,
      "old_screen_explicit_history_current_conflation": 1,
      "value_disagreement_time_or_source_unresolved": 1
    },
    "phase2_train_release_20261104": {
      "old_screen_quantity_type_error": 33,
      "at_or_below_old_screen_tolerance_not_clinical_adjudication": 1,
      "old_screen_explicit_history_current_conflation": 1,
      "value_disagreement_time_or_source_unresolved": 1
    },
    "server_legacy": {
      "arithmetic_residual_measurement_alignment_or_rounding_unresolved": 30,
      "value_disagreement_time_or_source_unresolved": 2,
      "density_sources_disagree_alignment_unresolved": 2,
      "prior_biopsy_history_vs_empty_card_requires_review": 2,
      "old_screen_quantity_type_error": 33,
      "at_or_below_old_screen_tolerance_not_clinical_adjudication": 1,
      "old_screen_explicit_history_current_conflation": 1
    }
  },
  "original_inputs_hash_checked": 3834,
  "original_inputs_unchanged": true,
  "GT_contents_opened": 0,
  "new_model_calls": 0,
  "input_values_changed": 0,
  "production_changed": false,
  "scope": "Only frozen T1 candidate list; not a full T1/T2/T3 contradiction audit or clinician adjudication.",
  "next_step": "Retain unresolved evidence and version boundaries; define shared S2 tool access/cost contract before model experiments."
}

量纲误读只关闭该旧筛查标记，不证明整例无其他冲突。不同日期只证明不能当作同次测量直接冲突，不证明数值全部正确。保留所有原值、来源和未知信息。
不自动删除病例或依GT选侧；没有生成已清洗训练／评测输入。后续两组须使用相同原始版本和同一冲突处理约定。
