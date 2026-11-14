"""Shared initial planning and fixed/adaptive acquisition candidates.

Explicit experimental entry only. Policy text is not diagnostic evidence.
Tools remain real MCP tools behind the case-bound S2 ledger.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage, messages_from_dict, messages_to_dict
from langchain_core.tools import ToolException
from omegaconf import OmegaConf
from pydantic import BaseModel, ConfigDict, Field

from chimera_agent_baseline import guarded_runtime
from chimera_agent_baseline.agent.form_fill import make_form_fill_node
from chimera_agent_baseline.agent.form_parsing import _extract_json_object
from chimera_agent_baseline.agent.prompts import build_system_prompt
from chimera_agent_baseline.agent.evidence_features import decode_payload
from chimera_agent_baseline.information_access import canonical, digest
from chimera_agent_baseline.control_schema import control_schema


class Action(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    tool: str
    query: str | None = Field(default=None, max_length=10000)
    purpose: str = Field(min_length=1, max_length=256)


class Plan(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    actions: list[Action] = Field(max_length=32)


class Decision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['execute', 'stop']
    remaining_plan: list[Action] = Field(max_length=32)
    reason: str = Field(min_length=1, max_length=512)


class PolicyPlan(BaseModel):
    """v2 wire format: one authoritative plan; empty means stop."""
    model_config = ConfigDict(extra='forbid', strict=True)
    remaining_plan: list[Action] = Field(max_length=32)
    reason: str = Field(min_length=1, max_length=512)

    def decision(self):
        return Decision(action='execute' if self.remaining_plan else 'stop',
                        remaining_plan=self.remaining_plan, reason=self.reason)


def repaired(cfg):
    return cfg.agent.shared_plan.get('control_interface_version', 1) == 2


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(path)


class Journal:
    """Append-only chained events; completed replies survive checkpoint loss."""
    def __init__(self, path):
        self.path = Path(path)
        self.events = []
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                event = json.loads(line)
                check = {k: v for k, v in event.items() if k != 'sha256'}
                previous = self.events[-1]['sha256'] if self.events else None
                if event['seq'] != len(self.events) or event['previous'] != previous or digest(check) != event['sha256']:
                    raise ValueError('Candidate journal chain differs; preserve and reconcile')
                self.events.append(event)

    def append(self, kind, **data):
        event = dict(seq=len(self.events), previous=self.events[-1]['sha256'] if self.events else None,
                     timestamp=datetime.now(timezone.utc).isoformat(), kind=kind, **data)
        event['sha256'] = digest(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open('a') as handle:
            handle.write(canonical(event) + '\n')
            handle.flush()
            import os
            os.fsync(handle.fileno())
        self.events.append(event)
        return event

    def find(self, kind, request_id):
        return next((x for x in reversed(self.events) if x['kind'] == kind and x.get('request_id') == request_id), None)


class RecordedModel:
    def __init__(self, model, cfg, journal, token_guard):
        self.model, self.cfg, self.journal, self.token_guard = model, cfg, journal, token_guard

    def invoke(self, stage, request_id, messages, *, response_schema=None, final_only=False):
        options = self.cfg.agent.shared_plan
        limit = options[stage + '_max_tokens']
        configured=self.cfg.model.get('extra_body')
        extra=OmegaConf.to_container(configured,resolve=True) if OmegaConf.is_config(configured) else dict(configured or {})
        extra.update(seed=options.seed, reasoning_budget_tokens=options[stage + '_reasoning_tokens'])
        if final_only:
            extra['reasoning_budget_tokens'] = 0
            extra['chat_template_kwargs'] = dict(extra.get('chat_template_kwargs') or {}, enable_thinking=False)
        kwargs = dict(max_tokens=limit, extra_body=extra)
        if response_schema is not None:
            kwargs['response_format'] = dict(type='json_schema', json_schema=dict(
                name='candidate_control', strict=True, schema=response_schema))
        serialized = messages_to_dict(messages)
        request_digest = digest(dict(stage=stage, messages=serialized, options=kwargs))
        old = self.journal.find('model_return', request_id)
        if old:
            if old['request_digest'] != request_digest:
                raise ValueError('Recorded model request differs on resume')
            return messages_from_dict(old['response'])[0]
        try:
            budget = (self.token_guard(messages, limit, chat_template_kwargs=extra['chat_template_kwargs'])
                      if final_only else self.token_guard(messages, limit))
        except Exception as exc:
            self.journal.append('model_rejected_before_request',request_id=request_id,stage=stage,error=str(exc))
            raise
        self.journal.append('model_request', request_id=request_id, request_digest=request_digest,
                            stage=stage, messages=serialized, options=kwargs, context_budget=budget)
        started = time.monotonic()
        try:
            response = self.model.invoke(messages, **kwargs)
        except Exception as exc:
            self.journal.append('model_error', request_id=request_id, stage=stage,
                error_type=type(exc).__name__, elapsed_seconds=time.monotonic()-started)
            raise
        if not isinstance(response, AIMessage):
            raise TypeError('Candidate model must return AIMessage')
        self.journal.append('model_return', request_id=request_id, request_digest=request_digest, stage=stage,
            response=messages_to_dict([response]), usage=getattr(response, 'usage_metadata', None),
            elapsed_seconds=time.monotonic()-started)
        return response

    def structured(self, stage, request_id, messages, schema, validate, *, output_schema=None):
        last_error = None
        for attempt in range(1, self.cfg.agent.shared_plan.control_format_retries + 1):
            prompt = list(messages)
            if last_error:
                prompt.append(HumanMessage(content='Control JSON invalid: ' + last_error + '. Return the specified JSON schema.'))
            response = self.invoke(stage, request_id + ':' + str(attempt), prompt,
                response_schema=output_schema, final_only=bool(output_schema) and attempt > 1)
            try:
                if output_schema and (response.tool_calls or response.response_metadata.get('finish_reason') == 'length'):
                    raise ValueError('Control output truncated or requested a tool')
                value = schema.model_validate_json(_extract_json_object(str(response.content)))
                validate(value)
                return value
            except (ValueError, TypeError) as exc:
                last_error = str(exc)[:1000]
                self.journal.append('control_parse_error', request_id=request_id, stage=stage, attempt=attempt, error=last_error)
        raise ValueError('Candidate control output invalid after bounded retries: ' + str(last_error))


    def diagnostic(self, messages):
        if not repaired(self.cfg):
            response = self.invoke('diagnosis', 'diagnosis', messages)
            if response.tool_calls or not isinstance(response.content, str) or not response.content.strip():
                raise ValueError('Common diagnostic response is empty or requests extra acquisition')
            return response
        for attempt in range(1, self.cfg.agent.shared_plan.diagnosis_attempts + 1):
            prompt = list(messages)
            if attempt > 1:
                prompt.append(HumanMessage(content='The previous response had no valid final assessment. '
                    'Return a non-empty final assessment using exactly the supplied evidence. '
                    'Acquisition remains closed; do not call tools or assume unseen information.'))
            response = self.invoke('diagnosis', 'diagnosis:' + str(attempt), prompt, final_only=attempt > 1)
            if not response.tool_calls and isinstance(response.content, str) and response.content.strip():
                return response
            self.journal.append('diagnostic_output_error', request_id='diagnosis:' + str(attempt),
                attempt=attempt, error='Empty final assessment or extra acquisition request')
        raise ValueError('Common diagnostic response invalid after bounded output recovery')


def validate_config(cfg):
    guarded_runtime.validate_options(cfg)
    if not guarded_runtime.enabled(cfg):
        raise ValueError('Shared-plan candidate requires the guarded information boundary')
    if not cfg.agent.information_access.get('knowledge_manifest'):
        raise ValueError('Complete candidate requires authorized knowledge retrieval')
    options = cfg.agent.shared_plan
    version = options.get('control_interface_version', 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError('Unsupported control_interface_version')
    if version == 2:
        if cfg.model.get('provider') != 'openai':
            raise ValueError('v2 constrained output adapter requires the verified local llama.cpp interface')
        if type(options.get('diagnosis_attempts')) is not int or not 1 <= options.diagnosis_attempts <= 2:
            raise ValueError('v2 diagnosis_attempts must be 1 or 2')
    for name in ('max_actions', 'max_policy_turns', 'control_format_retries', 'max_consecutive_failed_pairs', 'context_limit', 'context_reserve',
                 'planner_max_tokens', 'policy_max_tokens', 'diagnosis_max_tokens', 'form_fill_max_tokens'):
        if type(options.get(name)) is not int or options[name] < 1:
            raise ValueError('Positive integer required: shared_plan.' + name)
    if options.max_actions != options.max_policy_turns + 1:
        raise ValueError('Common first action plus adaptive policy turns must equal the shared action cap')
    if options.max_actions > 32 or options.control_format_retries > 3:
        raise ValueError('Candidate action/retry configuration exceeds accepted bounds')
    for stage in ('planner', 'policy', 'diagnosis', 'form_fill'):
        reasoning = options.get(stage + '_reasoning_tokens')
        if type(reasoning) is not int or reasoning < 0 or reasoning >= options[stage + '_max_tokens']:
            raise ValueError('Reasoning allowance must leave output space for ' + stage)
    if type(options.get('seed')) is not int:
        raise ValueError('Explicit integer seed required')
    if cfg.model.get('max_retries') != 0:
        raise ValueError('Disable invisible transport retries with model.max_retries=0')
    if cfg.agent.form_fill.get('self_refine') or cfg.agent.form_fill.get('atomic_check'):
        raise ValueError('This candidate uses common schema/permission verification; legacy clinical verifier is excluded')
    if cfg.agent.predictor.get('t1_evidence_features'):
        raise ValueError('Unadapted predictor feature bridge excluded')


def validate_actions(actions, tools, maximum):
    if len(actions) > maximum:
        raise ValueError('Plan exceeds shared remaining action cap')
    names = {t.name for t in tools}
    for action in actions:
        if action.tool not in names:
            raise ValueError('Unknown or unavailable action tool')
        if action.tool == 'search_guidelines':
            if not action.query or not action.query.strip():
                raise ValueError('Knowledge action requires a nonempty query')
        elif action.query is not None:
            raise ValueError('Clinical action does not accept query or case-switch arguments')


def diagnostic_system(task):
    return build_system_prompt(task)+'\n\n[ACQUISITION IS COMPLETE] The earlier tool-workflow instructions have already been handled. Do not request further tools. Assess only the supplied initial record and verified observations; missing/refused data stays unknown.'


def common_binding(cfg, access, context, tools):
    package = Path(__file__).parent
    return dict(case_id=access.binding['case_id'], task=access.binding['task'],
        run_id=cfg.agent.information_access.run_id,
        diagnostic_system_sha256=hashlib.sha256(diagnostic_system(access.binding['task']).encode()).hexdigest(),
        upfront_context_sha256=hashlib.sha256(context.encode()).hexdigest(),
        clinical_snapshot_id=access.binding['snapshot_id'], knowledge_snapshot_id=access.binding['knowledge_snapshot_id'],
        budget_units=access.binding['budget_units'], max_attempts=access.binding['max_attempts'],
        options={k:v for k,v in OmegaConf.to_container(cfg.agent.shared_plan,resolve=True).items() if k!='arm'},
        model={k:OmegaConf.to_container(cfg.model,resolve=True).get(k) for k in ('provider','model_id','base_url','extra_body','max_retries')},
        generation=OmegaConf.to_container(cfg.generation,resolve=True),
        tools=[dict(name=t.name,description=t.description,args_schema=t.args_schema if isinstance(t.args_schema,dict) else t.args_schema.model_json_schema()) for t in tools],
        code={str(p.relative_to(package)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(package.rglob('*.py')) if not p.name.startswith('._')})


def load_or_generate_plan(cfg, access, context, tools, directory, model, token_guard):
    binding = common_binding(cfg, access, context, tools)
    path = directory / 'initial_plan.json'
    with guarded_runtime.output_lease(directory):
        if path.exists():
            artifact = json.loads(path.read_text())
            if artifact['binding'] != binding or artifact['plan_id'] != digest(dict(binding=binding, actions=artifact['actions'])):
                raise ValueError('Shared initial-plan binding differs; do not regenerate an arm-specific plan')
            if artifact['journal_sha256'] != hashlib.sha256((directory/'model_calls.jsonl').read_bytes()).hexdigest():
                raise ValueError('Shared planner journal differs')
            validate_actions(Plan(actions=artifact['actions']).actions, tools, cfg.agent.shared_plan.max_actions)
            return artifact, True
        journal = Journal(directory/'model_calls.jsonl')
        recorded = RecordedModel(model,cfg,journal,token_guard)
        output_schema = control_schema('actions', tools, cfg.agent.shared_plan.max_actions) if repaired(cfg) else None
        messages=[SystemMessage(content='[SHARED_INITIAL_PLAN] Plan evidence acquisition before any hidden observations. Return ONLY JSON '+canonical(output_schema or Plan.model_json_schema())),
            HumanMessage(content=context+'\nTools: '+canonical(binding['tools'])+'\nBudget: '+canonical(guarded_runtime.public_budget(access))+
                f'\nAt most {cfg.agent.shared_plan.max_actions} ordered actions. Empty plan is permitted. Purpose is a short action rationale, not unseen clinical evidence.')]
        value=recorded.structured('planner','initial-plan',messages,Plan,
            lambda plan:validate_actions(plan.actions,tools,cfg.agent.shared_plan.max_actions), output_schema=output_schema)
        actions=value.model_dump(mode='json')['actions']
        artifact=dict(binding=binding,actions=actions,plan_id=digest(dict(binding=binding,actions=actions)),
            journal_sha256=hashlib.sha256(journal.path.read_bytes()).hexdigest())
        atomic_json(path,artifact)
        return artifact,False


def observation_view(observations):
    return [dict(tool=m.name,status=m.status,observation=decode_payload(m.content) or m.content) for m in observations]


async def perform_action(action, request_id, tools, access, journal, timeout):
    args={'query':action.query} if action.tool=='search_guidelines' else {'case_id':access.binding['case_id']}
    old=journal.find('tool_return',request_id)
    binding=digest(dict(tool=action.tool,args=args))
    if old:
        if old['request_digest']!=binding:raise ValueError('Recorded tool request differs')
        message=messages_from_dict(old['message'])[0]
        guarded_runtime.verify_messages(access,[message])
        return message
    if journal.find('tool_request',request_id):
        raise RuntimeError('Ambiguous in-flight tool outcome: preserve ledger and reconcile, never automatically reset or re-execute')
    journal.append('tool_request',request_id=request_id,request_digest=binding,tool=action.tool,purpose=action.purpose,args=args,
        budget_before=guarded_runtime.public_budget(access))
    started=time.monotonic()
    try:
        raw=await asyncio.wait_for(tools[action.tool].ainvoke(args),timeout=timeout)
        message=ToolMessage(content=raw if isinstance(raw,(str,list)) else canonical(raw),name=action.tool,tool_call_id=request_id)
    except ToolException as exc:
        try:
            text=guarded_runtime.expected_tool_error(exc)
        except ToolException:
            journal.append('tool_error',request_id=request_id,error_type=type(exc).__name__,elapsed_seconds=time.monotonic()-started)
            raise
        message=ToolMessage(content=text,name=action.tool,tool_call_id=request_id,status='error')
    except Exception as exc:
        journal.append('tool_error',request_id=request_id,error_type=type(exc).__name__,elapsed_seconds=time.monotonic()-started)
        raise
    guarded_runtime.verify_messages(access,[message])
    journal.append('tool_return',request_id=request_id,request_digest=binding,message=messages_to_dict([message]),
        budget_after=guarded_runtime.public_budget(access),elapsed_seconds=time.monotonic()-started)
    return message


def bind_common_first_observation(directory, artifact, message):
    # Compare clinical/knowledge payloads, not transport block UUIDs.
    value=dict(plan_id=artifact['plan_id'],tool=message.name,status=message.status,
        observation_sha256=digest(decode_payload(message.content) or message.content))
    path=directory/'first_observation.json'
    with guarded_runtime.output_lease(directory):
        if path.exists():
            if json.loads(path.read_text())!=value:
                raise ValueError('Common first observation differs across arms; stop the comparison')
        else:
            atomic_json(path,value)
    return value


def save_checkpoint(path, state):
    atomic_json(path,state)


async def acquire(cfg, arm, artifact, context, tools, access, directory, recorded, journal, common_directory=None):
    path=directory/'candidate_checkpoint.json'
    expected=digest(dict(access=access.binding,plan_id=artifact['plan_id'],arm=arm))
    if path.exists():
        state=json.loads(path.read_text())
        if state['binding']!=expected:raise ValueError('Candidate checkpoint binding differs')
    else:
        state=dict(binding=expected,arm=arm,phase='acquiring',remaining_plan=deepcopy(artifact['actions']),
            first_action_pending=bool(artifact['actions']),requests=0,policy_turns=0,observations=[],pending_action=None,
            stop_reason=None,stop_explanation=None)
        save_checkpoint(path,state)
    guarded_runtime.verify_messages(access,messages_from_dict(state['observations']))
    by_name={t.name:t for t in tools}
    while state['phase']=='acquiring':
        if state['pending_action']:
            message=await perform_action(Action(**state['pending_action']), 'tool:'+str(state['requests']), by_name, access, journal, cfg.agent.step_timeout)
            if state['requests']==1 and not state['observations'] and common_directory is not None:
                value=bind_common_first_observation(common_directory,artifact,message)
                atomic_json(directory/'first_observation.json',value)
            state['observations'].extend(messages_to_dict([message]))
            state['pending_action']=None
            save_checkpoint(path,state)
            continue
        if state['requests']>=cfg.agent.shared_plan.max_actions:
            state['stop_reason']='action_cap'
        elif not artifact['actions']:
            state['stop_reason']='empty_initial_plan'
        elif state['first_action_pending']:
            action=state['remaining_plan'].pop(0)
            state.update(first_action_pending=False,pending_action=action,requests=state['requests']+1)
            save_checkpoint(path,state)
            continue
        elif arm=='fixed':
            if not state['remaining_plan']:
                state['stop_reason']='fixed_plan_complete'
            else:
                state['pending_action']=state['remaining_plan'].pop(0)
                state['requests']+=1
                save_checkpoint(path,state)
                continue
        elif state['policy_turns']>=cfg.agent.shared_plan.max_policy_turns:
            state['stop_reason']='policy_turn_cap'
        else:
            observations=messages_from_dict(state['observations'])
            maximum=cfg.agent.shared_plan.max_actions-state['requests']
            output_schema=control_schema('remaining_plan',tools,maximum) if repaired(cfg) else None
            schema=PolicyPlan if repaired(cfg) else Decision
            instruction=('Empty remaining_plan means STOP; a nonempty list means EXECUTE its FIRST action. '
                'Do not output an action field. Supply a concise reason of at most 512 characters. '
                if repaired(cfg) else 'execute means perform the FIRST action of remaining_plan, which may replace/reorder the old plan. stop requires an empty remaining_plan. ')
            messages=[SystemMessage(content='[ADAPTIVE_ACQUISITION] After observing the common first action, revise the remaining plan or stop. Return ONLY JSON '+canonical(output_schema or Decision.model_json_schema())),
                HumanMessage(content=context+'\nAvailable tools: '+canonical([dict(name=t.name,description=t.description) for t in tools])+
                    '\nInitial plan: '+canonical(artifact['actions'])+
                    '\nRemaining plan: '+canonical(state['remaining_plan'])+
                    '\nVerified observations: '+canonical(observation_view(observations))+
                    '\nActual budget: '+canonical(guarded_runtime.public_budget(access))+
                    f"\nRemaining action slots: {cfg.agent.shared_plan.max_actions-state['requests']}. "+
                    instruction+'Missing information stays unknown.')]
            def check(decision):
                validate_actions(decision.remaining_plan,tools,cfg.agent.shared_plan.max_actions-state['requests'])
                if isinstance(decision,Decision) and (decision.action=='execute') != bool(decision.remaining_plan):
                    raise ValueError('execute requires a nonempty plan; stop requires empty plan')
            decision=recorded.structured('policy','policy:'+str(state['policy_turns']+1),messages,schema,check,output_schema=output_schema)
            if isinstance(decision,PolicyPlan):
                decision=decision.decision()
            state['policy_turns']+=1
            state['remaining_plan']=[x.model_dump(mode='json') for x in decision.remaining_plan]
            journal.append('policy_decision',policy_turn=state['policy_turns'],decision=decision.model_dump(mode='json'))
            if decision.action=='stop':
                state.update(stop_reason='policy_stop',stop_explanation=decision.reason)
            else:
                state['pending_action']=state['remaining_plan'].pop(0)
                state['requests']+=1
                save_checkpoint(path,state)
                continue
        state['phase']='diagnosis'
        journal.append('acquisition_stop',arm=arm,reason=state['stop_reason'],explanation=state['stop_explanation'],
            requests=state['requests'],policy_turns=state['policy_turns'],budget=guarded_runtime.public_budget(access))
        save_checkpoint(path,state)
    return state


class FormModel:
    """Record every shared formatter attempt, including prefill fallback."""
    def __init__(self, recorded):
        self.recorded=recorded
        self.calls=0

    def invoke(self, messages):
        self.calls+=1
        return self.recorded.invoke('form_fill','form-fill:'+str(self.calls),messages)


async def run_arm(cfg, arm, input_dir, query, output, common_directory, model, token_guard):
    from chimera_agent_baseline.run import _load_mcp_tools, _save_trace
    validate_config(cfg)
    if arm not in ('fixed','adaptive'):raise ValueError('Unknown shared-plan arm')
    local=deepcopy(cfg)
    local.agent.shared_plan.arm=arm
    local.paths.output_dir=str(output/arm)
    case=output/arm/f"task{query['task']}"/query['case_id']
    plan_directory=common_directory/f"task{query['task']}"/query['case_id']
    started=time.monotonic()
    with guarded_runtime.output_lease(case):
        if (case/'prediction.json').exists():
            if not (plan_directory/'initial_plan.json').is_file():
                raise ValueError('Completed arm has lost its common initial plan; do not regenerate')
            if json.loads((plan_directory/'initial_plan.json').read_text())!=json.loads((case/'initial_plan.json').read_text()):
                raise ValueError('Completed arm common plan differs')
            guarded_runtime.verify_completed(local,input_dir,query['task'],query,case)
            checkpoint=json.loads((case/'candidate_checkpoint.json').read_text())
            snapshot=json.loads((case/'information_access_snapshot.json').read_text())
            artifact=json.loads((case/'initial_plan.json').read_text())
            return dict(status='done',resumed=True,case_id=query['case_id'],arm=arm,plan_id=artifact['plan_id'],
                stop_reason=checkpoint['stop_reason'],requests=checkpoint['requests'],policy_turns=checkpoint['policy_turns'],
                spent_units=snapshot['spent_units'],admitted_attempts=snapshot['admitted_attempts'])
        if (case/'.failed').exists() and not cfg.agent.get('retry_failed'):
            raise RuntimeError('Previously failed arm requires explicit retry_failed; original ledger is retained')
        access=None
        try:
            session_path,access,bound=guarded_runtime.prepare_case(local,input_dir,query['task'],query,case)
            tools=await _load_mcp_tools(local,input_dir,f"task{query['task']}",query['task'],session_path)
            artifact,reused=load_or_generate_plan(local,access,bound['context'],tools,plan_directory,model,token_guard)
            atomic_json(case/'initial_plan.json',artifact)
            journal=Journal(case/'candidate_events.jsonl')
            journal.append('initial_plan_bound',plan_id=artifact['plan_id'],shared_plan_reused=reused,
                arm=arm,knowledge_snapshot_id=access.binding['knowledge_snapshot_id'])
            recorded=RecordedModel(model,local,journal,token_guard)
            state=await acquire(local,arm,artifact,bound['context'],tools,access,case,recorded,journal,common_directory=plan_directory)
            observations=messages_from_dict(state['observations'])
            guarded_runtime.verify_messages(access,observations)
            if state['phase']=='diagnosis':
                messages=[SystemMessage(content=diagnostic_system(query['task'])),
                    HumanMessage(content='[COMMON_DIAGNOSIS]\n'+bound['context']+
                        '\nShared initial plan: '+canonical(artifact['actions'])+
                        '\nActual verified observations: '+canonical(observation_view(observations))+
                        '\nActual budget: '+canonical(guarded_runtime.public_budget(access))+
                        '\nProvide a concise clinical assessment using the observed evidence. Tool refusal/no data is not negative clinical evidence. Do not assume unseen results. Acquisition is finished; do not call tools.')]
                response=recorded.diagnostic(messages)
                state.update(phase='form_fill',diagnostic_draft=messages_to_dict([response]))
                save_checkpoint(case/'candidate_checkpoint.json',state)
            if state['phase']=='form_fill':
                import os
                previous=os.environ.get('CHIMERA_OUTPUT_DIR')
                os.environ['CHIMERA_OUTPUT_DIR']=str(output/arm)
                try:
                    fill=make_form_fill_node(FormModel(recorded),max_retries=cfg.agent.form_fill.max_retries,
                        decision_override=False,self_refine=False,atomic_check=False,postprocess=False)
                    result=fill(dict(messages=[HumanMessage(content=bound['context']),*observations,
                        *messages_from_dict(state['diagnostic_draft'])],case_id=query['case_id'],task=query['task'],
                        patient={'psa':query.get('psa'),'age':query.get('age')},information_access=access.snapshot(),case_dir=None))
                finally:
                    if previous is None:os.environ.pop('CHIMERA_OUTPUT_DIR',None)
                    else:os.environ['CHIMERA_OUTPUT_DIR']=previous
                state.update(phase='complete',structured_response=result['structured_response'],
                    processing_stages=result['processing_stages'],form_fill_warnings=result['form_fill_warnings'])
                save_checkpoint(case/'candidate_checkpoint.json',state)
            if state['phase']!='complete':raise ValueError('Unknown candidate checkpoint phase')
            snapshot=access.snapshot()
            if snapshot['pending_requests']:raise RuntimeError('Unsettled acquisition at completion')
            result=dict(structured_response=state['structured_response'],processing_stages=state['processing_stages'],
                form_fill_warnings=state['form_fill_warnings'],
                messages=[HumanMessage(content=bound['context']),*observations,*messages_from_dict(state['diagnostic_draft'])],
                disease_plan={'plan_id':artifact['plan_id'],'actions':artifact['actions']},
                policy_model_turns=state['policy_turns'],policy_trace=[e for e in journal.events if e['kind'] in
                    ('tool_request','tool_return','policy_decision','acquisition_stop')],information_access=snapshot)
            atomic_json(case/'prediction.json',result['structured_response'])
            _save_trace(case,query['case_id'],query['task'],result,time.monotonic()-started)
            (case/'.failed').unlink(missing_ok=True)
            guarded_runtime.save_receipt(access,case)
            return dict(status='done',resumed=False,case_id=query['case_id'],arm=arm,plan_id=artifact['plan_id'],
                stop_reason=state['stop_reason'],requests=state['requests'],policy_turns=state['policy_turns'],
                spent_units=snapshot['spent_units'],admitted_attempts=snapshot['admitted_attempts'],elapsed_seconds=time.monotonic()-started)
        except Exception as exc:
            atomic_json(case/'candidate_failure.json',dict(error_type=type(exc).__name__,error=str(exc),elapsed_seconds=time.monotonic()-started))
            (case/'.failed').write_text(type(exc).__name__+': '+str(exc)+'\n')
            if access is not None:atomic_json(case/'information_access_snapshot.json',access.snapshot())
            raise


async def run_pair(cfg,input_dir,query,output,model,token_guard,order=('fixed','adaptive')):
    if set(order)!={'fixed','adaptive'} or len(order)!=2:raise ValueError('Both arms exactly once required')
    output=Path(output)
    results=[]
    # One process writes its per-case diagnostics at a time; shared plan has a
    # separate lease so different worker processes cannot generate it twice.
    with guarded_runtime.output_lease(output):
        for arm in order:
            try:
                row=await run_arm(cfg,arm,input_dir,query,output,output/'common_plans',model,token_guard)
            except Exception as exc:
                row=dict(status='failed',case_id=query['case_id'],arm=arm,error_type=type(exc).__name__,error=str(exc))
            results.append(row)
    return results
