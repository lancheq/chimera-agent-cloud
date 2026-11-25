"""Preregistered case inputs and expected outputs; independent of the candidate."""
UNKNOWN = 'UNKNOWN'
SAME = 'SAME_CONFIRMED'
DIFF = 'DIFFERENT_CONFIRMED'
COMP = 'COMPARABLE'
INCOMP = 'INCOMPARABLE'
CONFLICT = 'CONFIRMED_UNRESOLVED'
NA = 'NOT_APPLICABLE'


def state(identity, comp, conflict, count, groups=2, order=None, true=UNKNOWN, values=(5, 6)):
    return {'identity': identity, 'comparability': comp, 'conflict': conflict, 'observations': count,
            'groups': groups, 'order': order, 'truth': true, 'trend': UNKNOWN, 'values': list(values)}


def pair(rid, name, expected, b2, **overrides):
    return (rid, name, 'pair', overrides | {'upstream_expected': b2}, expected)


def case(rid, name, kind, expected, **inputs):
    return (rid, name, kind, inputs, expected)


conflict = state(SAME, COMP, CONFLICT, 1, true='UNRESOLVED')
same = 'SAME_OBSERVATION_SAME_VALUE'
unresolved = 'SAME_OBSERVATION_CONFLICT_UNRESOLVED'
unproved = state(UNKNOWN, COMP, UNKNOWN, UNKNOWN)

PAIRS = (
    pair('R01','card_lab',conflict,unresolved),
    pair('R01','lab_lab_decimal',state(SAME,COMP,CONFLICT,1,true='UNRESOLVED',values=(5.2,6.1)),
         unresolved,left_source='lab',values=(5.2,6.1)),
    pair('R01','card_card_zero',state(SAME,COMP,CONFLICT,1,true='UNRESOLVED',values=(0,6)),
         unresolved,right_source='card',values=(0,6)),
    pair('R01','mri_notes',conflict,unresolved,left_source='mri',right_source='notes'),
    pair('R02','numeric_equivalent',state(SAME,COMP,'NO_CONFLICT',1,values=(5,5.0)),same,values=(5,5.0)),
    pair('R03','ordered_before',state(DIFF,COMP,NA,2,order='before'),'DISTINCT_TIMES',
         right={'observed':'2026-01-04','identity':'other'}),
    pair('R03','ordered_after',state(DIFF,COMP,NA,2,order='after'),'DISTINCT_TIMES',
         left={'observed':'2026-01-04','identity':'other'}),
    pair('R03','disjoint_no_ids',state(DIFF,COMP,NA,2,order='before'),'DISTINCT_TIMES',
         left={'identity':None},right={'observed':'2026-01-04','identity':None}),
    pair('R03','different_ids_shared_period',state(DIFF,COMP,NA,2),'DISTINCT_OBSERVATIONS',
         right={'identity':'other'}),
    pair('R04','same_day_no_identity',unproved,UNKNOWN,left={'identity':None},right={'identity':None}),
    pair('R04','same_day_no_attestation',unproved,UNKNOWN,left={'collection_attested':False}),
    pair('R05','month_overlap_no_identity',unproved,UNKNOWN,
         left={'observed':'2026-01','identity':None},right={'identity':None}),
    pair('R05','two_months_same_period_no_identity',unproved,UNKNOWN,
         left={'observed':'2026-01','identity':None},right={'observed':'2026-01','identity':None}),
    pair('R06','reported_date_only',unproved,UNKNOWN,left={'observed':None},right={'observed':None}),
    pair('R07','psa_vs_psad',state(SAME,INCOMP,NA,1),'KNOWN_NOT_COMPARABLE',right_source='mri',
         right={'metric':'psad','definition':'PSA-density','unit':'ng/mL/mL'}),
    pair('R08','unknown_method',state(SAME,UNKNOWN,UNKNOWN,1),UNKNOWN,right={'method':None}),
    pair('R08','unknown_unit',state(SAME,UNKNOWN,UNKNOWN,1),UNKNOWN,left={'unit':None}),
    pair('R08','unknown_entity',state(SAME,UNKNOWN,UNKNOWN,1),UNKNOWN,right={'entity':None}),
    pair('R08','unknown_definition',state(SAME,UNKNOWN,UNKNOWN,1),UNKNOWN,right={'definition':None}),
    pair('R08','proven_method_conflict',state(SAME,INCOMP,NA,1),'KNOWN_NOT_COMPARABLE',right={'method':'other'}),
    pair('R08','proven_unit_conflict',state(SAME,INCOMP,NA,1),'KNOWN_NOT_COMPARABLE',left={'unit':'foreign-unit'}),
    pair('R08','proven_entity_conflict',state(SAME,INCOMP,NA,1),'KNOWN_NOT_COMPARABLE',right={'entity':'other'}),
    pair('R08','proven_definition_conflict',state(SAME,INCOMP,NA,1),'KNOWN_NOT_COMPARABLE',right={'definition':'other'}),
    pair('R08','distinct_with_missing_method',state(DIFF,UNKNOWN,NA,2,order='before'),UNKNOWN,
         right={'observed':'2026-01-04','identity':'other','method':None}),
    pair('R08','distinct_with_proven_unit_conflict',state(DIFF,INCOMP,NA,2,order='before'),'KNOWN_NOT_COMPARABLE',
         left={'unit':'foreign-unit'},right={'observed':'2026-01-04','identity':'other'}),
    pair('R08','unknown_identity_known_unit_conflict',state(UNKNOWN,INCOMP,NA,UNKNOWN),'KNOWN_NOT_COMPARABLE',
         left={'unit':'foreign-unit','identity':None},right={'identity':None}),
    pair('R08','unknown_identity_and_missing_method',state(UNKNOWN,UNKNOWN,UNKNOWN,UNKNOWN),UNKNOWN,
         left={'identity':None},right={'identity':None,'method':None}),
    pair('R15','same_id_different_certified_dates',unproved,UNKNOWN,right={'observed':'2026-01-04'}),
)

