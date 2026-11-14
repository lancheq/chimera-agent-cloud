"""Experimental T1 revision proposals; never overwrite a diagnosis in place.

These statistical bounds invite evidence review. They are not clinical
confidence guarantees, nor permission to reuse the old rationale after a flip.
No production graph uses this module by default.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class DecisionProposal:
    original: str
    proposed: str
    action: str
    p_yes: float
    lower: float
    upper: float
    reason: str


def propose_t1_revision(original: str, p_yes: float, lower: float, upper: float) -> DecisionProposal:
    if original not in {"yes", "no"}:
        raise ValueError("T1 original judgment must be yes or no")
    values = (p_yes, lower, upper)
    if any(isinstance(v, bool) or not math.isfinite(float(v)) or not 0 <= float(v) <= 1 for v in values):
        raise ValueError("Gate probabilities must be finite values in [0, 1]")
    if lower > upper:
        raise ValueError("Gate interval is reversed")
    proposed = "no" if original == "yes" and upper < .5 else "yes" if original == "no" and lower > .5 else original
    changed = proposed != original
    return DecisionProposal(original, proposed, "review_required" if changed else "keep",
                            float(p_yes), float(lower), float(upper),
                            "interval_supports_opposite_label" if changed else "retain_original")


def stage_t1_proposal(judgment: dict, proposal: DecisionProposal) -> dict:
    """Keep the complete original until a whole revised judgment is reviewed.

    In particular, never combine a new decision with the old free_text or plan.
    An actual review/acceptance integration is intentionally not implemented here.
    """
    if judgment.get("biopsy_decision") != proposal.original:
        raise ValueError("Proposal was made against a different original judgment")
    return {"judgment": deepcopy(judgment), "decision_review": asdict(proposal),
            "revision_applied": False}
