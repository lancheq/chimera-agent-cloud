"""Data model for longitudinal PSA evidence.

Design constraint: every number that reaches the language model must be
traceable to (section, locator) and must carry an explicit epistemic tag.
The model is allowed to see *that* a value is a raw measurement, a
second-hand quote, or a fitted estimate -- it is never allowed to see a
fitted number that looks like a measurement.
"""

from dataclasses import dataclass, field, asdict
import datetime as dt
from typing import Any


# --- epistemic vocabulary -------------------------------------------------
MEASURED = "measured"      # value printed in a structured field (trend/lab row)
QUOTED = "quoted"         # value embedded in prose (note / radiology narrative)
DERIVED = "derived"       # computed by this module (fitted, not observed)

# --- identity of two observations in the same time window ------------------
SAME_LIKELY = "SAME_OBSERVATION_LIKELY"   # same value, same month -> one measurement
DISTINCT = "DISTINCT"                     # incompatible values -> conflict
UNKNOWN = "UNKNOWN"                       # cannot tell

# --- fit eligibility ------------------------------------------------------
FIT_OK = "FIT"
FIT_HOLD = "HOLD"          # usable for display only, not for fitting
FIT_OUT = "EXCLUDED"       # filtered out (post-cutoff, non-PSA, unparsable)


@dataclass(frozen=True)
class Observation:
    """A single point of PSA evidence recovered from one section."""

    metric: str
    value: float
    unit: str
    date_raw: str
    date: str                 # ISO normalised representative date
    precision: str            # "day" | "month" | "year"
    t: float                  # decimal years since 2000-01-01 (fit axis)
    section: str              # clinical.json field the value came from
    locator: str              # JSON path / character span inside that section
    nature: str               # MEASURED | QUOTED | DERIVED
    weight: float             # fit weight (day=1.0, month=0.8, year=0.5)
    identity: str = UNKNOWN   # filled by reconcile
    fit: str = FIT_OK         # filled by reconcile / isolation
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Timeline:
    """All PSA evidence for one case under one access scope."""

    case_id: str
    task: int
    as_of: str                          # decision time, ISO date
    revealed_sections: list[str]
    observations: list[Observation] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    dedup: list[dict] = field(default_factory=list)
    estimate: dict | None = None
    warnings: list[str] = field(default_factory=list)

    def sorted_obs(self) -> list[Observation]:
        return sorted(self.observations, key=lambda o: (o.t, o.value))

    def to_dict(self) -> dict[str, Any]:
        d = {
            "case_id": self.case_id,
            "task": self.task,
            "as_of": self.as_of,
            "revealed_sections": self.revealed_sections,
            "observations": [o.to_dict() for o in self.sorted_obs()],
            "excluded": self.excluded,
            "conflicts": self.conflicts,
            "dedup": self.dedup,
            "estimate": self.estimate,
            "warnings": self.warnings,
        }
        return d


EPOCH = dt.date(2000, 1, 1)


def to_years(d: dt.date) -> float:
    return (d - EPOCH).days / 365.25


def from_years(t: float) -> dt.date:
    return EPOCH + dt.timedelta(days=round(t * 365.25))
