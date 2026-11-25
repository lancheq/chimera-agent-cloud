"""Terminal form-fill node — prompt + parse against the output schema.

The ReAct loop ends when the model stops issuing tool calls. The router
then routes to this node, which prompts the same model to emit a JSON
object matching the per-case Pydantic shape (built dynamically from
:mod:`chimera_agent_baseline.output.schema`) and validates with
:class:`langchain_core.output_parsers.PydanticOutputParser`.

We deliberately avoid ``model.with_structured_output`` — it relies on
function-calling support that varies wildly across providers (Gemma 4's
offline tool-call parser, for instance, sometimes does not emit a tool
call when there is only one forced schema-tool). Prompt-and-parse is
provider-neutral: any LangChain ``BaseChatModel`` works.

The node retries on validation errors up to ``max_retries`` times and
raises if every attempt fails — there is no partial / stub fallback, so
an unfillable case aborts the run loudly rather than writing a
half-formed prediction.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.output_parsers import PydanticOutputParser

from chimera_agent_baseline.output.schema import (
    VARIABLES_BY_TASK,
    build_dynamic_model,
    eligible_variables,
    normalise_to_full_shape,
)

log = logging.getLogger(__name__)

_REVEAL_FIELD_BY_TOOL: dict[int, dict[str, str]] = {
    1: {
        "get_family_history": "family_history",
        "get_previous_notes": "previous_notes",
        "get_lab_results": "laboratory_results",
        "get_psa_trend": "psa_trend",
        "get_mri_report": "radiology_report",
    },
    2: {
        "get_family_history": "family_history",
        "get_previous_notes": "previous_notes",
        "get_lab_results": "laboratory_results",
        "get_psa_trend": "psa_trend",
        "get_mri_report": "radiology_report",
        "get_pathology_report": "pathology_report",
    },
}

_SYSTEM_PROMPT = (
    "You are filling out the structured decision form for the prostate-cancer "
    "case you just analysed. The user message contains your reasoning "
    "transcript and the tools you called. Output a SINGLE JSON object matching "
    "the supplied schema EXACTLY — no extra keys, no markdown fences, no "
    "commentary before or after the JSON."
)


def make_form_fill_node(model: BaseChatModel, max_retries: int = 3):
    """Return a LangGraph node closure that captures the unbound *model*.

    *max_retries* is the number of validation attempts before the node
    raises :class:`RuntimeError`. A successful attempt yields a payload
    that already validates against the per-task output model
    (Task1Output / Task2Output / Task3Output).
    """

    def form_fill(state: dict[str, Any]) -> dict[str, Any]:
        messages = state["messages"]
        task = int(state.get("task", 1))
        case_id = state.get("case_id", "unknown")

        called = _called_tools_from_messages(messages)
        reveal_sequence = _reveal_sequence_from_messages(messages, task)
        transcript = _final_assistant_text(messages)

        elig = eligible_variables(task, called) if task in VARIABLES_BY_TASK else []

        Dynamic = build_dynamic_model(task, called)
        parser = PydanticOutputParser(pydantic_object=Dynamic)
        skeleton = _build_skeleton_instructions(task, elig)

        log.info(
            "form_fill: case=%s task=%d called_tools=%s eligible_vars=%s",
            case_id,
            task,
            sorted(called) or [],
            elig,
        )

        base_user = _user_prompt(case_id, task, transcript, sorted(called), elig) + "\n\n" + skeleton

        # Provider-agnostic retry loop. We keep the conversation flat — a
        # single system + user pair, regenerated on retry — because small
        # models can be derailed by long error-laden histories. The retry
        # message names the missing/invalid fields explicitly.
        warnings: list[str] = []
        structured_response: dict[str, Any] | None = None
        retry_hint: str | None = None

        for attempt in range(1, max_retries + 1):
            user_content = base_user if not retry_hint else f"{base_user}\n\n{retry_hint}"
            try:
                response = model.invoke(
                    [
                        SystemMessage(content=_SYSTEM_PROMPT),
                        HumanMessage(content=user_content),
                    ]
                )
                raw = response.content if isinstance(response.content, str) else json.dumps(response.content)
                obj = parser.parse(_extract_json_object(raw))
                structured_response = obj.model_dump(mode="json")
                break
            except Exception as exc:  # noqa: BLE001 — any parse/validation failure triggers a retry
                warnings.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
                log.warning("form_fill parse failed (attempt %d) for %s: %s", attempt, case_id, exc)
                if attempt == max_retries:
                    break
                retry_hint = (
                    "Your previous attempt did not validate. The validation "
                    f"error was:\n{exc}\n\n"
                    "Emit the JSON object exactly matching the shape above. "
                    "Every required key must appear. Output ONLY the JSON."
                )

        if structured_response is None:
            raise RuntimeError(
                f"form_fill: case {case_id}: all {max_retries} attempts failed to "
                f"produce schema-valid output. form_fill_warnings={warnings}"
            )

        # Pad omitted variables to "not_used" so downstream eval sees the
        # full static shape.
        if task in _REVEAL_FIELD_BY_TOOL:
            structured_response["reveal_sequence"] = reveal_sequence

        full = normalise_to_full_shape(task, structured_response)
        return {"structured_response": full, "form_fill_warnings": warnings}

    return form_fill


# ---------------------------------------------------------------------------
# Prompt builders + helpers
# ---------------------------------------------------------------------------


def _user_prompt(
    case_id: str,
    task: int,
    transcript: str,
    called_tools: list[str],
    eligible: list[str],
) -> str:
    tools_line = ", ".join(called_tools) if called_tools else "(no tools called)"
    head = (
        f"Case ID: {case_id}\n"
        f"Task: {task}\n\n"
        "Your reasoning transcript (final assistant message from the ReAct "
        'loop):\n"""\n'
        f"{transcript}\n"
        '"""\n\n'
        f"Tools you called during the ReAct loop: {tools_line}\n\n"
    )
    if task not in VARIABLES_BY_TASK:
        return head + (
            "Now fill out the form: give your predicted months to recurrence "
            "(a non-negative number) and a focused reasoning naming the 2-4 "
            "factors that most influenced your estimate."
        )
    return head + (
        "Variables you may weight (you may ONLY weight these — every other "
        "variable was either out of scope for this task or behind a tool you "
        "did not call):\n"
        f"  {', '.join(eligible) if eligible else '(none)'}\n\n"
        "Now fill out the form. Weight each variable (not_used / noted / "
        "important / decisive); give an overall confidence; and a focused "
        "reasoning naming the 2-4 factors that most influenced your "
        "recommendation."
    )