FROZEN = (
    case('R01','frozen_s1_repeat','frozen',state(SAME,COMP,CONFLICT,1,true='UNRESOLVED',values=(5,6,6)),
         fixture='B12-S1', relation='after_repeat'),
    case('R02','frozen_same_values','frozen',state(SAME,COMP,'NO_CONFLICT',1,values=(5.2,5.2)),
         fixture='B12-S3', relation='allowlisted_card_scalar'),
    case('R03','frozen_order','frozen',state(DIFF,COMP,NA,2,order='before',values=(5.0,5.0)),
         fixture='B12-S2', relation='verified_time_and_samples'),
    case('R09','repeated_settlements_same_group','frozen',state(SAME,COMP,'NO_CONFLICT',1,groups=1,values=(6,6)),
         fixture='B12-S1', relation='bound_aliases'),
)

NEGATIVES = (
    case('R10','missing_raw','bad_value',{'reason':'UNSUPPORTED_SCALAR','value_exported':False}, fault='missing'),
    case('R10','invalid_bool','bad_value',{'reason':'UNSUPPORTED_SCALAR','value_exported':False}, fault='bool'),
    case('R10','not_finite','bad_value',{'reason':'MALFORMED_B1_B2_RELATION','value_exported':False},fault='nan'),
    case('R10','malformed_date','bad_value',{'reason':'MALFORMED_B1_B2_RELATION','value_exported':False},fault='date'),
    case('R11','isolated_late_body','isolated_cutoff',
         {'reasons':['AFTER_CUTOFF'],'references':1,'late_in_state':False,'legacy_exported':False,
          'late_in_request_fixture':False}, fault='late'),
    case('R11','unproved_availability','isolated_cutoff',
         {'reasons':['AS_OF_UNPROVED'],'references':1,'late_in_state':False,'legacy_exported':False,
          'late_in_request_fixture':False}, fault='unknown'),
    case('R11','formal_sdk_late_body','formal_cutoff',
         {'sdk_sends':3,'late_in_b_projection':False,'late_sdk_request_indexes':[],
          'ledger_status':'after_cutoff','two_sockets':True}),
    case('R13','t3_psa_trend','t3_guard',
         {'denial':'unknown_or_disabled_tool','backend_reads':0,'spent_units':0,'admitted_attempts':0}),
    case('R14','not_a_completed_observation','assertion_states',
         {'types':['negated','planned','missing','uncertain'],'relations':0,'unbound_history_numbers':0}),
    case('R15','contradictory_b2_core','corrupt',{'reason':'B2_COMPARISON_MISMATCH'},fault='core'),
    case('R15','contradictory_b2_status','corrupt',{'reason':'B2_SAME_OBSERVATION_UNCERTIFIED'},fault='status'),
    case('R15','contradictory_alias_annotations','corrupt',{'reason':'CONFLICTING_GROUP_ASSERTIONS'},fault='alias_assertion'),
    case('R15','model_lowers_known','response',
         {'format':'accepted','first_semantic_mismatch':'conflict_status','raw_unchanged':True,
          'original_format_claim':'UNKNOWN','trusted_claim':'same_observation_conflict_unresolved'},
         variant='historic'),
    case('R15','model_elevates_unknown','response',
         {'format':'accepted','first_semantic_mismatch':'conflict_status','raw_unchanged':True,
          'original_format_claim':'same_observation_conflict_unresolved','trusted_claim':'UNKNOWN'},
         variant='elevate'),
    case('R15','model_invents_default_number','response',
         {'format':'accepted','first_semantic_mismatch':'measurements','raw_unchanged':True},
         variant='default'),
    case('R16','duplicate_alias_in_one_row','response',
         {'format':'accepted','first_semantic_mismatch':'measurements','raw_unchanged':True},
         variant='alias'),
    case('R16','raw_duplicate_json_key','response',
         {'format':'rejected','format_reason':'duplicate_json_key','semantic':'NOT_RUN','raw_unchanged':True},
         variant='duplicate_key'),
    case('R16','truncated_output','response',
         {'format':'rejected','format_reason':'incomplete_length','semantic':'NOT_RUN','raw_unchanged':True},
         variant='length'),
    case('R16','tampered_serialized_state','round_trip',
         {'exact_round_trip':True,'reason':'PROJECTION_ROUND_TRIP_MISMATCH','off':True}),
    case('R16','known_incomparable_to_legacy','legacy_gap',
         {'reason':'NO_LEGACY_INCOMPARABLE_ENUM','internal_status':'INCOMPARABLE','legacy_schema_support':False}),
    case('R16','independent_truth_and_explanation_schema','response',
         {'format':'rejected','format_reason':'schema_violation','semantic':'NOT_RUN','raw_unchanged':True},
         variant='extended_schema'),
)
BINDING = (
    'wrong-card-case_id','wrong-card-release','wrong-card-task','wrong-card-snapshot_id','wrong-card-session_id',
    'changed-card-bytes','forbidden-card-cspca','forbidden-card-AI_predicted_isup_grade_group',
    'forbidden-card-hidden','forbidden-card-ground_truth','forbidden-card-audit_label',
    'bad-ledger-session_id','bad-ledger-case_id','bad-ledger-task','bad-ledger-release',
    'bad-ledger-snapshot_id','bad-ledger-response_digest','bad-ledger-status','bad-ledger-success',
    'bad-ledger-tool_call_id','bad-ledger-fields','bad-ledger-allowed','missing-ledger',
    'ambiguous-ledger','reused-ledger','foreign-top-level-field',
)
CASES = PAIRS + FROZEN + NEGATIVES + tuple(
    case('R12',name,'rejected_frozen',
         {'reason': reason,'retained_values':[5.2],'conflict':'UNKNOWN','observation_count':'UNKNOWN'},
         relation=name)
    for name in BINDING
    for reason in ['UNSUPPORTED_RAW_FIELD' if name.startswith('forbidden-card-') else 'UNBOUND_SOURCE']
) + (
    case('R12','allowed_raw_rebound_wrong_scope','corrupt',{'reason':'REFERENCE_SCOPE_MISMATCH'},fault='scope'),
    case('R12','allowed_raw_rebound_wrong_receipt','corrupt',{'reason':'UNBOUND_TOOL_RECEIPT'},fault='receipt'),
    case('R12','allowed_raw_rebound_wrong_pointer','corrupt',{'reason':'BOUND_GROUP_MISMATCH'},fault='path'),
    case('R12','model_text_with_unmatched_pin','corrupt',{'reason':'SNAPSHOT_SHA256_MISMATCH'},fault='model'),
    case('R09','preserve_all_old_fixtures','prior',
         {'old_counterexamples':8,'supplemental':3,'equal_frozen_jsonl':True}),
)
