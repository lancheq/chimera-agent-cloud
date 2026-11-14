"""Controller mechanism/limits/recovery; real MCP integration is a separate artifact."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import pytest
from langchain_core.messages import AIMessage,HumanMessage
from langchain_core.tools import tool,ToolException
from omegaconf import OmegaConf
from chimera_agent_baseline import shared_plan as sp
from chimera_agent_baseline.information_access import InformationAccess,AccessDenied
from tools.validate_shared_plan_candidate import CandidateModel,fixture_token_guard

ROOT=Path(__file__).resolve().parents[1]


def setup(tmp_path,marker='ENOUGH',budget=3,attempts=6):
    cfg=OmegaConf.merge(OmegaConf.load(ROOT/'configs/config.yaml'),OmegaConf.load(ROOT/'configs/round2_shared_plan_candidate.yaml'))
    cfg.agent.information_access.budget_units=budget
    cfg.agent.information_access.max_attempts=attempts
    access=InformationAccess(ledger_path=tmp_path/'ledger.sqlite',session_id='unit',case_id='A',task=1,
        snapshot_id='unit-source',knowledge_snapshot_id='unit-guideline',budget_units=budget,max_attempts=attempts)
    calls=[]
    def execute(name,case_id,fetch,query=None):
        try:return access.execute(name,case_id,fetch,query=query)
        except AccessDenied as exc:raise ToolException('information_access:'+str(exc))
    @tool
    async def get_mri_report(case_id:str)->str:
        """Fetch a synthetic MRI through the test ledger."""
        def fetch():calls.append('MRI');return {'case_id':case_id,'radiology_report':marker}
        return json.dumps(execute('get_mri_report',case_id,fetch))
    @tool
    async def get_lab_results(case_id:str)->str:
        """Fetch synthetic lab through the test ledger."""
        def fetch():calls.append('LAB');return {'case_id':case_id,'laboratory_results':[{'val':'observed'}]}
        return json.dumps(execute('get_lab_results',case_id,fetch))
    @tool
    async def search_guidelines(query:str)->str:
        """Fetch synthetic knowledge through the test ledger."""
        def fetch():calls.append('KNOWLEDGE');return {'query':query,'results':[{'text':'synthetic guideline'}]}
        return json.dumps(execute('search_guidelines','A',fetch,query))
    tools=[get_mri_report,get_lab_results,search_guidelines]
    artifact={'actions':[{'tool':'get_mri_report','purpose':'Inspect the report'},
                         {'tool':'get_lab_results','purpose':'Inspect the lab'}],'plan_id':'unit-plan'}
    model=CandidateModel();journal=sp.Journal(tmp_path/'events.jsonl')
    recorded=sp.RecordedModel(model,cfg,journal,fixture_token_guard)
    return cfg,access,tools,artifact,model,journal,recorded,calls


def acquire(tmp_path,items,arm='adaptive'):
    cfg,access,tools,artifact,model,journal,recorded,calls=items
    return asyncio.run(sp.acquire(cfg,arm,artifact,'Case ID: A\nTask: 1',tools,access,tmp_path,recorded,journal))


@pytest.mark.parametrize('marker,arm,expected',[
    ('ENOUGH','fixed',['MRI','LAB']),('NEEDS_GUIDELINE','fixed',['MRI','LAB']),
    ('ENOUGH','adaptive',['MRI']),('NEEDS_GUIDELINE','adaptive',['MRI','KNOWLEDGE']),
])
def test_observation_controls_adaptive_next_action_but_not_fixed(tmp_path,marker,arm,expected):
    items=setup(tmp_path,marker)
    state=acquire(tmp_path,items,arm)
    assert items[-1]==expected
    assert state['phase']=='diagnosis'
    assert state['stop_reason']==('fixed_plan_complete' if arm=='fixed' else 'policy_stop')
    assert items[1].snapshot()['spent_units']==(2 if arm=='fixed' else 1)


def test_budget_denial_is_observed_without_becoming_a_revealed_section(tmp_path):
    items=setup(tmp_path,budget=1)
    state=acquire(tmp_path,items,'fixed')
    assert items[-1]==['MRI']
    assert state['observations'][1]['data']['status']=='error'
    assert not state['observations'][1]['data']['additional_kwargs']['information_access_clinical_success']
    assert items[1].snapshot()['visible_sections']==['radiology_report']


def test_empty_initial_plan_does_not_force_tools_or_adaptive_calls(tmp_path):
    items=setup(tmp_path);items[3]['actions']=[]
    state=acquire(tmp_path,items)
    assert state['stop_reason']=='empty_initial_plan' and not items[-1] and not items[4].inputs


def test_shared_plan_generated_once_and_binding_changes_refused(tmp_path):
    cfg,access,tools,artifact,model,journal,recorded,calls=setup(tmp_path)
    directory=tmp_path/'common'
    first,reused=sp.load_or_generate_plan(cfg,access,'Case ID: A\nTask: 1',tools,directory,model,fixture_token_guard)
    assert not reused
    second,reused=sp.load_or_generate_plan(cfg,access,'Case ID: A\nTask: 1',tools,directory,model,fixture_token_guard)
    assert reused and first==second and len(model.inputs)==1
    assert 'ENOUGH' not in str(model.inputs[0]) and not calls
    with pytest.raises(ValueError,match='binding differs'):
        sp.load_or_generate_plan(cfg,access,'changed upfront context',tools,directory,model,fixture_token_guard)
    assert len(model.inputs)==1


def test_schema_rejects_case_switch_and_unregistered_tools(tmp_path):
    cfg,access,tools,*_=setup(tmp_path)
    with pytest.raises(ValueError):sp.Action(tool='get_mri_report',purpose='x',case_id='B')
    with pytest.raises(ValueError,match='Unknown'):
        sp.validate_actions([sp.Action(tool='get_hidden_features',purpose='x')],tools,6)
    with pytest.raises(ValueError,match='nonempty query'):
        sp.validate_actions([sp.Action(tool='search_guidelines',purpose='x')],tools,6)
    assert access.snapshot()['admitted_attempts']==0


def test_invalid_policy_fails_after_bounded_format_retries(tmp_path):
    items=setup(tmp_path)
    class Invalid:
        def invoke(self,messages,**kwargs):return AIMessage(content='not JSON')
    items=list(items);items[6]=sp.RecordedModel(Invalid(),items[0],items[5],fixture_token_guard)
    with pytest.raises(ValueError,match='bounded retries'):acquire(tmp_path,items)
    assert items[-1]==['MRI']
    assert sum(e['kind']=='model_return' for e in items[5].events)==2
    assert not (tmp_path/'prediction.json').exists()


def test_action_and_policy_caps_stop_even_repeated_cached_requests(tmp_path):
    items=list(setup(tmp_path,attempts=2))
    class Repeat:
        def invoke(self,messages,**kwargs):return AIMessage(content=json.dumps(dict(action='execute',reason='repeat',
            remaining_plan=[dict(tool='get_mri_report',purpose='repeat')])) )
    items[6]=sp.RecordedModel(Repeat(),items[0],items[5],fixture_token_guard)
    state=acquire(tmp_path,items)
    assert state['requests']==6 and state['policy_turns']==5 and state['stop_reason']=='action_cap'
    assert items[-1]==['MRI'] and items[1].snapshot()['admitted_attempts']==2
    assert items[1].snapshot()['spent_units']==1


def test_checkpoint_loss_after_return_reuses_tool_without_extra_charge(monkeypatch,tmp_path):
    items=setup(tmp_path)
    original=sp.save_checkpoint
    def interrupt(path,state):
        if state['observations'] and state['pending_action'] is None:raise ConnectionError('simulated checkpoint interruption')
        original(path,state)
    monkeypatch.setattr(sp,'save_checkpoint',interrupt)
    with pytest.raises(ConnectionError):acquire(tmp_path,items)
    assert items[-1]==['MRI']
    before=items[1].snapshot()
    monkeypatch.setattr(sp,'save_checkpoint',original)
    state=acquire(tmp_path,items)
    assert state['stop_reason']=='policy_stop' and items[-1]==['MRI']
    assert items[1].snapshot()['admitted_attempts']==before['admitted_attempts']==1
    assert items[1].snapshot()['spent_units']==before['spent_units']==1


def test_ambiguous_tool_request_is_not_automatically_replayed(tmp_path):
    items=list(setup(tmp_path));tool=items[2][0]
    original=tool.ainvoke
    async def loses_response(*args,**kwargs):
        await original(*args,**kwargs)
        raise ConnectionError('lost tool response')
    from types import SimpleNamespace
    items[2][0]=SimpleNamespace(name=tool.name,ainvoke=loses_response)
    with pytest.raises(ConnectionError):acquire(tmp_path,items)
    assert items[1].snapshot()['spent_units']==1
    with pytest.raises(RuntimeError,match='Ambiguous'):acquire(tmp_path,items)
    assert items[-1]==['MRI'] and items[1].snapshot()['admitted_attempts']==1


def test_context_rejection_precedes_model_call_and_is_audited(tmp_path):
    cfg,access,tools,artifact,model,journal,*_=setup(tmp_path)
    def reject(*args):raise ValueError('context limit')
    recorded=sp.RecordedModel(model,cfg,journal,reject)
    with pytest.raises(ValueError,match='context limit'):
        recorded.invoke('planner','plan',[HumanMessage(content='synthetic')])
    assert not model.inputs
    assert journal.events[-1]['kind']=='model_rejected_before_request'


def test_recorded_model_reply_survives_resume_without_regeneration(tmp_path):
    cfg,access,tools,artifact,model,journal,recorded,*_=setup(tmp_path)
    msgs=[HumanMessage(content='[COMMON_DIAGNOSIS] Case ID: A\nTask: 1')]
    response=recorded.invoke('diagnosis','stable',msgs)
    replay=recorded.invoke('diagnosis','stable',msgs)
    assert response==replay and len(model.inputs)==1
    with pytest.raises(ValueError,match='request differs'):
        recorded.invoke('diagnosis','stable',[HumanMessage(content='changed')])


def test_journal_tampering_is_not_silently_accepted(tmp_path):
    path=tmp_path/'events.jsonl';journal=sp.Journal(path);journal.append('fixture',value=1)
    path.write_text(path.read_text().replace('"value": 1','"value": 2'))
    with pytest.raises(ValueError,match='chain differs'):sp.Journal(path)


def test_common_first_observation_ignores_transport_uuid_but_not_content(tmp_path):
    from langchain_core.messages import ToolMessage
    def message(value,uuid):
        return ToolMessage(content=[dict(type='text',text=json.dumps({'case_id':'A','radiology_report':value}),id=uuid)],
            name='get_mri_report',tool_call_id='first')
    artifact={'plan_id':'fixture'}
    first=sp.bind_common_first_observation(tmp_path,artifact,message('observed','transport-1'))
    second=sp.bind_common_first_observation(tmp_path,artifact,message('observed','transport-2'))
    assert first==second
    with pytest.raises(ValueError,match='differs across arms'):
        sp.bind_common_first_observation(tmp_path,artifact,message('changed','transport-3'))


def test_initial_plan_can_legally_start_with_knowledge_and_stop_from_observation(tmp_path):
    items=setup(tmp_path)
    items[3]['actions']=[dict(tool='search_guidelines',query='synthetic',purpose='Check guidance'),
                         dict(tool='get_mri_report',purpose='Inspect report')]
    state=acquire(tmp_path,items)
    assert state['stop_reason']=='policy_stop' and items[-1]==['KNOWLEDGE']
    assert items[1].snapshot()['spent_units']==0 and items[1].snapshot()['admitted_attempts']==1


def test_adaptive_model_receives_full_registered_action_space(tmp_path):
    items=setup(tmp_path)
    acquire(tmp_path,items)
    policy=items[4].inputs[0]['messages']
    text='\n'.join(str(m.content) for m in policy)
    assert 'Available tools:' in text
    for tool in items[2]:assert tool.name in text and tool.description in text


def test_diagnostic_system_closes_prior_tool_workflow():
    assert '[ACQUISITION IS COMPLETE]' in sp.diagnostic_system(1)
