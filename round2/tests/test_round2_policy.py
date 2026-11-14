"""Exercise real graph routing with local tools and deterministic model replies."""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

from chimera_agent_baseline.agent import graph as graph_module
from chimera_agent_baseline.agent.policy import validate_policy


class ScriptedModel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.inputs = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.inputs.append(messages)
        return next(self.replies)


def run_graph(monkeypatch, tmp_path, replies, *, workflow="react", max_model_turns=None,
              plan_context_chars=500, context="Case ID: example"):
    calls = []

    @tool
    async def get_lab_results(case_id: str) -> str:
        """Read a local lab record for the requested case."""
        calls.append(case_id)
        return json.dumps({"case_id": case_id, "psa": 5})

    def fill_node(_model, **kwargs):
        def fill(state):
            return {"structured_response": {"tool_results": sum(isinstance(m, ToolMessage)
                                                              for m in state["messages"])}}
        return fill

    monkeypatch.setattr(graph_module, "make_form_fill_node", fill_node)
    monkeypatch.setenv("CHIMERA_OUTPUT_DIR", str(tmp_path))
    model = ScriptedModel(replies)
    graph = graph_module.create_graph(
        [get_lab_results], model, "test system policy", workflow=workflow, max_model_turns=max_model_turns,
        plan_context_chars=plan_context_chars,
    )
    state = asyncio.run(graph.ainvoke({
        "messages": [HumanMessage(content=context)], "case_id": "example", "task": 2,
    }))
    return state, calls, model


def request(call_id="call-1"):
    return AIMessage(content="", tool_calls=[{
        "id": call_id, "name": "get_lab_results", "args": {"case_id": "example"}, "type": "tool_call",
    }])


def test_direct_react_executes_tools_then_stops(monkeypatch, tmp_path):
    state, calls, model = run_graph(monkeypatch, tmp_path, [request(), AIMessage(content="Enough evidence")])
    assert calls == ["example"]
    assert len(model.inputs) == 2
    assert isinstance(model.inputs[0][0], SystemMessage)
    assert "disease_plan" not in state
    assert state["structured_response"]["tool_results"] == 1
    assert [e["action"] for e in state["policy_trace"]] == [
        "request_tools", "observe_results", "finish_evidence",
    ]
    result = state["policy_trace"][1]["results"][0]
    assert result["name"] == "get_lab_results"
    assert result["tool_call_id"] == "call-1"


def test_budget_executes_outstanding_tools_and_reaches_formfill(monkeypatch, tmp_path):
    state, calls, model = run_graph(monkeypatch, tmp_path, [request()], max_model_turns=1)
    assert calls == ["example"]
    assert len(model.inputs) == 1
    assert state["structured_response"]["tool_results"] == 1
    assert state["policy_trace"][-1]["action"] == "stop_for_budget"
    assert not state["messages"][-1].tool_calls


def test_no_tool_decision_goes_directly_to_formfill(monkeypatch, tmp_path):
    state, calls, _ = run_graph(monkeypatch, tmp_path, [AIMessage(content="Enough evidence")], max_model_turns=1)
    assert calls == []
    assert state["structured_response"]["tool_results"] == 0
    assert [e["action"] for e in state["policy_trace"]] == ["finish_evidence"]


def test_planned_default_path_still_executes_planner_tools(monkeypatch, tmp_path):
    state, calls, model = run_graph(monkeypatch, tmp_path, [
        AIMessage(content='Plan: check the lab record\nTOOLS: ["get_lab_results"]'),
        AIMessage(content="Enough evidence"),
    ], workflow="planned_react")
    assert calls == ["example"]
    assert len(model.inputs) == 2
    assert state["disease_plan"]["name"] == "prostate_treatment_decision"
    assert state["policy_trace"][0]["executed_tools"] == ["get_lab_results"]


def test_planned_path_already_preserves_primary_system_prompt(monkeypatch, tmp_path):
    _, _, model = run_graph(monkeypatch, tmp_path, [
        AIMessage(content='Plan: labs\nTOOLS: ["get_lab_results"]'), AIMessage(content="Done"),
    ], workflow="planned_react")
    assert model.inputs[1][0].content == "test system policy"


@pytest.mark.parametrize("limit,tail_visible", [(500, False), (None, True)])
def test_full_planner_context_preserves_late_patient_information(monkeypatch, tmp_path, limit, tail_visible):
    _, _, model = run_graph(monkeypatch, tmp_path, [
        AIMessage(content='TOOLS: ["get_lab_results"]'), AIMessage(content="Done"),
    ], workflow="planned_react", plan_context_chars=limit, context="x" * 2200 + "UNIQUE_LATE_HISTORY")
    assert ("UNIQUE_LATE_HISTORY" in model.inputs[0][1].content) == tail_visible


@pytest.mark.parametrize("workflow,budget,forced", [
    ("unknown", None, False), ("react", 0, False), ("react", -1, False),
    ("react", True, False), ("react", 1.5, False), ("react", None, True),
])
def test_invalid_controls_fail_explicitly(workflow, budget, forced):
    with pytest.raises(ValueError):
        validate_policy(workflow, budget, forced)


def test_trace_status_without_predictor_is_preserved(tmp_path):
    from chimera_agent_baseline.run import _save_trace, _serialize_messages

    _save_trace(tmp_path, "example", 1, {"messages": [], "policy_trace": [{"action": "finish_evidence"}]}, 1.2)
    assert json.loads((tmp_path / "trace.json").read_text())["status"] == "success"
    _save_trace(tmp_path, "example", 1, None, 1.2, ValueError("bad output"))
    assert json.loads((tmp_path / "trace.json").read_text())["status"] == "failed"
    serialized = _serialize_messages([
        request(), ToolMessage(content="result", tool_call_id="call-1", name="get_lab_results"),
    ])
    assert serialized[0]["tool_calls"][0]["id"] == serialized[1]["tool_call_id"]
    assert serialized[1]["name"] == "get_lab_results"
