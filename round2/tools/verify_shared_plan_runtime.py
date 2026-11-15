"""Verify actual import provenance and exercise the v2 output adapter WITHOUT generation."""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_environment(protocol, environment, cwd):
    snapshot = Path(protocol['source_snapshot']).resolve()
    if Path(cwd).resolve() != snapshot:
        raise ValueError('Actual working directory differs from declared source snapshot')
    expected = [snapshot / 'src', snapshot, snapshot / 'tests']
    actual = [Path(p).resolve() for p in environment.get('PYTHONPATH', '').split(os.pathsep) if p]
    if actual != expected:
        raise ValueError('Actual PYTHONPATH differs from declared source snapshot')


def check_module(name, path, protocol):
    path = Path(path).resolve()
    root = Path(protocol['source_snapshot']).resolve()
    if not path.is_relative_to(root):
        raise ValueError('Imported module escapes declared snapshot: ' + name)
    relative = str(path.relative_to(root))
    if protocol['source_files'].get(relative) != sha(path):
        raise ValueError('Imported module hash differs: ' + name)
    return dict(path=str(path), sha256=sha(path))


def verify(protocol_path, worker):
    protocol = json.loads(protocol_path.read_text())
    pin = json.loads((protocol_path.parent / 'protocol_digest.json').read_text())
    if sha(protocol_path) != pin['sha256']:
        raise ValueError('Protocol digest differs')
    check_environment(protocol, os.environ, Path.cwd())
    from omegaconf import OmegaConf
    from langchain_core.messages import AIMessage, HumanMessage
    from chimera_agent_baseline import shared_plan as sp
    from chimera_agent_baseline.control_schema import control_schema
    cfg_path = protocol_path.parent / f'worker{worker}.yaml'
    if sha(cfg_path) != pin['config_sha256'][str(worker)]:
        raise ValueError('Worker config digest differs')
    cfg = OmegaConf.load(cfg_path)
    sp.validate_config(cfg)
    if cfg.agent.shared_plan.control_interface_version != 2 or not sp.repaired(cfg):
        raise ValueError('The actual runtime does not implement accepted v2')
    for name in ('chimera_agent_baseline.shared_plan', 'chimera_agent_baseline.control_schema',
                 'chimera_agent_baseline.model_context', 'chimera_agent_baseline.guarded_runtime',
                 'chimera_agent_baseline.case_loader', 'chimera_agent_baseline.models',
                 'tools.run_shared_plan_candidate', 'tools.analyze_shared_plan_study', 'tools.verify_shared_plan_study'):
        importlib.import_module(name)
    modules = {}
    for name, module in list(sys.modules.items()):
        if name.startswith('chimera_agent_baseline') or name in (
                'tools.run_shared_plan_candidate', 'tools.analyze_shared_plan_study', 'tools.verify_shared_plan_study'):
            path = getattr(module, '__file__', None)
            if path:
                modules[name] = check_module(name, path, protocol)
    tools = [SimpleNamespace(name='get_mri_report')]
    schema = control_schema('remaining_plan', tools, 1)
    class Capture:
        calls = 0
        def invoke(self, messages, **kwargs):
            self.calls += 1
            if kwargs.get('response_format', {}).get('json_schema', {}).get('schema') != schema:
                raise ValueError('Actual adapter did not attach constrained v2 schema')
            return AIMessage(content='{"remaining_plan": [], "reason": "Synthetic import check"}')
    capture = Capture()
    with tempfile.TemporaryDirectory(prefix='chimera-runtime-probe-') as directory:
        journal = sp.Journal(Path(directory) / 'synthetic.jsonl')
        recorded = sp.RecordedModel(capture, cfg, journal, lambda *_: {'fits': True, 'synthetic': True})
        decision = recorded.structured('policy', 'probe', [HumanMessage(content='Synthetic software check')],
                                       sp.PolicyPlan, lambda _: None, output_schema=schema)
        if decision.decision().action != 'stop' or capture.calls != 1:
            raise ValueError('v2 single-plan control check failed')
    return dict(status='actual_v2_runtime_verified_no_generation', worker=worker, modules=modules,
                real_model_calls=0, scripted_adapter_checks=1, response_schema_attached=True)


def worker_command(protocol, worker):
    command = protocol['execution_commands'][worker]
    root = Path(protocol['source_snapshot']).resolve()
    if Path(command[0]).resolve() != Path(sys.executable).resolve():
        raise ValueError('Worker Python interpreter differs')
    if Path(command[1]).resolve() != root / 'tools/run_shared_plan_candidate.py':
        raise ValueError('Worker entry differs from verified snapshot')
    if len(command) != 9 or command[2::2] != ['--config', '--cohort', '--output', '--execute']:
        raise ValueError('Unexpected worker execution arguments')
    return command


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--worker', type=int, choices=(0, 1), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--execute-worker', action='store_true', help='After provenance succeeds, execute the pinned worker in THIS process')
    args = parser.parse_args()
    try:
        result = verify(args.protocol.resolve(), args.worker)
    except Exception as exc:
        with args.output.open('x') as handle:
            json.dump(dict(status='rejected_before_generation', error_type=type(exc).__name__, error=str(exc)), handle, indent=2)
        raise
    with args.output.open('x') as handle:
        json.dump(result, handle, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != 'modules'}), flush=True)
    if args.execute_worker:
        import runpy
        command = worker_command(json.loads(args.protocol.read_text()), args.worker)
        sys.argv = command[1:]
        runpy.run_path(command[1], run_name='__main__')
