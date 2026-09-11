"""Structured output contract for the agent.

This module defines the **submission schema** every agent must produce
to be eligible for evaluation. Outputs that do not validate against
:class:`Task1Output` / :class:`Task2Output` / :class:`Task3Output` are
rejected. Participants may swap models, tools, prompts, and orchestration
freely, but this shape is fixed.

The shape mirrors the urologist forms' review/export page (``target.json`` /
``target_task2.json`` / ``target_task3.json`` convention), including a
``reveal_sequence`` — the ordered list of tool calls ("reveals") the agent
made while working the case:

* **Task 1** (biopsy decision) — ``biopsy_decision`` ("yes"/"no") plus
  ``confidence``, per-variable ``variable_weights``, ``reveal_sequence``,
  ``repeat_test``, and free-text ``free_text``.
* **Task 2** (treatment decision) — a ``treatment_recommendation`` object
  (primary action + modalities/detail/surveillance protocol), plus
  ``confidence``, ``variable_weights``, ``reveal_sequence``, ``repeat_test``,
  and ``free_text``.
* **Task 3** (recurrence prognosis) — an ``event`` indicator and a numeric
  ``months_to_recurrence`` plus ``reveal_sequence``, ``repeat_test``, and
  ``free_text`` (no weights / confidence).

After the ReAct loop produces a final assistant message, the terminal
form-fill node issues a separate prompt-and-parse call that populates a
*judgment* model — the subset of the full record the LLM actually reasons
about (decision/action, confidence, variable_weights, repeat_test,
free_text). :func:`assemble_full_output` then merges that judgment with the
programmatic case fields (``case_id``, ``patient``, ``reveal_sequence``,
derived from the run itself, not the LLM) and validates the result against
the full ``Task<N>Output`` model. For tasks 1 and 2 a per-task
variable→tool mapping drives a *dynamic* judgment schema: only variables
present in the prompt context OR backed by a tool the agent actually called
appear as required weight fields for that case. The validated payload is
then normalised back to the full static shape (``not_used`` for omitted
variables) so downstream eval sees a uniform record.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, create_model

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


class BiopsyDecision(StrEnum):
    """Task-1 biopsy recommendation, as the export's yes/no token."""

    YES = "yes"
    NO = "no"


class TreatmentAction(StrEnum):
    """The four primary management actions for Task-2."""

    ACTIVE_SURVEILLANCE = "active_surveillance"
    CONTINUED_SURVEILLANCE = "continued_surveillance"
    WATCHFUL_WAITING = "watchful_waiting"
    ACTIVE_TREATMENT = "active_treatment"


# ---------------------------------------------------------------------------
# Tool -> reveal_sequence key/label mapping. Tools without a fixed entry
# (e.g. ``search_guidelines``) fall back to a generic key/label derived
# from the tool name (see :func:`reveal_info_for_tool`).
# ---------------------------------------------------------------------------

TOOL_REVEAL_INFO: dict[str, tuple[str, str]] = {
    "get_mri_report": ("section_s3-mri", "Radiology / MRI report"),
    "get_lab_results": ("section_s3-labs", "Laboratory results"),
    "get_psa_trend": ("section_s3-psa", "PSA trend"),
    "get_previous_notes": ("section_s3-prev", "Previous notes"),
    "get_family_history": ("section_s3-fh", "Family history (anamnesis)"),
    "get_pathology_report": ("section_s3-path", "Pathology report"),
    "get_surgical_pathology_report": ("section_s3-surgpath", "Surgical pathology report"),
}


def reveal_info_for_tool(tool_name: str) -> tuple[str, str]:
    """Return ``(key, label)`` for a tool name, falling back to a generic pair."""
    if tool_name in TOOL_REVEAL_INFO:
        return TOOL_REVEAL_INFO[tool_name]
    return f"section_{tool_name}", tool_name.replace("_", " ")


# ---------------------------------------------------------------------------
# Per-task naming / versioning for the export envelope.
# ---------------------------------------------------------------------------

TASK_NAME: dict[int, str] = {
    1: "urologist_biopsy_decision_cot",
    2: "urologist_treatment_decision_cot",
    3: "urologist_recurrence_prognosis_cot",
}

TASK_SCHEMA_VERSION: dict[int, str] = {
    1: "1.4",
    2: "1.0",
    3: "1.0",
}

# Wrapper key + source blurb used when bundling a single validated case into
# the target.json-style envelope (see :func:`wrap_prediction`).
TASK_WRAPPER_KEY: dict[int, str] = {
    1: "biopsy_decision",
    2: "treatment_decision",
    3: "recurrence_prognosis",
}

