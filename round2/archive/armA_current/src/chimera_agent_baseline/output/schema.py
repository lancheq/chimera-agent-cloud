"""Structured output contract for the agent.

This module defines the **submission schema** every agent must produce
to be eligible for evaluation. Outputs that do not validate against
:class:`Task1Output` / :class:`Task2Output` / :class:`Task3Output` are
rejected. Participants may swap models, tools, prompts, and orchestration
freely, but this shape is fixed.

The shape mirrors the urologist forms' review/export page:

* **Task 1** (biopsy decision) — a binary ``biopsy_decision`` plus
  ``confidence``, per-variable ``variable_weights``, and free-text
  ``reasoning``.
* **Task 2** (treatment decision) — a single ``action`` (one of four),
  plus ``confidence``, ``variable_weights``, and ``reasoning``.
* **Task 3** (recurrence prognosis) — a numeric ``months_to_recurrence``
  plus ``reasoning`` (no weights / confidence).

After the ReAct loop produces a final assistant message, the terminal
form-fill node issues a separate prompt-and-parse call that populates
the per-task shape. For tasks 1 and 2 a per-task variable→tool mapping
drives a *dynamic* schema: only variables present in the prompt context
OR backed by a tool the agent actually called appear as required weight
fields for that case. The validated payload is then normalised back to
the full static shape (``not_used`` for omitted variables) so downstream
eval sees a uniform record.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

# ---------------------------------------------------------------------------
# Shared enums — lowercase tokens, matching the urologist forms' export.
# ---------------------------------------------------------------------------


class Weight(StrEnum):
    NOT_USED = "not_used"
    NOTED = "noted"
    IMPORTANT = "important"
    DECISIVE = "decisive"


class Confidence(StrEnum):
    CLEAR = "clear"
    BORDERLINE = "borderline"
    UNCERTAIN = "uncertain"


class TreatmentAction(StrEnum):
    """The four primary management actions for Task-2."""

    ACTIVE_SURVEILLANCE = "active_surveillance"
    CONTINUED_SURVEILLANCE = "continued_surveillance"
    WATCHFUL_WAITING = "watchful_waiting"
    ACTIVE_TREATMENT = "active_treatment"


# ---------------------------------------------------------------------------
# Per-task variable -> tool mapping (drives the variable_weights fields).
# ``None`` means the variable is in the prompt context and so always
# rateable; a string means "this tool must have been called for the
# variable to be rateable". These mirror the forms' reasoning variables.
# ---------------------------------------------------------------------------


TASK1_VARIABLES: dict[str, str | None] = {
    "bx": None,
    "fh": "get_family_history",
    "age": None,
    "dre": None,
    "psa": None,
    "vol": None,
    "psad": None,
    "cspca": None,
    "pirads": None,
    "comorbidity": None,
}


TASK2_VARIABLES: dict[str, str | None] = {
    "ct": None,
    "fh": "get_family_history",
    "age": None,
    "psa": None,
    "psad": None,
    "cspca": None,
    "pirads": None,
    "bx_isup": None,
    "bx_gl_sec": None,
    "bx_gl_prim": None,
    "comorbidity": None,
}


VARIABLES_BY_TASK: dict[int, dict[str, str | None]] = {
    1: TASK1_VARIABLES,
    2: TASK2_VARIABLES,
}


# ---------------------------------------------------------------------------
# Static "full-shape" output models — one per task. Used to validate the
# final record and (tasks 1/2) to normalise the dynamic response back to a
# consistent on-disk shape.
# ---------------------------------------------------------------------------

_REASONING_FIELD = Field(
    min_length=40,
    description="Free-text reasoning: the evidence used and the 2-4 factors that drove the recommendation.",
)
_WEIGHTS_FIELD = Field(
    default_factory=dict,
    description="One weight per reasoning variable (not_used / noted / important / decisive).",
)


class Task1Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    task: Literal[1] = 1
    biopsy_decision: bool = Field(description="True = recommend biopsy, False = defer / no biopsy.")
    confidence: Confidence
    variable_weights: dict[str, Weight] = _WEIGHTS_FIELD
    reveal_sequence: list[
        Literal[
            "family_history",
            "previous_notes",
            "laboratory_results",
            "psa_trend",
            "radiology_report",
        ]
    ] = Field(default_factory=list)
    reasoning: str = _REASONING_FIELD


class Task2Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    task: Literal[2] = 2
    action: TreatmentAction = Field(description="The single recommended management action (one of four).")
    confidence: Confidence
    variable_weights: dict[str, Weight] = _WEIGHTS_FIELD
    reveal_sequence: list[
        Literal[
            "family_history",
            "previous_notes",
            "laboratory_results",
            "psa_trend",
            "radiology_report",
            "pathology_report",
        ]
    ] = Field(default_factory=list)
    reasoning: str = _REASONING_FIELD


class Task3Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    task: Literal[3] = 3
    months_to_recurrence: float = Field(
        ge=0.0,
        description="Predicted time to biochemical recurrence (or last follow-up), in months.",
    )
    reasoning: str = _REASONING_FIELD


TASK_OUTPUT_MODELS: dict[int, type[BaseModel]] = {
    1: Task1Output,
    2: Task2Output,
    3: Task3Output,
}


# ---------------------------------------------------------------------------
# Dynamic schema builder (tasks 1 & 2 — the weight-bearing tasks).
# ---------------------------------------------------------------------------


def eligible_variables(task: int, called_tools: set[str]) -> list[str]:
    """Variables eligible for a weight in the structured output.

    A variable is eligible if its required tool is ``None`` (in the
    prompt) or in ``called_tools`` (the agent actually invoked it during
    the ReAct loop).
    """
    mapping = VARIABLES_BY_TASK[task]
    return [v for v, req in mapping.items() if req is None or req in called_tools]


def build_dynamic_model(task: int, called_tools: set[str]) -> type[BaseModel]:
    """Construct a per-case pydantic model used to constrain the form-fill call.

    For tasks 1 & 2 the loose ``dict[str, Weight]`` is replaced with a
    strict per-variable model enumerating exactly the eligible keys. Task
    3 has no weights, so its static model is used as-is.
    """
    base = TASK_OUTPUT_MODELS[task]
    if task not in VARIABLES_BY_TASK:
        return base

    eligible = eligible_variables(task, called_tools)
    var_fields: dict[str, Any] = {name: (Weight, Field(description=f"Weight for '{name}'.")) for name in eligible}
    DynamicWeights = create_model(  # noqa: N806 — dynamic class name
        f"DynamicTask{task}Weights",
        __config__=ConfigDict(extra="forbid"),
        **var_fields,
    )

    Dynamic = create_model(  # noqa: N806
        f"Dynamic{base.__name__}",
        __base__=base,
        variable_weights=(DynamicWeights, Field(description="Per-variable weights.")),
    )
    return Dynamic


def normalise_to_full_shape(task: int, raw_payload: dict[str, Any]) -> dict[str, Any]:
    """Pad any variable not present in ``raw_payload['variable_weights']``.

    Missing variables get ``"not_used"`` so the on-disk record always has
    the same field set per task. No-op for task 3 (no weights).
    """
    if task not in VARIABLES_BY_TASK:
        return dict(raw_payload)

    full_payload = dict(raw_payload)
    weights = dict(full_payload.get("variable_weights") or {})
    for var in VARIABLES_BY_TASK[task]:
        weights.setdefault(var, Weight.NOT_USED.value)
    full_payload["variable_weights"] = weights
    return full_payload
