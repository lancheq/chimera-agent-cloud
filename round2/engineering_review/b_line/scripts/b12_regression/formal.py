"""Run existing root-entry contracts on identical frozen source with/without B12."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CANDIDATE = Path('chimera_agent_baseline/agent/b12_evidence_state.py')
MODULE = 'chimera_agent_baseline.agent.b12_evidence_state'
SUITES = (
    'tests/test_b_mcp_sdk_compatibility.py',
    'tests/test_b_evidence_reconciliation_build.py',
    'tests/test_information_access.py',
    'tests/test_adaptive_budget_close.py',
    'tests/test_adaptive_host_smoke.py',
    'tests/test_p01_g1t_sdk_request_boundary.py',
)


def main(mode, target):
    assert mode in ('baseline', 'candidate')
    target.mkdir(parents=True)
    for name in ('inference.py', 'reasoning_align.py'):
        shutil.copyfile(ROOT/name, target/name)
    overlay = target/'src'
    shutil.copytree(ROOT/'src', overlay, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    if mode == 'baseline':
        (overlay/CANDIDATE).unlink()
    sys.path[:0] = [str(overlay), str(target)]
    import chimera_agent_baseline
    assert Path(chimera_agent_baseline.__file__).resolve().is_relative_to(overlay)
    spec = importlib.util.find_spec(MODULE)
    assert (spec is not None) == (mode == 'candidate')
    default_off = None
    if spec is not None:
        from chimera_agent_baseline.agent.b12_evidence_state import derive_state
        default_off = derive_state(b'invalid disabled probe', None, 'disabled') is None
        assert default_off
    import inference
    import reasoning_align
    assert Path(inference.__file__).resolve() == target / "inference.py"
    assert Path(reasoning_align.__file__).resolve() == target / "reasoning_align.py"
    import pytest

    class SerialPolicy(asyncio.DefaultEventLoopPolicy):
        def new_event_loop(self):
            loop = super().new_event_loop()
            loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
            return loop

    policy = SerialPolicy()
    policy.set_child_watcher(asyncio.SafeChildWatcher())
    asyncio.set_event_loop_policy(policy)
    from preregister import pin
    print(f'{mode}: overlay B12 present={spec is not None}, candidate default off={default_off}', flush=True)
    tests = [str(ROOT/p) for p in SUITES]
    rc = pytest.main(['-q', '-p', 'no:cacheprovider', '-o', 'junit_family=xunit2',
                      '--basetemp', str(target/'cases'), '--junitxml', str(target/'results.xml'), *tests])
    (target/'mode.json').write_text(json.dumps({'mode': mode, 'same_formal_source': True,
        'old_candidate_absent': spec is None, 'default_off': default_off, 'exit': int(rc),
        'fixture_stdio_source': 'frozen shared root, no B12 consumer', 'suites': list(SUITES),
        'overlay_candidate_sha256': pin(overlay/CANDIDATE)['sha256'] if spec is not None else None},indent=2)+'\n')
    return int(rc)

if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1], Path(sys.argv[2]).resolve()))