TASK_SOURCE_TEXT: dict[int, str] = {
    1: (
        "LLM agent (mimic-pathologist) form responses. Same schema as target.json (1.4). "
        "Clear cases match the target; borderline cases reflect realistic LLM disagreement "
        "patterns so the evaluator has signal across decision/confidence/weights/tools/rationale."
    ),
    2: (
        "LLM agent (mimic-urologist) treatment-decision form responses. Same schema as "
        "target_task2.json (1.0). Clear cases generally match target; borderline/discordant "
        "cases include realistic disagreement patterns."
    ),
    3: (
        "LLM agent (mimic-urologist) recurrence-prognosis form responses. Same schema as "
        "target_task3.json (1.0). Clear cases generally match target; borderline/discordant "
        "cases include realistic disagreement patterns."
    ),
}


# ---------------------------------------------------------------------------
# Per-task variable -> tool mapping (drives the variable_weights fields).
# ``None`` means the variable is in the prompt context and so always
# rateable; a string means "this tool must have been called for the
# variable to be rateable". These mirror the forms' reasoning variables.
# ---------------------------------------------------------------------------


TASK1_VARIABLES: dict[str, str | None] = {
    "psa": None,
    "age": None,
    "dre": None,
    "comorbidity": None,
    "bx": None,
    "pirads": None,
    "psad": None,
    "vol": None,
    "cspca": None,
    "fh": "get_family_history",
}


TASK2_VARIABLES: dict[str, str | None] = {
    "psa": None,
    "age": None,
    "ct": None,
    "comorbidity": None,
    "pirads": None,
    "psad": None,
    "cspca": None,
    "bx_gl_prim": None,
    "bx_gl_sec": None,
    # NB: no "bx_gl_tert". The platform's task-2 socket schema rejects it:
    #   instance Additional properties are not allowed ('bx_gl_tert' was
    #   unexpected)
    # GC run f698486a. docs/CHIMERA-agent赛事整理.md:103 also lists exactly 11
    # task-2 variables and omits it, so the platform and the docs agree and this
    # dict was the outlier. Because normalise_to_full_shape() pads every key
    # listed here, keeping it guaranteed an invalid key in EVERY task-2 output.
    "bx_isup": None,
    "fh": "get_family_history",
}


VARIABLES_BY_TASK: dict[int, dict[str, str | None]] = {
    1: TASK1_VARIABLES,
    2: TASK2_VARIABLES,
}


# ---------------------------------------------------------------------------
# Shared sub-objects.
# ---------------------------------------------------------------------------

_REASONING_FIELD = Field(
    min_length=40,
    description="Free-text reasoning: the evidence used and the 2-4 factors that drove the recommendation.",
)
_WEIGHTS_FIELD = Field(
    default_factory=dict,
    description="One weight per reasoning variable (not_used / noted / important / decisive).",
)


