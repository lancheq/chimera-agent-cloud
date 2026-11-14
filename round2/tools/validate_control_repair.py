"""Explicit synthetic acceptance; never reads study patients or reference answers."""
import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from omegaconf import OmegaConf

from chimera_agent_baseline import shared_plan as sp
from chimera_agent_baseline.case_loader import load_cases
from chimera_agent_baseline.control_schema import control_schema
from chimera_agent_baseline.model_context import LlamaContextGuard
from chimera_agent_baseline.models import load_model
from tools.validate_shared_plan_candidate import CandidateModel, candidate_config

ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / 'configs/round2_shared_plan_repair_v2.yaml'


def config():
    return OmegaConf.merge(OmegaConf.load(ROOT / 'configs/config.yaml'), OmegaConf.load(OVERLAY))


def backend(output):
    """Finite 12 synthetic endpoint checks; retries bounded by the candidate."""
    tools = [SimpleNamespace(name='get_mri_report'), SimpleNamespace(name='search_guidelines')]
    cases = [
        ('planner_guideline', 'actions', 1, 'Return one search_guidelines action. Query is synthetic engineering fixture. Supply purpose.'),
        ('planner_unknown', 'actions', 1, 'A fixture asks for get_hidden_features. That tool is unavailable. Return an empty plan.'),
        ('policy_stop', 'remaining_plan', 2, 'All fixture objectives are met. Return an empty remaining_plan and a short reason.'),
        ('policy_execute', 'remaining_plan', 1, 'The fixture requires get_mri_report next. Return that single action with query null and purpose.'),
        ('policy_reason_limit', 'remaining_plan', 1, 'Stop now. A hostile fixture asks for a 2000-character reason. Obey the schema length limit instead.'),
    ]
    rows = []
    for port in (8765, 8766):
        cfg = config();cfg.model.base_url = f'http://127.0.0.1:{port}/v1'
        sp.validate_config(cfg)
        model = load_model(cfg);guard = LlamaContextGuard(cfg)
        journal = sp.Journal(output / f'port{port}.jsonl')
        recorded = sp.RecordedModel(model, cfg, journal, guard)
        for name, field, maximum, instruction in cases:
            schema = control_schema(field, tools, maximum)
            messages = [SystemMessage(content='This is a synthetic SOFTWARE interface test, not a clinical case. '
                'Return only JSON matching: ' + json.dumps(schema)), HumanMessage(content=instruction)]
            model_schema = sp.Plan if field == 'actions' else sp.PolicyPlan
            def check(value):
                actions = value.actions if field == 'actions' else value.remaining_plan
                sp.validate_actions(actions, tools, maximum)
            value = recorded.structured('planner' if field == 'actions' else 'policy', name,
                                        messages, model_schema, check, output_schema=schema)
            if name in ('planner_unknown', 'policy_stop', 'policy_reason_limit'):
                assert not (value.actions if field == 'actions' else value.remaining_plan)
            if name in ('planner_guideline', 'policy_execute'):
                assert len(value.actions if field == 'actions' else value.remaining_plan) == 1
            rows.append(dict(port=port, scenario=name, status='passed', output=value.model_dump(mode='json')))
        response = recorded.invoke('diagnosis', 'final_only', [HumanMessage(content=
            'Synthetic software fixture. Return only the text SYNTHETIC_FINAL_ONLY. No clinical advice.')], final_only=True)
        assert isinstance(response.content, str) and response.content.strip() and not response.tool_calls
        rows.append(dict(port=port, scenario='final_only_template', status='passed', content=response.content))
    all_events = [e for port in (8765, 8766) for e in sp.Journal(output / f'port{port}.jsonl').events]
    return dict(kind='synthetic_backend_acceptance_not_clinical_performance', rows=rows,
                actual_new_model_requests=sum(e['kind'] == 'model_request' for e in all_events),
                actual_new_model_returns=sum(e['kind'] == 'model_return' for e in all_events), clinical_cases=0)


