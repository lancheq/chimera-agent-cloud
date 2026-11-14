from copy import deepcopy
import json
import pytest
from langchain_core.messages import AIMessage,ToolMessage
from chimera_agent_baseline.agent.evidence_review import (primary_records,adjudicate,
    make_review_payload,validate_judgment,normalize_quote,primary_text,AuditReply)
from chimera_agent_baseline.output.schema import eligible_variables,Task1Output,VARIABLES_BY_TASK


class Model:
    def __init__(self,reply):self.reply=reply;self.calls=[]
    def invoke(self,messages):
        payload=json.loads(messages[-1].content.split('\nYour previous')[0]);self.calls.append(payload)
        value=self.reply(payload) if callable(self.reply) else self.reply
        return AIMessage(content=json.dumps(value))


def message(cid='case',status='success'):
    return ToolMessage(name='get_mri_report',tool_call_id='call',status=status,
        content=json.dumps({'case_id':cid,'radiology_report':'Synthetic source report: PI-RADS 3 lesion recorded for a test fixture.',
                            'cspca':'HIDDEN EXTRA MUST NOT BE IMPORTED'}))


def packet():
    records,called,_=primary_records({'age':65,'pid':'private-source-id'},[message()], 'case')
    weights={name:'not_used' for name in eligible_variables(1,set(called))}
    j={'biopsy_decision':'yes','confidence':'uncertain','variable_weights':weights,
       'repeat_test':'Original synthetic plan','free_text':'Original synthetic explanation used only for this software test.'}
    full=Task1Output.model_validate({'case_id':'case','patient':{'id':'case'},**j,
        'variable_weights':{name:'not_used' for name in VARIABLES_BY_TASK[1]}}).model_dump(mode='json')
    return {'case_id':'case','records':records,'called_tools':called,'p_yes':.2,
            'predictor_decision':'no','original_judgment':j,'original_full_output':full}


def reply(p):
    j=deepcopy(p['original_judgment']);j.update(biopsy_decision='no',free_text='A complete alternative synthetic rationale for the fixture.',repeat_test='Revised synthetic plan')
    return {'case_id':'case','judgment':j,'citations':[{'record_id':'obs_001',
        'quote':'PI-RADS 3 lesion recorded','claim':'A synthetic fixture lesion category is recorded.'}],
        'reason_for_revision':'Synthetic test revision, not clinical advice.'}


def good_audit(payload):
    return {'case_id':'case','review_digest':payload['review_digest'],
        'audited_decision':payload['proposed_final_output']['biopsy_decision'],
        'factual_support':True,'decision_reason_consistent':True,'plan_consistent':True,
        'reviewed_fields':['biopsy_decision','free_text','repeat_test'],'unsupported_claims':[],'issues':[]}


def test_only_successful_case_matched_defined_tool_fields_are_primary():
    records,_,rejected=primary_records({'pid':'private'},[message('other'),message(status='error'),
        AIMessage(content='I saw an MRI'),message()], 'case')
    assert len(records)==2 and len(rejected)==2
    assert all('HIDDEN EXTRA' not in r['text'] and 'private' not in r['text'] for r in records)


def test_source_arm_adds_only_primary_records_with_other_inputs_equal():
    p=packet();a,ra=make_review_payload(p,'context_only_review');b,rb=make_review_payload(p,'observed_evidence_review')
    assert len(ra)==1 and len(rb)==2
    assert {k:v for k,v in a.items() if k!='primary_records'}=={k:v for k,v in b.items() if k!='primary_records'}
    assert 'label' not in a and 'label' not in b


def test_complete_revision_is_accepted_only_after_bound_separate_audit():
    p=packet();audit=Model(good_audit);r=adjudicate(p,'observed_evidence_review',Model(reply(p)),audit)
    assert r['accepted'] and r['revised']
    assert r['final_output']['biopsy_decision']=='no'
    assert r['final_output']['free_text']!=p['original_full_output']['free_text']
    assert r['final_output']['repeat_test']!=p['original_full_output']['repeat_test']
    assert 'original_judgment' not in audit.calls[0] and 'quantitative_opinion' not in audit.calls[0]
    assert p['original_full_output']['biopsy_decision']=='yes'


@pytest.mark.parametrize('field,value',[('plan_consistent',False),('review_digest','0'*64),('audited_decision','yes'),('reviewed_fields',['biopsy_decision'])])
def test_audit_rejection_preserves_the_entire_original(field,value):
    p=packet()
    def audit(payload):
        r=good_audit(payload);r[field]=value;return r
    result=adjudicate(p,'observed_evidence_review',Model(reply(p)),Model(audit))
    assert not result['accepted'] and result['final_output']==p['original_full_output']


def test_generated_judgment_or_unavailable_tool_cannot_be_used_as_primary_evidence():
    p=packet();review=reply(p);audit=Model(good_audit)
    result=adjudicate(p,'context_only_review',Model(review),audit)
    assert not result['accepted'] and not audit.calls
    assert result['final_output']==p['original_full_output']


def test_invented_quote_is_a_visible_policy_rejection_not_accepted_revision():
    p=packet();review=reply(p);review['citations'][0]['quote']='UNSEEN INVENTED CLINICAL CLAIM'
    result=adjudicate(p,'observed_evidence_review',Model(review),Model(good_audit))
    assert not result['accepted'] and result['rejection_reasons']


def test_no_missing_decision_or_blank_weight_defaults():
    p=packet();j=deepcopy(p['original_judgment']);j['biopsy_decision']=None
    with pytest.raises(ValueError):validate_judgment(j,p['called_tools'])
    j=deepcopy(p['original_judgment']);j['variable_weights']['age']=''
    with pytest.raises(ValueError,match='explicit'):validate_judgment(j,p['called_tools'])


def test_json_escaped_source_quote_is_equivalent_without_erasing_numeric_signs():
    p=packet()
    p['records'][0]['text']=json.dumps({'history':'Prior biopsy bucket "Positive" is recorded.'})
    review=reply(p)
    review['citations']=[{'record_id':'upfront','quote':'Prior biopsy bucket "Positive" is recorded.',
        'claim':'Prior biopsy bucket is recorded in the fixture.'}]
    result=adjudicate(p,'context_only_review',Model(review),Model(good_audit))
    assert result['accepted']
    assert normalize_quote('PSA -4 ng/mL')!=normalize_quote('PSA 4 ng/mL')


def test_leaf_rendering_preserves_paths_and_short_complete_facts():
    text=primary_text({'age':71,'note_sections':[{'t':'Biopsy bucket "Positive" is recorded.'}]})
    assert 'age: 71' in text
    assert 'note_sections[0].t: Biopsy bucket "Positive" is recorded.' in text
    p=packet();review=reply(p)
    review['citations']=[{'record_id':'upfront','quote':'age: 65','claim':'Age is recorded in the synthetic fixture.'}]
    assert adjudicate(p,'context_only_review',Model(review),Model(good_audit))['accepted']


def test_auditor_field_list_is_a_schema_contract_not_clinical_variable_names():
    p={'case_id':'case','review_digest':'test','proposed_final_output':{'biopsy_decision':'no'}}
    v=good_audit(p);v['reviewed_fields']=['psa','age','repeat_test']
    with pytest.raises(ValueError):
        AuditReply.model_validate(v)
