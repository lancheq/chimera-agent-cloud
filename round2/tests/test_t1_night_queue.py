import json
import os
import pytest
from tools.run_t1_until_deadline import owned_pid
from tools.summarize_t1_contract_validation import summarize, ARM


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_deadline_guard_refuses_an_unrelated_process(tmp_path):
    write(tmp_path / 'launcher.json', {'worker_pid': os.getpid()})
    with pytest.raises(RuntimeError, match='ownership'):
        owned_pid({'run_dir': str(tmp_path)})


def test_repeated_generations_keep_patient_denominator(tmp_path):
    write(tmp_path / 'protocol.json', {'mechanism': 'synthetic fixture', 'replicates': [{'name': 'a'}, {'name': 'b'}]})
    cases = [{'case_id': str(i), 'label': 'yes', 'initial_judgment': 'no',
              'final_decision': {ARM: 'yes'}} for i in range(91)]
    for name in ('a', 'b'):
        run = tmp_path / name
        write(run / 'status.json', {'status': 'complete'})
        write(run / 'oof_actual_tools/cases.json', cases)
        write(run / 'oof_observed_training/cases.json', cases)
    result = summarize(tmp_path)
    assert result['complete']
    assert result['n_unique_patients'] == 91
    assert result['n_generated_trajectories'] == 182
    assert result['patient_cluster_mean_accuracy_difference'] == 1
    assert result['patient_cluster_se'] == 0