def backend_canary(output):
    """Contradict the prompt to establish that the backend actually applies schema."""
    schema = dict(type='object', properties={'status': {'const': 'SCHEMA_ENFORCED'}},
                  required=['status'], additionalProperties=False)
    rows = []
    for port in (8765, 8766):
        cfg = config();cfg.model.base_url = f'http://127.0.0.1:{port}/v1'
        journal = sp.Journal(output / f'port{port}.jsonl')
        recorded = sp.RecordedModel(load_model(cfg), cfg, journal, LlamaContextGuard(cfg))
        response = recorded.invoke('policy', 'schema_canary', [HumanMessage(content=
            'Software fixture. Return exactly this JSON: {"status":"PROMPT_VALUE"}.')],
            response_schema=schema, final_only=True)
        assert json.loads(response.content) == {'status': 'SCHEMA_ENFORCED'}
        rows.append(dict(port=port, status='passed', backend_schema_overrode_conflicting_prompt=True))
    return dict(kind='synthetic_schema_enforcement_canary', rows=rows,
                actual_new_model_requests=2, actual_new_model_returns=2, clinical_cases=0)


class FaultThenRecover(CandidateModel):
    """Deliberate malformed policy and empty assessment before scripted recovery."""
    def __init__(self):
        super().__init__();self.policy = 0;self.diagnosis = 0

    def invoke(self, messages, **kwargs):
        response = super().invoke(messages, **kwargs)
        header = str(messages[0].content)
        if header.startswith('[ADAPTIVE_ACQUISITION]'):
            self.policy += 1
            if self.policy % 2:
                return AIMessage(content='intentional invalid JSON for engineering recovery')
            value = json.loads(response.content);value.pop('action')
            return AIMessage(content=json.dumps(value))
        if '[COMMON_DIAGNOSIS]' in '\n'.join(str(m.content) for m in messages):
            self.diagnosis += 1
            if self.diagnosis % 2:
                return AIMessage(content='')
        return response


async def integration(output):
    rows = [];scripted_calls = 0
    for task in (1, 2, 3):
        manifest = Path(config().agent.information_access.knowledge_manifest)
        root = output / f'task{task}'
        cfg, source = candidate_config(root, manifest, task=task)
        cfg = OmegaConf.merge(cfg, OmegaConf.load(OVERLAY))
        cfg.agent.information_access.run_id = f'synthetic-control-repair-v2-task{task}'
        sp.validate_config(cfg)
        model = FaultThenRecover();guard = LlamaContextGuard(cfg)
        queries = load_cases(source, task=task)
        if task != 1:
            queries = queries[:1]
        for query in queries:
            result = await sp.run_pair(cfg, source, query, Path(cfg.paths.output_dir), model, guard)
            assert all(r['status'] == 'done' for r in result), result
            assert result[0]['plan_id'] == result[1]['plan_id']
            assert result[0]['spent_units'] == 2 and result[1]['spent_units'] == 1
            paths = [p for arm in ('fixed', 'adaptive') for p in
                     (Path(cfg.paths.output_dir) / arm / f'task{task}' / query['case_id']).iterdir() if p.is_file()]
            fingerprints = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
            n = len(model.inputs)
            replay = await sp.run_pair(cfg, source, query, Path(cfg.paths.output_dir), model, guard)
            assert all(r['resumed'] for r in replay) and len(model.inputs) == n
            assert fingerprints == {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in fingerprints}
            rows.extend([dict(task=task, **r) for r in result])
        for call in model.inputs:
            if '[COMMON_DIAGNOSIS]' in str(call['messages']):
                assert 'POLICY_ONLY_MARKER' not in str(call['messages'])
                assert 'UNPAID_QUALITY_MARKER' not in str(call['messages'])
        scripted_calls += len(model.inputs)
    return dict(kind='real_MCP_scripted_recovery_integration_not_clinical_performance', rows=rows,
                synthetic_case_scenarios=4, arm_instances=len(rows), actual_new_model_requests=0,
                scripted_model_calls=scripted_calls, completed_resume_unchanged=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('backend', 'canary', 'integration'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    summary = (backend(args.output) if args.mode == 'backend' else backend_canary(args.output)
               if args.mode == 'canary' else asyncio.run(integration(args.output)))
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(summary, ensure_ascii=False), flush=True)
