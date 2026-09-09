"""Tool definitions for the CHIMERA-Agent baseline.

Two registries — one per task — are exposed by the MCP server. Each
:class:`~chimera_agent_baseline.tools.base.ToolSpec` lists the fields
from the per-case ``clinical.json`` it returns.

The tools mirror the masked "Extended EHR view" sections of the
urologist forms: the structured "Clinical data" panel is rendered into
the prompt up front, while the documents below were hidden behind a
click and so must be actively requested. Each tool returns its
``clinical.json`` field value directly.

To add a custom tool, append a new ``ToolSpec`` to the appropriate
registry below.
"""

from chimera_agent_baseline.tools.base import ToolSpec

# ---------------------------------------------------------------------------
# Masked "Extended EHR view" documents — one tool per section.
#
# The structured "Clinical data" panel the agent always sees (encounter,
# vitals, PMHx, age, PSA, PI-RADS, PSA density / velocity, prostate
# volume, DRE, prior biopsy, csPCa probability; for task 2 also clinical
# stage and biopsy Gleason / ISUP) is rendered into the prompt up front.
# The tools below cover the documents the urologist had to reveal.
# ---------------------------------------------------------------------------

PSA_TREND = ToolSpec(
    name="get_psa_trend",
    description=(
        "Retrieve the patient's prior PSA measurements as a time series "
        "(date + value). Reveals PSA velocity / trajectory beyond the "
        "single headline value already in the prompt."
    ),
    fields=("psa_trend",),
)

LAB_RESULTS = ToolSpec(
    name="get_lab_results",
    description=(
        "Retrieve the full laboratory panel for the patient (PSA, free PSA, "
        "haematology, renal function, testosterone, alkaline phosphatase, "
        "LDH, etc.) as a list of {name, val, date, flag}."
    ),
    fields=("laboratory_results",),
)

MRI_REPORT = ToolSpec(
    name="get_mri_report",
    description=(
        "Retrieve the radiologist's full mpMRI report as free text. The "
        "headline imaging values (PI-RADS, prostate volume, PSA density, AI "
        "csPCa probability) are already in the prompt; this is the prose "
        "report behind them."
    ),
    fields=("radiology_report",),
)

PATHOLOGY_REPORT = ToolSpec(
    name="get_pathology_report",
    description=(
        "Retrieve the pathologist's full biopsy report as free text. The "
        "headline biopsy values (Gleason patterns, ISUP grade group) are "
        "already in the prompt for biopsied patients; this is the prose "
        "report behind them. Returns a 'no data' note if the patient has "
        "not had a biopsy."
    ),
    fields=("pathology_report",),
)

SURGICAL_PATHOLOGY_REPORT = ToolSpec(
    name="get_surgical_pathology_report",
    description=(
        "Retrieve the surgical (radical prostatectomy) pathology report as "
        "free text: post-operative ISUP grade, margins, extraprostatic "
        "extension, seminal-vesicle / lymph-node involvement, and stage. "
        "Returns a 'no data' note if the patient has not had surgery."
    ),
    fields=("surgical_pathology_report",),
)

PREVIOUS_NOTES = ToolSpec(
    name="get_previous_notes",
    description=(
        "Retrieve previous GP / urology consultation notes for this patient, as a list of {date, author, text}."
    ),
    fields=("previous_notes",),
)

FAMILY_HISTORY = ToolSpec(
    name="get_family_history",
    description=(
        "Ask the patient about their first-degree family history of "
        "prostate cancer (father / brothers). Returns 'Yes', 'No', or "
        "'Unknown'. This is anamnesis elicited during the consultation "
        "rather than information present in the EHR up front, so it must "
        "be actively requested."
    ),
    fields=("family_history",),
)


# Tasks 1 & 2 expose the same masked Extended EHR view, so they share the
# same tool set. (Task 1's clinical.json has no pathology_report, so
# get_pathology_report returns a "no data" note there — mirroring the
# form's masked-but-empty pathology section.)
TASK1_TOOLS: list[ToolSpec] = [
    PSA_TREND,
    LAB_RESULTS,
    MRI_REPORT,
    PATHOLOGY_REPORT,
    PREVIOUS_NOTES,
    FAMILY_HISTORY,
]

TASK2_TOOLS: list[ToolSpec] = [
    PSA_TREND,
    LAB_RESULTS,
    MRI_REPORT,
    PATHOLOGY_REPORT,
    PREVIOUS_NOTES,
    FAMILY_HISTORY,
]


# Task 3 — recurrence prognosis. The post-treatment record is simplified
# (age / PSA / DRE up front); the masked documents are the radiology
# report, the biopsy and surgical pathology reports, previous notes, and
# the family-history anamnesis. No PSA trend / lab panel.
TASK3_TOOLS: list[ToolSpec] = [
    MRI_REPORT,
    PATHOLOGY_REPORT,
    SURGICAL_PATHOLOGY_REPORT,
    PREVIOUS_NOTES,
    FAMILY_HISTORY,
]
