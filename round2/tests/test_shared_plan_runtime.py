"""Regression for the actual-import vs declared-snapshot deployment mismatch."""
import hashlib
from pathlib import Path

import pytest
from tools.verify_shared_plan_runtime import check_environment, check_module, worker_command


def protocol(tmp_path):
    root = tmp_path / 'accepted'
    p = root / 'src/chimera_agent_baseline/shared_plan.py'
    p.parent.mkdir(parents=True)
    p.write_text('accepted_v2 = True\n')
    return dict(source_snapshot=str(root), source_files={str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()}), p


def test_correct_snapshot_environment_is_accepted(tmp_path):
    p, _ = protocol(tmp_path)
    root = Path(p['source_snapshot'])
    check_environment(p, {'PYTHONPATH': ':'.join(map(str, (root/'src', root, root/'tests')))}, root)


def test_new_entrypoint_with_old_pythonpath_is_rejected(tmp_path):
    p, _ = protocol(tmp_path)
    root = Path(p['source_snapshot'])
    with pytest.raises(ValueError, match='PYTHONPATH'):
        check_environment(p, {'PYTHONPATH': '/old/src:/old:/old/tests'}, root)


def test_old_working_directory_is_rejected(tmp_path):
    p, _ = protocol(tmp_path)
    root = Path(p['source_snapshot'])
    env = {'PYTHONPATH': ':'.join(map(str, (root/'src', root, root/'tests')))}
    with pytest.raises(ValueError, match='working directory'):
        check_environment(p, env, tmp_path)


def test_actual_import_from_other_snapshot_is_rejected(tmp_path):
    p, _ = protocol(tmp_path)
    wrong = tmp_path/'old.py';wrong.write_text('legacy = True\n')
    with pytest.raises(ValueError, match='escapes'):
        check_module('shared_plan', wrong, p)


def test_correct_file_with_wrong_bytes_is_rejected(tmp_path):
    p, file = protocol(tmp_path)
    file.write_text('legacy = True\n')
    with pytest.raises(ValueError, match='hash differs'):
        check_module('shared_plan', file, p)


def test_correct_actual_import_is_recorded(tmp_path):
    p, file = protocol(tmp_path)
    result = check_module('shared_plan', file, p)
    assert result['path'] == str(file) and len(result['sha256']) == 64


def test_bootstrap_only_executes_verified_worker(tmp_path):
    import sys
    p, _ = protocol(tmp_path)
    p['execution_commands'] = [[sys.executable, str(Path(p['source_snapshot'])/'tools/run_shared_plan_candidate.py'),
        '--config', 'cfg', '--cohort', 'cohort', '--output', 'new-output', '--execute']]
    assert worker_command(p, 0) == p['execution_commands'][0]
    p['execution_commands'][0][1] = '/old/tools/run_shared_plan_candidate.py'
    with pytest.raises(ValueError, match='entry differs'):
        worker_command(p, 0)
