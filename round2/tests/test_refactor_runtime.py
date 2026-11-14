import json

import pytest
from langchain_core.messages import AIMessage

from chimera_agent_baseline.agent import form_fill as ff
from chimera_agent_baseline.agent.fact_check import FactCheckResult
from chimera_agent_baseline.agent.predictor_node import make_predictor_node
from chimera_agent_baseline.rag import GuidelinesSearch
from chimera_agent_baseline.runtime_audit import ComponentFailure


class Model:
    def __init__(self, payload):
        self.payload = payload

    def invoke(self, messages):
        if isinstance(self.payload, str):
            return AIMessage(content=self.payload)
        raw = json.dumps(self.payload)
        return AIMessage(content=raw[1:] if messages[-1].type == "ai" else raw)


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("CHIMERA_OUTPUT_DIR", str(tmp_path))
    monkeypatch.delenv("CHIMERA_STRICT_RUNTIME", raising=False)
    monkeypatch.setattr(ff, "_T1_DL_MODE", "off")
    monkeypatch.setattr(ff, "_T1_DECISION_LAYER", False)
    return {"task": 1, "case_id": "synthetic", "patient": {"age": 65, "psa": 5},
            "messages": [AIMessage(content="Synthetic context for a parser and routing regression only.")]}


@pytest.mark.parametrize("task", [1, 2, 3])
def test_complete_parse_failure_is_audited_and_strictly_rejected(runtime, monkeypatch, tmp_path, task):
    runtime["task"] = task
    result = ff.make_form_fill_node(Model("unparseable"), max_retries=1)(runtime)
    assert any("DEFAULTED" in w for w in result["form_fill_warnings"])
    assert result["processing_stages"][0]["status"] == "fallback"
    monkeypatch.setenv("CHIMERA_STRICT_RUNTIME", "1")
    with pytest.raises(ComponentFailure, match="parsed_judgment"):
        ff.make_form_fill_node(Model("unparseable"), max_retries=1)(runtime)
    audit = json.loads((tmp_path / f"task{task}/synthetic/processing_audit.json").read_text())
    assert audit["stages"][-1]["fallback"] == "legacy_decision_recovery"


def test_verification_and_decision_layer_have_distinct_snapshots(runtime, monkeypatch):
    payload = ff._default_judgment(1)
    payload.update(biopsy_decision="yes", confidence="clear", free_text="Synthetic diagnostic reasoning used only for this regression fixture.")
    monkeypatch.setattr(ff, "_T1_DL_MODE", "v2")
    monkeypatch.setattr(ff, "_T1_DECISION_LAYER", True)
    monkeypatch.setattr(ff, "_T1_DL2_CACHE", {"feature_names": ["llm_yes"], "coef": [-1], "intercept": 0})
    runtime["predictor_decision"] = {"probabilities": {"no": 0.6, "yes": 0.4}}

    def verify(task, cid, transcript, judgment, eligible, model):
        judgment["confidence"] = "borderline"
        return FactCheckResult(warnings=["synthetic verification"])

    monkeypatch.setattr(ff, "atomic_fact_check", verify)
    result = ff.make_form_fill_node(Model(payload), atomic_check=True)(runtime)
    stages = {s["stage"]: s for s in result["processing_stages"]}
    assert stages["parsed_judgment"]["after"]["biopsy_decision"] == "yes"
    assert stages["verified_judgment"]["after"]["confidence"] == "borderline"
    assert stages["t1_decision_layer"]["before"]["biopsy_decision"] == "yes"
    assert stages["t1_decision_layer"]["after"]["biopsy_decision"] == "no"
    assert payload["biopsy_decision"] == "yes"


def test_predictor_failure_legacy_fallback_and_strict_rejection(runtime, monkeypatch):
    class BrokenPredictor:
        available_tasks = [1]

        def predict(self, *args, **kwargs):
            raise ValueError("corrupt predictor")

    runtime["case_dir"] = "/synthetic"
    node = make_predictor_node(BrokenPredictor())
    assert node(runtime)["predictor_decision"] == {}
    monkeypatch.setenv("CHIMERA_STRICT_RUNTIME", "1")
    with pytest.raises(ComponentFailure, match="corrupt predictor"):
        node(runtime)


def test_missing_reranker_requires_explicit_disable_in_strict_mode(runtime, monkeypatch, tmp_path):
    search = object.__new__(GuidelinesSearch)
    monkeypatch.setenv("CHIMERA_RERANKER", "on")
    assert search._load_reranker(tmp_path / "missing") is None
    monkeypatch.setenv("CHIMERA_STRICT_RUNTIME", "1")
    with pytest.raises(ComponentFailure, match="reranker_load"):
        search._load_reranker(tmp_path / "missing")
    monkeypatch.setenv("CHIMERA_RERANKER", "off")
    assert search._load_reranker(tmp_path / "missing") is None


def test_missing_embedding_socket_is_not_silently_dense_success(runtime, monkeypatch, tmp_path):
    import chimera_agent_baseline.rag as rag

    monkeypatch.setattr(rag, "SOCKET_PATH", tmp_path / "nonexistent.sock")
    search = object.__new__(GuidelinesSearch)
    assert search._dense_search("synthetic query", 1) == []
    monkeypatch.setenv("CHIMERA_STRICT_RUNTIME", "1")
    with pytest.raises(ComponentFailure, match="dense_retrieval"):
        search._dense_search("synthetic query", 1)


@pytest.mark.parametrize("strict", [False, True])
def test_case_lookup_keeps_valid_payload_and_exposes_unknown_patient(runtime, monkeypatch, tmp_path, strict):
    from chimera_agent_baseline.mcp_server import _register_precomputed_tool
    from chimera_agent_baseline.tools.base import CaseDataStore, ToolSpec

    data = tmp_path / "task1/agent_input/known"
    data.mkdir(parents=True)
    (data / "prostate-biopsy-decision-clinical-data.json").write_text(json.dumps({
        "case_id": "known", "radiology_report": "synthetic report",
    }))
    captured = []

    class Registration:
        def tool(self):
            def register(fn):
                captured.append(fn)
                return fn
            return register

    _register_precomputed_tool(Registration(), CaseDataStore(data.parent),
                              ToolSpec("get_mri_report", "Synthetic test", ("radiology_report",)))
    monkeypatch.setenv("CHIMERA_STRICT_RUNTIME", "1" if strict else "0")
    assert json.loads(captured[0]("known")) == {"case_id": "known", "radiology_report": "synthetic report"}
    if strict:
        with pytest.raises(ComponentFailure, match="case_tool_lookup"):
            captured[0]("missing")
    else:
        assert "error" in json.loads(captured[0]("missing"))
