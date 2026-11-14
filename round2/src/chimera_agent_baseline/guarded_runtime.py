"""Opt-in evidence-only runner binding and verified consumer view.

Unverified predictor/modality paths fail explicitly instead of reading full files.
No training, hidden feature reconstruction, or diagnostic decision rules.
"""
from copy import deepcopy
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import sys
from importlib.metadata import version
from pathlib import Path
from omegaconf import OmegaConf
from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException
from chimera_agent_baseline.information_access import InformationAccess, AccessDenied, canonical, digest
from chimera_agent_baseline.agent.evidence_features import decode_payload
from chimera_agent_baseline.tools.base import CASE_DATA_FILENAMES_BY_TASK


def enabled(cfg):return bool(cfg.agent.get('information_access',{}).get('enabled',False))


@contextmanager
def output_lease(output):
    """Do not let two runners overwrite the same task's receipts or traces."""
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.guarded_runner.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Guarded output already has an active runner') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def validate_options(cfg,predictor=None):
    if not enabled(cfg):return
    p=cfg.agent.get('predictor',{})
    if predictor is not None or p.get('enabled') or p.get('decision_override') or p.get('decision_override_tasks'):
        raise ValueError('Guarded runner: predictor/modality authorization not verified; explicitly disable predictor and override')
    access=cfg.agent.information_access
    if not isinstance(access.get('run_id'),str) or not access.run_id.strip():raise ValueError('Guarded run_id required')
    for key in ('budget_units','max_attempts'):
        if type(access.get(key)) is not int or access[key]<0:raise ValueError('Guarded '+key+' must be a nonnegative integer')
    cap=cfg.agent.get('max_model_turns')
    if type(cap) is not int or cap<1:raise ValueError('Guarded max_model_turns must be explicitly positive')
    if cfg.agent.get('force_rag_grayzone'):raise ValueError('Guarded knowledge provenance not yet verified; forced retrieval unavailable')


def prepare_case(cfg,input_dir,task,query,case_output):
    validate_options(cfg)
    cid=query['case_id']
    if cid in ('.','..') or Path(cid).name!=cid or '/' in cid or '\\' in cid:raise ValueError('Unsafe case id')
    source=Path(input_dir)/cid
    prompts=[source/n for n in ('prompt.json','structured-prompt.json') if (source/n).is_file()]
    if not prompts:raise ValueError('Missing upfront source')
    raw=json.loads(prompts[0].read_text())
    if any(json.loads(p.read_text())!=raw for p in prompts[1:]):raise ValueError('Conflicting prompt aliases')
    if raw.get('case_id')!=cid or int(raw.get('task',task))!=task:raise ValueError('Upfront identity differs')
    if set(raw)&{'ground_truth','reference_label','reference_reasoning','data_quality_annotations'}:raise ValueError('Reference/quality audit fields cannot be upfront')
    from chimera_agent_baseline.case_loader import render_baseline_prompt
    # Official release prompts may omit task. Bind routing metadata from the
    # task directory before rendering; otherwise Jinja selects biopsy for T2/T3.
    context=render_baseline_prompt({**raw, 'task':task})
    profile={'agent':OmegaConf.to_container(cfg.agent,resolve=True),
        'generation':OmegaConf.to_container(cfg.get('generation',{}),resolve=True) if OmegaConf.is_config(cfg.get('generation',{})) else dict(cfg.get('generation',{})),
        'model':{k:cfg.get('model',{}).get(k) for k in ('provider','model_id','base_url','extra_body')},
        'policy_environment':{k:os.environ.get(k) for k in ('CHIMERA_T1_TOOL_DENY','CHIMERA_T1_PROMPT','CHIMERA_PLAN_TOOLS_FIX','CHIMERA_FORMFILL_SELF_REFINE')},
        'code':{name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in
            ('guarded_runtime.py','run.py','information_access.py','agent/graph.py','agent/form_fill.py','agent/prompts.py')}}
    # Scheduling/resume switches do not change a case's acquisition policy.
    for key in ('retry_failed','pids','limit','tasks'):
        profile['agent'].pop(key,None)
    # Convert nested Hydra model settings without ever reading model API secrets.
    profile['model']=OmegaConf.to_container(OmegaConf.create(profile['model']),resolve=True)
    from chimera_agent_baseline.agent.prompts import build_system_prompt
    profile['system_prompt_sha256']=query.get('_system_prompt_digest') or hashlib.sha256(build_system_prompt(task).encode()).hexdigest()
    profile['upfront_context_sha256']=hashlib.sha256(context.encode()).hexdigest()
    profile['runtime']={'python':sys.executable, 'packages':{name:version(name) for name in
        ('langchain-core','langgraph','langchain-mcp-adapters','mcp','omegaconf','pydantic')}}
    profile['code']={str(p.relative_to(Path(__file__).parent)):hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted(Path(__file__).parent.rglob('*.py')) if not p.name.startswith('._')}
    clinical=source/CASE_DATA_FILENAMES_BY_TASK[task]
    config={'ledger_path':str((case_output/'information_access.sqlite').resolve()),
        'session_id':cfg.agent.information_access.run_id+':'+digest(profile), 'case_id':cid,'task':task,
        'snapshot_id':hashlib.sha256(clinical.read_bytes()).hexdigest(),
        'budget_units':cfg.agent.information_access.budget_units,'max_attempts':cfg.agent.information_access.max_attempts,
        'initial_source_sha256':hashlib.sha256(prompts[0].read_bytes()).hexdigest()}
    from chimera_agent_baseline.information_access import tool_contract
    fields={f for x in tool_contract(task)['tools'].values() for f in x['fields']}
    config['initial_sections']={k:raw[k] for k in fields if k in raw}
    knowledge_manifest=cfg.agent.information_access.get('knowledge_manifest')
    if knowledge_manifest:
        from chimera_agent_baseline.knowledge_access import load_manifest, sha
        manifest=Path(knowledge_manifest).resolve()
        load_manifest(manifest, require_alignment=True)
        config['knowledge_manifest']=str(manifest)
        config['knowledge_snapshot_id']=sha(manifest)
    case_output.mkdir(parents=True,exist_ok=True);path=case_output/'access_session.json'
    if path.exists() and json.loads(path.read_text())!=config:raise ValueError('Existing case session differs; use a new output directory, not a reset ledger')
    access=InformationAccess.from_config(input_dir,config)
    if access.snapshot()['pending_requests']:raise ValueError('Unresolved in-flight reservation; reconcile before resuming')
    if not path.exists():path.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n')
    bound=deepcopy(query)
    # Only the structured upfront file is exposed. No clinical/audit/reference
    # sidecar or image vector file is sent to any model stage.
    bound['context']=context+'\n\n[Complete supplied upfront record; not newly acquired clinical data]\n'+canonical(raw)
    return path,access,bound


