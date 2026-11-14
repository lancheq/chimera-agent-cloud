"""Observable execution controls; no diagnosis rules or reward labels.

Events describe actions actually taken. They are suitable for paired ablations
and trajectory export, but do not establish causal effects or an RL reward.
"""

from __future__ import annotations

from typing import Any


def validate_policy(workflow: str, max_model_turns: int | None, force_rag_grayzone: bool) -> None:
    if workflow not in {"planned_react", "react"}:
        raise ValueError(f"Unknown workflow {workflow!r}; expected planned_react or react")
    if max_model_turns is not None and (
        isinstance(max_model_turns, bool) or not isinstance(max_model_turns, int) or max_model_turns < 1
    ):
        raise ValueError("max_model_turns must be null or a positive integer")
    if workflow == "react" and force_rag_grayzone:
        raise ValueError("force_rag_grayzone requires planned_react; react lets the model choose retrieval")


def action_event(
    stage: str, action: str, *, elapsed_seconds: float = 0.0, **details: Any,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "action": action,
        "elapsed_seconds": round(elapsed_seconds, 6),
        **details,
    }
