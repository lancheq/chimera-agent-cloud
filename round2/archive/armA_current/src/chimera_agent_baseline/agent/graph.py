"""LangGraph ReAct + form-fill graph.

The graph has three nodes:

1. **agent**     -- LLM call with the case-specific tools bound (ReAct).
2. **tools**     -- executes any tool the agent called.
3. **form_fill** -- terminal node that prompts the SAME model for the
   structured-output JSON. Implementation lives in
   :mod:`chimera_agent_baseline.agent.form_fill`.

The agent loops between *agent* and *tools* until it stops issuing tool
calls and produces a final assistant message. The router then routes to
*form_fill*, which produces ``state["structured_response"]`` for
:mod:`chimera_agent_baseline.run` to persist.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from chimera_agent_baseline.agent.form_fill import make_form_fill_node

log = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    """State for the ReAct + form-fill graph.

    ``messages`` and the ``add_messages`` reducer are the LangGraph ReAct
    primitives. The other fields carry per-case context for the form-fill
    node:

    * ``task`` — 1 (biopsy decision) or 2 (treatment decision). Drives
      which set of reasoning variables / Pydantic model is used.
    * ``case_id`` — included in the structured response.
    * ``structured_response`` — populated by ``form_fill`` and read by
      :mod:`chimera_agent_baseline.run` after graph completion.
    * ``form_fill_warnings`` — diagnostics (validation retries, post-hoc
      downgrades). Empty list when everything is clean.
    """

    messages: Annotated[list, add_messages]
    task: int
    case_id: str
    structured_response: dict[str, Any]
    form_fill_warnings: list[str]


def _route_after_agent(state: AgentState) -> str:
    """If the last message has tool calls, run them; otherwise fill the form."""
    return "tools" if tools_condition(state) == "tools" else "form_fill"


def create_graph(
    tools: list[BaseTool],
    model: BaseChatModel,
    system_prompt: str,
    step_timeout: int = 120,
    form_fill_max_retries: int = 3,
):
    """Build and compile the ReAct + form-fill graph.

    The same ``model`` is used for both phases:

    * The ReAct loop binds the case tools and runs cyclically.
    * The terminal form-fill node prompts the model with a per-case
      Pydantic skeleton and parses the response with
      :class:`PydanticOutputParser`, retrying on validation errors.
    """
    model_with_tools = model.bind_tools(tools)

    def agent(state: AgentState) -> dict[str, Any]:
        messages = state["messages"]
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=system_prompt), *messages]
        response = model_with_tools.invoke(messages)
        return {"messages": [response]}

    builder = StateGraph(AgentState)
    builder.add_node("agent", agent)
    builder.add_node("tools", ToolNode(tools))
    builder.add_node("form_fill", make_form_fill_node(model, max_retries=form_fill_max_retries))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", _route_after_agent, {"tools": "tools", "form_fill": "form_fill"})
    builder.add_edge("tools", "agent")
    builder.add_edge("form_fill", END)

    graph = builder.compile()
    graph.step_timeout = step_timeout
    log.info("Compiled ReAct+form_fill graph with %d tools", len(tools))
    return graph