EXPECTED_DENIALS={'information_budget_exhausted','attempt_budget_exhausted','cross_case',
    'unknown_or_disabled_tool','request_pending','knowledge_snapshot_unverified','invalid_query'}


def expected_tool_error(error: ToolException) -> str:
    """Let the policy observe expected refusals; infrastructure errors abort."""
    if any('information_access:' + code in str(error) for code in EXPECTED_DENIALS):
        return 'Error: ' + str(error)
    raise error


def verify_messages(access,messages):
    for m in messages:
        if not isinstance(m,ToolMessage):continue
        is_error=getattr(m,'status','success')=='error' or str(m.content).startswith('Error:')
        if is_error:
            if not any('information_access:'+code in str(m.content) for code in EXPECTED_DENIALS):
                raise AccessDenied('Unexpected tool failure in guarded graph')
            m.status='error';m.additional_kwargs['information_access_expected_denial']=True
            m.additional_kwargs['information_access_clinical_success']=False
            continue
        spec=access.contract['tools'].get(m.name)
        if m.name == 'search_guidelines':
            value=decode_payload(m.content)
            if not value or canonical(value) != canonical(access.require_knowledge(value.get('query'))):
                raise AccessDenied('Tool knowledge result does not match granted source')
            m.additional_kwargs['information_access_clinical_success']=False
            m.additional_kwargs['information_access_knowledge_success']=True
            continue
        if spec is None:raise AccessDenied('Successful unverified tool result')
        value=decode_payload(m.content)
        if not value or value.get('case_id')!=access.binding['case_id'] or set(value)-{'case_id','note',*spec['fields']}:
            raise AccessDenied('Invalid clinical tool observation')
        fields=[f for f in spec['fields'] if f in value]
        visible=access.require_visible(access.binding['case_id'],fields)
        if any(canonical(value[f])!=canonical(visible[f]) for f in fields):raise AccessDenied('Tool result does not match granted source')
        m.additional_kwargs['information_access_clinical_success']=bool(fields)
    return access.snapshot()


def public_budget(access):
    s=access.snapshot()
    return {k:s[k] for k in ('spent_units','reserved_units','admitted_attempts','budget_units','max_attempts','visible_sections')}


def _file_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_receipt(access, case_output):
    snapshot = access.snapshot()
    receipt = {'binding': access.binding, 'ledger_snapshot_digest': digest(snapshot),
               'prediction_sha256': _file_sha(case_output / 'prediction.json'),
               'trace_sha256': _file_sha(case_output / 'trace.json'),
               'candidate_artifacts': {n:_file_sha(case_output/n) for n in
                   ('candidate_events.jsonl','candidate_checkpoint.json','initial_plan.json','first_observation.json') if (case_output/n).is_file()}}
    (case_output / 'information_access_snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
    path = case_output / 'information_access_receipt.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    temporary.replace(path)


def verify_completed(cfg, input_dir, task, query, case_output):
    path = case_output / 'information_access_receipt.json'
    if not path.is_file() or (case_output / '.failed').exists():
        raise ValueError('Completed guarded case requires a valid receipt; do not reuse unguarded/failed output')
    _, access, _ = prepare_case(cfg, input_dir, task, query, case_output)
    expected = json.loads(path.read_text())
    current = {'binding': access.binding, 'ledger_snapshot_digest': digest(access.snapshot()),
               'prediction_sha256': _file_sha(case_output / 'prediction.json'),
               'trace_sha256': _file_sha(case_output / 'trace.json'),
               'candidate_artifacts': {n:_file_sha(case_output/n) for n in
                   ('candidate_events.jsonl','candidate_checkpoint.json','initial_plan.json','first_observation.json') if (case_output/n).is_file()}}
    if expected != current:
        raise ValueError('Completed guarded receipt differs; preserve original outputs and investigate')
