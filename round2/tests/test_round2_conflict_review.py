from tools.review_round2_conflicts import date_interval,unit_kind,review,psa_observations


def candidate(kind='psa_multi'):return {'type':kind}


def test_density_is_not_serum_psa_or_a_true_value_repair():
    card={'psa':5.9};ehr={'psa_trend':[{'date':'Jan 2025','val':5.9}],
        'laboratory_results':[{'name':'PSA','val':'5.9 ng/mL','date':'23 Dec 2024'}],
        'radiology_report':'MRI Jan 2025. PSA density of 0.14 ng/mL/mL.'}
    x=review(candidate(),card,ehr)
    assert x['status']=='old_screen_quantity_type_error'
    assert x['wrong_quantity_sources']==['mri_report']
    assert x['selection_of_true_value'] is None and not x['input_edit']
    assert ehr['radiology_report'].endswith('0.14 ng/mL/mL.')


def test_same_quantity_at_different_specimen_dates_is_retained_as_longitudinal():
    ehr={'psa_trend':[{'date':'Jan 2025','val':.5}],
        'laboratory_results':[{'name':'PSA','val':'.4 ng/mL','date':'20 Dec 2024'}]}
    assert review(candidate(),{},ehr)['status']=='different_measurement_times_keep_longitudinal_values'


def test_month_precision_does_not_prove_same_specimen_or_same_day():
    ehr={'psa_trend':[{'date':'Jan 2025','val':5}],
        'laboratory_results':[{'name':'PSA','val':'8 ng/mL','date':'10 Jan 2025'}]}
    x=review(candidate(),{},ehr)
    assert x['status']=='value_disagreement_time_or_source_unresolved'
    assert x['remaining_screen_disagreements'][0]['date_relation']=='measurement_date_unknown_or_partial_overlap'


def test_document_and_encounter_dates_are_not_specimen_dates():
    x=psa_observations({'psa':5,'enc_date':'10 Jan 2025'},
        {'radiology_report':'10 Jan 2025 MRI: current PSA 8 ng/mL.'})
    assert x['card']['measurement_date'] is None
    assert x['card']['document_date']['precision']=='day'
    assert x['mri_report']['measurement_date'] is None


def test_free_psa_cannot_close_as_confirmed_total_psa():
    x=review(candidate(),{'psa':5},{'laboratory_results':[{'name':'Free PSA','val':'1 ng/mL','date':'10 Jan 2025'}]})
    assert x['observations']['lab']['quantity']=='not_confirmed_total_psa'
    assert 'lab' in x['wrong_quantity_sources']


def test_exact_screen_threshold_does_not_become_float_roundoff_conflict():
    ehr={'psa_trend':[{'date':'Jan 2025','val':.76}],
        'laboratory_results':[{'name':'PSA','val':'.8 ng/mL','date':'20 Dec 2024'}]}
    assert review(candidate(),{},ehr)['status']=='at_or_below_old_screen_tolerance_not_clinical_adjudication'


def test_arithmetic_mismatch_is_not_automatically_corrected():
    x=review(candidate('psad_self'),{'psa':6.8,'vol':36,'psad':.2},{})
    assert abs(x['calculated_psa_over_volume']-6.8/36)<1e-12
    assert 'unresolved' in x['status'] and not x['input_edit']


def test_future_or_negated_biopsy_is_not_affirmative_history():
    x=review(candidate('bx_mention'),{'bx':'None','notes':'No prior biopsy. A biopsy is planned.'},{})
    assert x['status']=='biopsy_mention_only_not_proof_of_prior_procedure'


def test_partial_dates_and_units_remain_explicit():
    assert date_interval('Feb 2024')['end']=='2024-02-29'
    assert date_interval('01/02/2025') is None
    assert unit_kind('5.2 ng/mL')==unit_kind('5.2 µg/L')=='ng_per_ml_equivalent'
    assert unit_kind('5.2')=='unrecorded_or_unrecognized'
    assert unit_kind('0.14 ng/mL/mL')=='unrecorded_or_unrecognized'


def test_explicit_dated_history_now_comparison_is_not_same_time_conflict():
    ehr={'psa_trend':[{'date':'Jan 2025','val':21}],
        'radiology_report':'Assessment of PSA trend (23.5 ng/mL in Feb 2024, now 21.0 ng/mL).'}
    x=review(candidate(),{'psa':21},ehr)
    assert x['status']=='old_screen_explicit_history_current_conflation'
    assert x['observations']['mri_report']['measurement_date']['start']=='2024-02-01'
    assert x['selection_of_true_value'] is None
    # Additional unmatched observations cannot be wished away by a now marker.
    x=review(candidate(),{'psa':18},ehr)
    assert x['status']=='value_disagreement_time_or_source_unresolved'
