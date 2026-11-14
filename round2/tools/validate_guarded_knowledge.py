"""Actual pinned hybrid search through runner and MCP; scripted diagnosis only."""
import argparse
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import time
from langchain_core.messages import AIMessage, HumanMessage
from chimera_agent_baseline import run as runner
from chimera_agent_baseline.knowledge_access import load_manifest, sha
from tools.validate_guarded_runner import fixture_config, ScriptedModel

QUERY = 'EAU prostate cancer biopsy indications PI-RADS PSA density'


class KnowledgeModel(ScriptedModel):
    def invoke(self, messages):
        human = next(m.content for m in messages if isinstance(m, HumanMessage))
        if str(messages[0].content).startswith('You are filling out') or 'Produce a 3-5 step' in human:
            return super().invoke(messages)
        self.inputs.append(deepcopy(messages))
        turn = self.turns.get('A', 0)
        self.turns['A'] = turn + 1
        actions = [('get_mri_report', {'case_id': 'A'}), ('search_guidelines', {'query': QUERY}),
                   ('search_guidelines', {'query': QUERY}), ('get_lab_results', {'case_id': 'A'})]
        if self.workflow == 'planned_react':
            actions = actions[1:]
        if turn < len(actions):
            name, args = actions[turn]
            return AIMessage(content='', tool_calls=[dict(id='knowledge-' + str(turn), name=name, args=args, type='tool_call')])
        return AIMessage(content='Synthetic software test completed. The permitted MRI and pinned guideline passages were observed; laboratory data remains unknown.')


async def validate(root, workflow, manifest):
    cfg, input_dir = fixture_config(root, workflow)
    cfg.agent.pids = ['A']
    cfg.agent.information_access.knowledge_manifest = str(manifest.resolve())
    os.environ['CHIMERA_OUTPUT_DIR'] = cfg.paths.output_dir
    os.environ['CHIMERA_STRICT_RUNTIME'] = '1'
    model = KnowledgeModel(workflow)
    started = time.monotonic()
    summaries = []
    assert await runner._run_task(cfg, 1, input_dir, model, 'Synthetic engineering case', task_summaries=summaries) == 1
    case = Path(cfg.paths.output_dir) / 'task1/A'
    snapshot = json.loads((case / 'information_access_snapshot.json').read_text())
    assert snapshot['spent_units'] == 1 and snapshot['admitted_attempts'] == 3
    assert snapshot['visible_sections'] == ['radiology_report']
    assert snapshot['reserved_units'] == snapshot['pending_requests'] == 0
    query_events = [e for e in snapshot['events'] if e.get('tool') == 'search_guidelines']
    assert [e['status'] for e in query_events] == ['reserved', 'returned', 'cache_returned']
    assert all(e.get('charged_units', 0) == 0 for e in query_events)
    trace = json.loads((case / 'trace.json').read_text())
    hits = [m for m in trace['messages'] if m.get('name') == 'search_guidelines']
    assert len(hits) == 2
    from chimera_agent_baseline.agent.evidence_features import decode_payload
    first, cached = [decode_payload(m['content']) for m in hits]
    assert first == cached and first['results']
    assert all(h['knowledge_snapshot_id'] == sha(manifest) and h['provenance'] for h in first['results'])
    assert {h['type'] for h in first['results']} == {'chunk', 'qa'}
    assert all('HIDDEN_LAB_' not in str(m.content) and 'HIDDEN_AUDIT_' not in str(m.content)
               for messages in model.inputs for m in messages)
    before = {n: sha(case / n) for n in ('prediction.json', 'trace.json', 'information_access_receipt.json')}
    calls = len(model.inputs)
    assert await runner._run_task(cfg, 1, input_dir, model, 'Synthetic engineering case') == 0
    assert len(model.inputs) == calls and before == {n: sha(case / n) for n in before}
    load_manifest(manifest)
    result = dict(stage='S2_P5_actual_knowledge_engineering', workflow=workflow, actual_llm_calls=0,
        actual_retrieval_callbacks=1, cached_retrievals=1, synthetic_cases=1, unique_patient_claim=False,
        scripted_model_calls=calls, hit_count=len(first['results']), hit_types=[h['type'] for h in first['results']],
        spent_units=snapshot['spent_units'], admitted_attempts=snapshot['admitted_attempts'],
        source_manifest_sha256=sha(manifest), elapsed_seconds=time.monotonic()-started,
        completed_output_sha256=before, task_summaries=summaries)
    (root / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    return result


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():
        raise SystemExit('Use a new acceptance output directory')
    os.environ['CHIMERA_TRACE_CONTENT_LIMIT']='0'
    for workflow in ('react', 'planned_react'):
        print(json.dumps(asyncio.run(validate(a.output/workflow,workflow,a.manifest))),flush=True)
