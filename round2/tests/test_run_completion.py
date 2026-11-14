"""Batch completion must include previously failed and resumed cases."""

import asyncio
import json
from pathlib import Path

import pytest
from omegaconf import OmegaConf

from chimera_agent_baseline import run as runner

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("retry_failed", [False, True])
@pytest.mark.parametrize("strict", [False, True])
def test_partial_batch_is_not_marked_done(monkeypatch, tmp_path, retry_failed, strict):
    cfg = OmegaConf.load(ROOT / "configs/config.yaml")
    cfg.paths.data_root = str(tmp_path / "data")
    cfg.paths.output_dir = str(tmp_path / "output")
    cfg.agent.tasks = [1]
    cfg.agent.retry_failed = retry_failed
    cfg.agent.predictor.enabled = False
    cfg.agent.predictor.decision_override = False
    cfg.agent.predictor.decision_override_tasks = []
    input_dir = tmp_path / "data/task1/agent_input"
    input_dir.mkdir(parents=True)
    output_dir = tmp_path / "output/task1"
    (output_dir / "resumed").mkdir(parents=True)
    (output_dir / "resumed/prediction.json").write_text('{"existing": true}')
    (output_dir / "old_failure").mkdir(parents=True)
    (output_dir / "old_failure/.failed").write_text("APIConnectionError: old outage")
    queries = [{"case_id": cid, "context": "test", "task": 1}
               for cid in ("resumed", "old_failure", "new_success", "new_failure")]
    attempted = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def get_tools(self):
            return []

    class Graph:
        async def ainvoke(self, state, config):
            attempted.append(state["case_id"])
            if state["case_id"] == "new_failure":
                raise ConnectionError("test outage")
            return {"structured_response": {"ok": True}, "messages": []}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(runner, "load_model", lambda cfg: object())
    monkeypatch.setattr(runner, "load_cases", lambda *args, **kwargs: queries)
    monkeypatch.setattr(runner, "MultiServerMCPClient", Client)
    monkeypatch.setattr(runner, "create_graph", lambda *args, **kwargs: Graph())
    monkeypatch.setenv("CHIMERA_STRICT_RUNTIME", "1" if strict else "0")
    if strict:
        with pytest.raises(RuntimeError, match="Strict experiment incomplete"):
            asyncio.run(runner.run_agent(cfg))
    else:
        asyncio.run(runner.run_agent(cfg))

    manifest = json.loads((tmp_path / "output/run_manifest.json").read_text())
    assert manifest["status"] == "incomplete"
    assert manifest["task_summaries"][0]["expected"] == 4
    assert "resumed" not in attempted
    assert (output_dir / "resumed/prediction.json").read_text() == '{"existing": true}'
    assert ("old_failure" in attempted) == retry_failed
    missing = manifest["task_summaries"][0]["missing_prediction_ids"]
    assert set(missing) == ({"new_failure"} if retry_failed else {"old_failure", "new_failure"})
    if retry_failed:
        assert not (output_dir / "old_failure/.failed").exists()


def test_verified_pin_rejects_changed_evaluator(tmp_path):
    from tools.round2_regression import verify_file

    candidate = tmp_path / "evaluate.py"
    candidate.write_text("changed evaluator")
    with pytest.raises(ValueError, match="Pinned file mismatch"):
        verify_file(candidate, "0" * 64)
