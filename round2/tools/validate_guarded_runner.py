"""Synthetic runner/MCP acceptance. Scripted replies, zero actual LLM calls."""
import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import os

from langchain_core.messages import AIMessage, HumanMessage
from omegaconf import OmegaConf
from chimera_agent_baseline import run as runner
from chimera_agent_baseline.output.schema import eligible_variables
from chimera_agent_baseline.tools.base import CASE_DATA_FILENAMES_BY_TASK

ROOT = Path(__file__).resolve().parents[1]


def fixture_config(root, workflow='react', task=1):
    cfg = OmegaConf.load(ROOT / 'configs/config.yaml')
    cfg.paths.data_root = str(root / 'data')
    cfg.paths.output_dir = str(root / 'output')
    cfg.agent.tasks = [task]
    cfg.agent.workflow = workflow
    cfg.agent.plan_context_chars = None
    cfg.agent.max_model_turns = 5
    cfg.agent.predictor.enabled = False
    cfg.agent.predictor.decision_override = False
    cfg.agent.predictor.decision_override_tasks = []
    cfg.agent.form_fill.atomic_check = False
    cfg.agent.information_access = dict(enabled=True, run_id='synthetic-engineering-v1', budget_units=1, max_attempts=6)
    input_dir = root / f'data/task{task}/agent_input'
    for cid in ('A', 'B'):
        case = input_dir / cid
        case.mkdir(parents=True, exist_ok=True)
        (case / 'prompt.json').write_text(json.dumps(dict(case_id=cid, task=task, age=65, psa=5)))
        (case / CASE_DATA_FILENAMES_BY_TASK[task]).write_text(json.dumps(dict(
            case_id=cid, radiology_report='SYNTHETIC_MRI_' + cid,
            laboratory_results=[{'val': 'HIDDEN_LAB_' + cid}], pathology_report='HIDDEN_PATH_' + cid)))
        # Neither sidecar nor its label may be used by any model consumer.
        (case / 'data_quality_annotations.json').write_text(json.dumps({'annotation': 'HIDDEN_AUDIT_' + cid}))
    return cfg, input_dir


class ScriptedModel:
    """Software fixture; these arbitrary labels carry no clinical meaning."""
    def __init__(self, workflow='react', task=1):
        self.workflow = workflow
        self.task = task
        self.inputs = []
        self.turns = {}
        self.bindings = []

    def bind_tools(self, tools):
        self.bindings.append([t.name for t in tools])
        return self

    def invoke(self, messages):
        self.inputs.append(deepcopy(messages))
        human = next(m.content for m in messages if isinstance(m, HumanMessage))
        cid = re.search(r'Case ID: ([AB])', human)[1]
        if str(messages[0].content).startswith('You are filling out'):
            judgment = dict(repeat_test=None,
                free_text='Synthetic engineering response; this is a software fixture with no clinical interpretation.')
            if self.task in (1, 2):
                judgment.update(confidence='uncertain', variable_weights={k: 'not_used' for k in eligible_variables(self.task, {'get_mri_report'})})
            if self.task == 1:
                judgment['biopsy_decision'] = 'yes'
            elif self.task == 2:
                judgment['treatment_recommendation'] = {'primary': 'active_surveillance'}
            else:
                judgment.update(event=0, months_to_recurrence=24.0)
            raw = json.dumps(judgment)
            return AIMessage(content=raw[1:] if messages[-1].content == '{' else raw)
        if 'Produce a 3-5 step evidence gathering plan' in human:
            return AIMessage(content='Plan: obtain the MRI report.\nTOOLS: ["get_mri_report"]')
        turn = self.turns.get(cid, 0)
        self.turns[cid] = turn + 1
        actions = [('get_mri_report', cid), ('get_mri_report', cid),
                   ('get_pathology_report' if self.task == 3 else 'get_lab_results', cid), ('get_mri_report', 'B' if cid == 'A' else 'A')]
        if self.workflow == 'planned_react':
            actions = actions[1:]
        if turn < len(actions):
            name, case = actions[turn]
            return AIMessage(content='', tool_calls=[dict(id=f'{cid}-{turn}', name=name,
                args={'case_id': case}, type='tool_call')])
        return AIMessage(content='Synthetic test finished using the permitted MRI report. Unobserved laboratory information remains unknown.')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def validate(root, workflow, task=1):
    cfg, input_dir = fixture_config(root, workflow, task)
    os.environ['CHIMERA_OUTPUT_DIR'] = cfg.paths.output_dir
    os.environ['CHIMERA_STRICT_RUNTIME'] = '1'
    model = ScriptedModel(workflow, task)
    summaries = []
    assert await runner._run_task(cfg, task, input_dir, model, 'Synthetic software test', task_summaries=summaries) == 2
    snapshots = {}
    before = {}
    for cid in ('A', 'B'):
        output = Path(cfg.paths.output_dir) / f'task{task}' / cid
        snapshots[cid] = json.loads((output / 'information_access_snapshot.json').read_text())
        s = snapshots[cid]
        assert s['spent_units'] == 1 and s['reserved_units'] == s['pending_requests'] == 0
        assert s['visible_sections'] == ['radiology_report'] and s['admitted_attempts'] == 2
        assert any(e.get('status') == 'cross_case' and e.get('allowed') is False for e in s['events'])
        trace = json.loads((output / 'trace.json').read_text())
        assert trace['status'] == 'success'
        assert any(e['stage'] == 'postprocessing' and e['status'] == 'disabled' for e in trace['processing_stages'])
        prediction = json.loads((output / 'prediction.json').read_text())
        assert [e['key'] for e in prediction['reveal_sequence']] == ['section_s3-mri']
        before[cid] = {n: sha(output / n) for n in ('prediction.json', 'trace.json', 'information_access_receipt.json')}
    for messages in model.inputs:
        text = '\n'.join(str(m.content) for m in messages)
        cid = re.search(r'Case ID: ([AB])', text)[1]
        assert 'HIDDEN_LAB_' not in text and 'HIDDEN_AUDIT_' not in text and 'HIDDEN_PATH_' not in text
        other = 'B' if cid == 'A' else 'A'
        assert 'SYNTHETIC_MRI_' + other not in text
    assert all('get_image_predictor' not in names and 'search_guidelines' not in names for names in model.bindings)
    calls = len(model.inputs)
    assert await runner._run_task(cfg, task, input_dir, model, 'Synthetic software test') == 0
    assert len(model.inputs) == calls
    for cid in ('A', 'B'):
        output = Path(cfg.paths.output_dir) / f'task{task}' / cid
        assert {n: sha(output / n) for n in before[cid]} == before[cid]
    result = dict(workflow=workflow, task=task, synthetic_cases=2, actual_llm_calls=0,
                  scripted_model_calls=calls, summaries=summaries,
                  snapshots=snapshots, resume_preserved=before)
    (root / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Use a new synthetic artifact directory to preserve prior acceptance evidence')
    for task in (1, 2, 3):
        for workflow in ('react', 'planned_react'):
            result = asyncio.run(validate(args.output / f'task{task}' / workflow, workflow, task))
            print(json.dumps({k: result[k] for k in ('task', 'workflow', 'synthetic_cases', 'actual_llm_calls', 'scripted_model_calls')}), flush=True)