def _build_skeleton_instructions(task: int, eligible: list[str]) -> str:
    """Concrete JSON-skeleton format instructions.

    PydanticOutputParser's ``get_format_instructions()`` dumps the full
    JSON schema (with ``$defs``, ``$ref``, ``additionalProperties``,
    etc.). Small models tested in the wild (Gemma 4 E2B) sometimes echo
    that schema back verbatim instead of producing an instance. A
    concrete shape with placeholder values is far more robust and is
    still unambiguous about which keys are required.
    """
    if task == 3:
        skeleton = "\n".join(
            [
                "{",
                '  "case_id": "<the case id>",',
                '  "task": 3,',
                '  "months_to_recurrence": <number — predicted months to recurrence / last follow-up>,',
                '  "reasoning": "<at least 40 chars; the evidence and the 2-4 factors that drove your estimate>"',
                "}",
            ]
        )
        return (
            "Output a SINGLE JSON object matching exactly this shape (replace "
            "every <...> with a real value):\n\n"
            f"{skeleton}\n\n"
            "Do NOT emit any other keys. Do NOT wrap in markdown fences. Do NOT echo the schema."
        )

    weight_enum = '"not_used" | "noted" | "important" | "decisive"'
    confidence_enum = '"clear" | "borderline" | "uncertain"'

    if task == 1:
        decision_line = '  "biopsy_decision": <true | false>,'
    else:
        action_enum = '"active_surveillance" | "continued_surveillance" | "watchful_waiting" | "active_treatment"'
        decision_line = f'  "action": <{action_enum}>,'

    weight_lines = [f'    "{var}": <{weight_enum}>,' for var in eligible]
    if weight_lines:
        weight_lines[-1] = weight_lines[-1].rstrip(",")

    skeleton = "\n".join(
        [
            "{",
            '  "case_id": "<the case id>",',
            f'  "task": {task},',
            decision_line,
            f'  "confidence": <{confidence_enum}>,',
            '  "variable_weights": {',
            *weight_lines,
            "  },",
            '  "reasoning": "<at least 40 chars; name the 2-4 factors that drove your call>"',
            "}",
        ]
    )

    return (
        "Output a SINGLE JSON object matching exactly this shape (replace "
        "every <...> with a real value):\n\n"
        f"{skeleton}\n\n"
        f"`variable_weights` MUST include all and only these keys: {', '.join(eligible)}.\n"
        "Use the weight 'not_used' for any variable that did not influence your "
        "decision. Do NOT emit any other keys at the top level. Do NOT wrap in "
        "markdown fences. Do NOT echo the schema."
    )


def _extract_json_object(text: str) -> str:
    """Best-effort extraction of a JSON object from raw model output."""
    if not text:
        return text
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        return fence.group(1)
    start = text.find("{")
    if start < 0:
        return text
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]

def _reveal_sequence_from_messages(messages: list, task: int) -> list[str]:
    """Return the clinical-data sections accessed through tool calls."""

    mapping = _REVEAL_FIELD_BY_TOOL.get(task, {})
    reveal_sequence: list[str] = []

    for message in messages:
        if not isinstance(message, ToolMessage) or not message.name:
            continue

        field = mapping.get(message.name)
        if field is not None and field not in reveal_sequence:
            reveal_sequence.append(field)

    return reveal_sequence

def _called_tools_from_messages(messages: list) -> set[str]:
    return {m.name for m in messages if isinstance(m, ToolMessage) and m.name}


def _final_assistant_text(messages: list) -> str:
    for m in reversed(messages):
        if isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None):
            return m.content if isinstance(m.content, str) else json.dumps(m.content)
    return ""