class Patient(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    psa_ng_ml: float | None = None
    age_years: float | None = None


class RevealEntry(BaseModel):
    """One tool call the agent made, recorded as a form "reveal" event."""

    model_config = ConfigDict(extra="forbid")

    page: str
    order: int
    key: str
    label: str
    value: Literal["section"] = "section"
    via: Literal["tool_call"] = "tool_call"
    ts: str


class TreatmentRecommendation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    primary: TreatmentAction | None = Field(default=None, description="The single recommended management action (one of four).")
    modalities: list[str] = Field(
        default_factory=list, description="Specific modalities, when primary is an active treatment."
    )
    detail: str | None = Field(default=None, description="Free-text detail on the recommendation.")
    as_protocol: str | None = Field(
        default=None, description="Surveillance protocol description, when primary is a surveillance action."
    )
    as_trigger: str | None = Field(
        default=None, description="Trigger for escalation out of surveillance, when applicable."
    )


# ---------------------------------------------------------------------------
# Static "full-shape" output models — one per task. Used to validate the
# final record, assembled by :func:`assemble_full_output` from the LLM
# judgment plus the programmatic case fields (case_id, patient,
# reveal_sequence).
# ---------------------------------------------------------------------------


class Task1Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.4"] = "1.4"
    task: Literal["urologist_biopsy_decision_cot"] = "urologist_biopsy_decision_cot"
    case_type: Literal["dataset"] = "dataset"
    case_id: str
    patient: Patient
    biopsy_decision: BiopsyDecision
    confidence: Confidence
    variable_weights: dict[str, Weight] = _WEIGHTS_FIELD
    reveal_sequence: list[RevealEntry] = Field(default_factory=list)
    repeat_test: str | None = None
    free_text: str = _REASONING_FIELD


class Task2Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    task: Literal["urologist_treatment_decision_cot"] = "urologist_treatment_decision_cot"
    case_type: Literal["dataset"] = "dataset"
    case_id: str
    patient: Patient
    treatment_recommendation: TreatmentRecommendation
    confidence: Confidence
    variable_weights: dict[str, Weight] = _WEIGHTS_FIELD
    reveal_sequence: list[RevealEntry] = Field(default_factory=list)
    repeat_test: str | None = None
    free_text: str = _REASONING_FIELD


class Task3Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    task: Literal["urologist_recurrence_prognosis_cot"] = "urologist_recurrence_prognosis_cot"
    case_type: Literal["dataset"] = "dataset"
    case_id: str
    patient: Patient
    event: int = Field(
        ge=0,
        le=1,
        description=(
            "Recurrence indicator: 1 if biochemical recurrence is predicted to occur, "
            "0 if censored / no recurrence by last follow-up."
        ),
    )
    months_to_recurrence: float = Field(
        ge=0.0,
        description="Predicted time to biochemical recurrence (or last follow-up), in months.",
    )
    reveal_sequence: list[RevealEntry] = Field(default_factory=list)
    repeat_test: str | None = None
    free_text: str = _REASONING_FIELD


TASK_OUTPUT_MODELS: dict[int, type[BaseModel]] = {
    1: Task1Output,
    2: Task2Output,
    3: Task3Output,
}


# ---------------------------------------------------------------------------
# LLM "judgment" models — the subset of each output the model actually
# fills in. Programmatic fields (case_id, patient, reveal_sequence,
# schema_version, task, case_type) are known ahead of time from the run
# itself and merged in afterwards by :func:`assemble_full_output` — the
# model is never asked to reproduce data it doesn't need to reason about.
# ---------------------------------------------------------------------------


class Task1Judgment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    biopsy_decision: BiopsyDecision | None = None
    confidence: Confidence | None = None
    variable_weights: dict[str, Weight] = _WEIGHTS_FIELD
    repeat_test: str | None = None
    free_text: str | None = None


class Task2Judgment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    treatment_recommendation: TreatmentRecommendation | None = None
    confidence: Confidence | None = None
    variable_weights: dict[str, Weight] = _WEIGHTS_FIELD
    repeat_test: str | None = None
    free_text: str | None = None


class Task3Judgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event: int = Field(
        ge=0,
        le=1,
        description=(
            "Recurrence indicator: 1 if biochemical recurrence is predicted to occur, "
            "0 if censored / no recurrence by last follow-up."
        ),
    )
    months_to_recurrence: float = Field(ge=0.0, description="Predicted months to recurrence / last follow-up.")
    repeat_test: str | None = None
    free_text: str = _REASONING_FIELD


TASK_JUDGMENT_MODELS: dict[int, type[BaseModel]] = {
    1: Task1Judgment,
    2: Task2Judgment,
    3: Task3Judgment,
}


# ---------------------------------------------------------------------------
# Dynamic schema builder (tasks 1 & 2 — the weight-bearing tasks).
# ---------------------------------------------------------------------------


def _blank_weight_to_not_used(value: Any) -> Any:
    """Coerce blank/whitespace weight tokens to 'not_used' before enum validation.

    LLM outputs occasionally emit ``""`` or whitespace for a weight (e.g.
    ``fh: ""``). StrEnum validation rejects those, which previously aborted a
    whole parse attempt; here they fall back to NOT_USED so the case proceeds.
    """
    if isinstance(value, str) and not value.strip():
        return Weight.NOT_USED.value
    return value


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
    3 has no weights, so its judgment model is used as-is.
    """
    base = TASK_JUDGMENT_MODELS[task]
    if task not in VARIABLES_BY_TASK:
        return base

    eligible = eligible_variables(task, called_tools)
    var_fields: dict[str, Any] = {
        name: (
            Annotated[Weight, BeforeValidator(_blank_weight_to_not_used)],
            Field(
                default=Weight.NOT_USED.value,
                description=f"Weight for '{name}'.",
            ),
        )
        for name in eligible
    }
    DynamicWeights = create_model(  # noqa: N806 — dynamic class name
        f"DynamicTask{task}Weights",
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


def assemble_full_output(
    task: int,
    case_id: str,
    patient: dict[str, Any],
    reveal_sequence: list[dict[str, Any]],
    judgment: dict[str, Any],
) -> dict[str, Any]:
    """Merge the LLM judgment with the programmatic case fields.

    Pads ``variable_weights`` to the full static shape, adds ``case_id``,
    ``patient``, and ``reveal_sequence``, then validates against the full
    ``Task<N>Output`` model (``schema_version`` / ``task`` / ``case_type``
    fall back to their fixed per-task defaults).
    """
    full = normalise_to_full_shape(task, judgment)
    full["case_id"] = case_id
    full["patient"] = patient
    full["reveal_sequence"] = reveal_sequence
    model = TASK_OUTPUT_MODELS[task](**full)
    return model.model_dump(mode="json")


def wrap_prediction(task: int, prediction: dict[str, Any]) -> dict[str, Any]:
    """Bundle a single validated case record into the target.json-style envelope."""
    return {
        "source": TASK_SOURCE_TEXT[task],
        "schema_version": TASK_SCHEMA_VERSION[task],
        TASK_WRAPPER_KEY[task]: [prediction],
    }
