"""Private analysis integration using synthetic outputs, not the study targets."""
from copy import deepcopy
import json
from pathlib import Path
from shutil import copytree
import pytest
from omegaconf import OmegaConf
from tools import analyze_shared_plan_study as analysis
from chimera_agent_baseline.knowledge_access import sha

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/'output/s3_shared_plan_final_20261113/integration/candidate_output'


def toy_study(tmp_path):
    if not FIXTURE.is_dir():pytest.skip('Standalone real-MCP fixture is required')
    results=tmp_path/'results';copytree(FIXTURE,results/'worker0')
    (results/'worker1').mkdir(parents=True)
    protocol_dir=tmp_path/'protocol';protocol_dir.mkdir()
    references=[];runtime=[]
    for cid,label in [('A','yes'),('B','no')]:
        case=protocol_dir/cid;case.mkdir()
        decision=case/'decision.json';decision.write_text(json.dumps(label))
        reasoning=case/'reasoning.json';reasoning.write_text('{"reveal_sequence": []}')
        references.append(dict(case_id=cid,risk_group_id='fixture-'+cid,worker=0,references={
            'decision':dict(path=str(decision),sha256=sha(decision)),
            'reasoning':dict(path=str(reasoning),sha256=sha(reasoning))}))
        receipt=json.loads((results/'worker0/fixed/task1'/cid/'information_access_receipt.json').read_text())
        runtime.append(dict(case_id=cid,clinical_sha256=receipt['binding']['snapshot_id']))
    plan=json.loads((results/'worker0/fixed/task1/A/initial_plan.json').read_text())
    cfg=OmegaConf.merge(OmegaConf.load(ROOT/'configs/config.yaml'),OmegaConf.load(ROOT/'configs/round2_shared_plan_candidate.yaml'))
    cfg.agent.information_access.run_id='synthetic-shared-plan-acceptance-v1'
    for worker in (0,1):
        OmegaConf.save(cfg,protocol_dir/f'worker{worker}.yaml')
        (protocol_dir/f'runtime_worker{worker}.json').write_text(json.dumps({'cases':runtime if worker==0 else []}))
        status=dict(status='completed',expected_arms=4 if worker==0 else 0,arms_attempted=4 if worker==0 else 0,pending_cases=[])
        (results/f'worker{worker}/candidate_batch_status.json').write_text(json.dumps(status))
    evaluator=ROOT/'output/round2_20261110/official_evaluator/evaluate.py';mapping=evaluator.parent/'section_variable_mapping.json'
    protocol=dict(study_id='synthetic-analysis-test',n_case_records=2,cohort_role='synthetic_fixture_not_clinical_performance',
        evaluator=dict(path=str(evaluator),sha256=sha(evaluator),mapping=str(mapping),mapping_sha256=sha(mapping)),
        knowledge_manifest=dict(sha256=plan['binding']['knowledge_snapshot_id']),
        source_files={'src/chimera_agent_baseline/'+k:v for k,v in plan['binding']['code'].items()},
        metrics=dict(statistics=dict(paired_bootstrap_draws=100,bootstrap_seed=20261113)),evaluation_cases=references)
    path=protocol_dir/'protocol.json';path.write_text(json.dumps(protocol))
    (protocol_dir/'protocol_digest.json').write_text(json.dumps(dict(sha256=sha(path),
        config_sha256={str(w):sha(protocol_dir/f'worker{w}.yaml') for w in (0,1)},
        runtime_cohorts={str(w):sha(protocol_dir/f'runtime_worker{w}.json') for w in (0,1)})))
    return path,results


def test_terminal_analysis_keeps_fixed_denominator_and_separates_costs(tmp_path):
    protocol,results=toy_study(tmp_path)
    result=analysis.analyze(protocol,results,tmp_path/'analysis')
    assert result['denominator']==2
    assert result['primary']['accuracy']=={'fixed':.5,'adaptive':.5}
    assert result['primary']['adaptive_minus_fixed']==0
    assert result['secondary']['conditional_information_unit_difference']==-1
    assert result['analysis_model_calls']==0


def test_pending_collection_is_refused_before_reference_analysis(monkeypatch,tmp_path):
    protocol,results=toy_study(tmp_path)
    path=results/'worker0/candidate_batch_status.json'
    value=json.loads(path.read_text());value['status']='running';path.write_text(json.dumps(value))
    monkeypatch.setattr(analysis,'official_evaluator',lambda *_:pytest.fail('Pending run must not be scored'))
    with pytest.raises(ValueError,match='not terminal'):
        analysis.analyze(protocol,results,tmp_path/'analysis')


def test_output_tampering_is_not_treated_as_diagnostic_error(tmp_path):
    protocol,results=toy_study(tmp_path)
    path=results/'worker0/fixed/task1/A/prediction.json';path.write_text('{}')
    with pytest.raises(ValueError,match='receipt differs'):
        analysis.analyze(protocol,results,tmp_path/'analysis')
