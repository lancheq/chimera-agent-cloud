"""Atomic Fact-Checking guardrail — decompose → verify → flag.

Implements the Stage D pre-fill guardrail of the Pro four-stage graph.
After self-refine produces a reasoning trace, this module:

1. **Decompose**: splits the free_text / reasoning into atomic claims
   (one clinically testable assertion per sentence).
2. **Verify**: checks each claim against:
   a. The evidence transcript (did the agent actually retrieve this value?)
   b. RAG-retrieved guideline passages (is the threshold correct?)
3. **Flag**: marks each claim as TRUE / FALSE / UNVERIFIED.
4. **Report**: if any FALSE claims are found, signals the caller to
   optionally re-trigger self-refine.

The module combines programmatic checks (fast, deterministic) with
optional RAG-based verification (slower but catches fabricated thresholds).
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from chimera_agent_baseline.agent.trace_ids import trace_case_id as _trace_case_id

log = logging.getLogger(__name__)


@dataclass
class Claim:
    """A single atomic fact claim extracted from free_text."""

    text: str
    category: str  # "threshold" | "value" | "decision" | "other"
    status: str = "UNVERIFIED"  # "TRUE" | "FALSE" | "UNVERIFIED"
    evidence: str = ""  # supporting evidence from transcript
    issue: str = ""  # description of the problem if FALSE


@dataclass
class FactCheckResult:
    """Result of atomic fact checking."""

    claims: list[Claim] = field(default_factory=list)
    n_true: int = 0
    n_false: int = 0
    n_unverified: int = 0
    has_false: bool = False
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return f"{self.n_true} TRUE, {self.n_false} FALSE, {self.n_unverified} UNVERIFIED"


# --- Threshold library: load from resources/thresholds.json (P1-A) ---
# Falls back to a minimal hardcoded set if the JSON file is not found.

_THRESHOLDS_JSON_PATHS = [
    # Relative to this file → src/chimera_agent_baseline/agent/
    Path(__file__).resolve().parent.parent.parent.parent / "resources" / "thresholds.json",
    # Also check common CWD-relative path
    Path(os.getcwd()) / "resources" / "thresholds.json",
    # GC container path
    Path("/opt/ml/model/resources/thresholds.json"),
]


def _load_thresholds() -> list[dict[str, Any]]:
    """Load clinical thresholds from thresholds.json.

    Returns a list of threshold dicts. Each entry has:
    - id, task, metric, threshold, direction, action, description, source, keywords, tolerance

    Falls back to a minimal hardcoded set if the JSON file is not found.
    """
    for path in _THRESHOLDS_JSON_PATHS:
        if path.exists():
            try:
                with open(path) as f:
                    data = json.load(f)
                thresholds = data.get("thresholds", [])
                log.info("Loaded %d thresholds from %s", len(thresholds), path)
                return thresholds
            except Exception as exc:
                log.warning("Failed to load thresholds from %s: %s, using fallback", path, exc)
                break

    log.warning("thresholds.json not found in any search path, using minimal fallback")
    return [
        {"id": "fallback_psad", "task": 1, "metric": "psad", "threshold": 0.15,
         "direction": ">=", "unit": "ng/mL/cc", "action": "consider_biopsy",
         "description": "PSA density biopsy threshold", "source": "EAU fallback",
         "keywords": ["psa density", "psad", "psad 0.15"], "tolerance": 0.02},
        {"id": "fallback_pirads45", "task": 1, "metric": "pirads", "threshold": 4,
         "direction": ">=", "unit": "score", "action": "perform_biopsy",
         "description": "PI-RADS 4-5: biopsy recommended regardless of PSA density",
         "source": "EAU fallback",
         "keywords": ["pi-rads 4", "pirads 4", "pi-rads 5", "pirads 5", "pirads >= 4"], "tolerance": 0},
        {"id": "fallback_isup_gg2_as", "task": 2, "metric": "isup_as_intermediate", "threshold": 2,
         "direction": "<=", "unit": "grade_group", "action": "as_favourable_intermediate",
         "description": "ISUP GG 2 may be eligible for AS in favourable intermediate-risk disease",
         "source": "EAU fallback",
         "keywords": ["isup gg 2", "isup grade group 2", "isup 2", "gleason 3+4", "gg2"], "tolerance": 0},
        {"id": "fallback_isup3_at", "task": 2, "metric": "isup_high_risk", "threshold": 3,
         "direction": ">=", "unit": "grade_group", "action": "active_treatment_recommended",
         "description": "ISUP GG >= 3 indicates unfavourable intermediate or high risk; active treatment recommended",
         "source": "EAU fallback",
         "keywords": ["isup >= 3", "isup gg 3", "isup gg 4", "isup gg 5", "gleason 4+3", "gleason 8"], "tolerance": 0},
        {"id": "fallback_ct3_high_risk", "task": 2, "metric": "clinical_stage_high_risk",
         "threshold": "cT3", "direction": ">=", "unit": "clinical_stage", "action": "high_risk",
         "description": "Clinical stage cT3 or higher defines high-risk disease",
         "source": "EAU fallback",
         "keywords": ["ct3", "ct3a", "ct3b", "ct4", "stage t3"], "tolerance": 0},
        {"id": "fallback_psa20_high_risk", "task": 2, "metric": "psa_high_risk", "threshold": 20.0,
         "direction": ">", "unit": "ng/mL", "action": "high_risk",
         "description": "PSA > 20 ng/mL defines high risk disease regardless of ISUP grade",
         "source": "EAU fallback",
         "keywords": ["psa > 20", "psa 20", "psa high risk"], "tolerance": 1.0},
        {"id": "fallback_bcr_rp", "task": 3, "metric": "bcr_rp", "threshold": 0.2,
         "direction": ">", "unit": "ng/mL", "action": "biochemical_recurrence_rp",
         "description": "Post-RP recurrence threshold", "source": "EAU fallback",
         "keywords": ["bcr", "recurrence", "psa > 0.2", "0.2 ng"], "tolerance": 0.05},
        {"id": "fallback_bcr_rt", "task": 3, "metric": "bcr_rt", "threshold": 2.0,
         "direction": ">", "unit": "ng/mL_above_nadir", "action": "biochemical_recurrence_rt",
         "description": "Phoenix nadir+2 RT recurrence", "source": "EAU fallback",
         "keywords": ["phoenix", "nadir + 2", "nadir+2", "bcr rt"], "tolerance": 0.0},
    ]


# Loaded once at module import
_THRESHOLDS: list[dict[str, Any]] = _load_thresholds()

# Build a keyword → threshold index for fast lookup
_THRESHOLD_INDEX: dict[str, list[dict[str, Any]]] = {}
for _t in _THRESHOLDS:
    for _kw in _t.get("keywords", []):
        _THRESHOLD_INDEX.setdefault(_kw.lower(), []).append(_t)

# Numeric patterns to extract from free_text
_VALUE_PATTERNS = [
    (r"PSA[:\s]+(\d+\.?\d*)\s*ng", "PSA value"),
    (r"PI-?RADS[:\s]*(\d)", "PI-RADS score"),
    (r"ISUP[:\s]*(?:grade\s*group)?[:\s]*(\d)", "ISUP grade"),
    (r"Gleason[:\s]*(\d)\s*\+\s*(\d)", "Gleason score"),
    (r"PSA\s*density[:\s]*(\d+\.?\d*)", "PSA density"),
    (r"PSAD[:\s]*(\d+\.?\d*)", "PSA density"),
]

# Variable → transcript keyword mapping for weight support checking
_VAR_KEYWORDS = {
    "psa": ["psa"],
    "psad": ["psa density", "psad", "psa_density"],
    "pirads": ["pi-rads", "pirads"],
    "bx_isup": ["isup", "grade group", "gleason"],
    "bx_gl_prim": ["gleason"],
    "bx_gl_sec": ["gleason"],
    "age": ["age", "years old", "year-old"],
    "ct": ["ct1", "ct2", "clinical stage", "ct stage"],
    "fh": ["family history", "family hx"],
    "cspca": ["cspca", "clinically significant"],
    "comorbidity": ["comorbid", "med history", "medical history"],
    "vol": ["prostate vol", "volume"],
    "dre": ["dre", "digital rectal"],
}


# System prompt for LLM-assisted atomic claim decomposition
_DECOMPOSE_SYSTEM = (
    "You are a clinical fact decomposition engine. Given a free-text reasoning "
    "paragraph about a prostate cancer case, split it into ATOMIC claims — each "
    "claim must be a single, independently testable assertion.\n\n"
    "Rules:\n"
    "- Each claim should contain exactly ONE clinical fact or assertion.\n"
    "- Split compound sentences: 'PSA 8.5偏高且PSAD 0.12正常' becomes 2 claims.\n"
    "- Include threshold comparisons as separate claims.\n"
    "- Include decision statements as separate claims.\n"
    "- Do NOT add information not present in the input text.\n"
    "- Output ONLY a JSON array of strings, nothing else.\n\n"
    "Example input: 'PSA is 5.2 ng/mL with PSAD 0.151. PI-RADS 3 lesion found. "
    "Biopsy recommended given elevated PSA.'\n"
    'Example output: ["PSA is 5.2 ng/mL", "PSAD is 0.151", '
    '"PI-RADS 3 lesion found", "Biopsy recommended given elevated PSA"]'
)


def _llm_decompose_claims(
    model: BaseChatModel,
    free_text: str,
    case_id: str,
) -> list[str]:
    """Use the LLM to decompose free_text into atomic claims.

    Returns a list of claim strings. On any failure, returns an empty list
    so the caller can fall back to regex-based decomposition.
    """
    user_msg = (
        f"Case ID: {case_id}\n\n"
        f"Free-text reasoning:\n\"\"\"\n{free_text[:3000]}\n\"\"\"\n\n"
        "Split this into atomic claims. Output ONLY a JSON array of strings."
    )
    try:
        resp = model.invoke(
            [
                SystemMessage(content=_DECOMPOSE_SYSTEM),
                HumanMessage(content=user_msg),
            ]
        )
        raw = resp.content if isinstance(resp.content, str) else json.dumps(resp.content)
        # Extract JSON array from response
        raw = raw.strip()
        # Handle markdown fences
        fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.DOTALL)
        if fence:
            raw = fence.group(1)
        else:
            start = raw.find("[")
            if start >= 0:
                end = raw.rfind("]")
                if end > start:
                    raw = raw[start : end + 1]
        claims_list = json.loads(raw)
        if isinstance(claims_list, list):
            return [s.strip() for s in claims_list if isinstance(s, str) and s.strip()]
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM decompose_claims failed for %s: %s", case_id, exc)
    return []


def _categorize_claim(text: str) -> str:
    """Categorize a claim text into threshold/value/decision/other."""
    lower = text.lower()
    if any(kw in lower for kw in ["threshold", "\u2265", "\u2264", "> ", "< ", "exceeds", "below", "above"]):
        return "threshold"
    if any(kw in lower for kw in ["psa", "pirads", "isup", "gleason", "psad"]):
        return "value"
    if any(kw in lower for kw in ["recommend", "decision", "biopsy", "treatment", "surveillance"]):
        return "decision"
    return "other"


def decompose_claims(
    free_text: str,
    model: BaseChatModel | None = None,
    case_id: str = "",
) -> list[Claim]:
    """Split free_text into atomic claims (one clinically testable assertion each).

    When *model* is provided, uses LLM-assisted decomposition for finer
    granularity. Falls back to regex sentence-splitting when no model is
    given or LLM decomposition fails.
    """
    if not free_text:
        return []

    claims: list[Claim] = []

    # --- LLM-assisted decomposition (preferred when model available) ---
    if model is not None:
        llm_claims = _llm_decompose_claims(model, free_text, case_id)
        if llm_claims:
            for claim_text in llm_claims:
                if len(claim_text) < 5:
                    continue
                claims.append(Claim(
                    text=claim_text,
                    category=_categorize_claim(claim_text),
                ))
            if claims:
                log.info("decompose_claims: %s LLM decomposition produced %d claims", case_id, len(claims))
                return claims
            log.info("decompose_claims: %s LLM decomposition returned empty, falling back to regex", case_id)

    # --- Regex fallback: split on sentence boundaries + conjunction splitting ---
    sentences = re.split(r"(?<=[.!?])\s+", free_text.strip())
    for sent in sentences:
        sent = sent.strip()
        if len(sent) < 10:
            continue
        # Further split on conjunctions that join independent assertions
        # e.g., "PSA is 5.2 and PSAD is 0.15" -> 2 claims
        sub_parts = re.split(r"\s+(?:and|but|however|whereas|while|;)\s+", sent, flags=re.IGNORECASE)
        for part in sub_parts:
            part = part.strip()
            if len(part) < 5:
                continue
            claims.append(Claim(
                text=part,
                category=_categorize_claim(part),
            ))

    # --- Specialized regex extraction: 5 key clinical metrics ---
    # Ensures critical values are captured as atomic claims even when
    # sentence splitting or LLM decomposition misses them.
    existing_texts = {c.text.lower() for c in claims}
    _specialized_patterns = [
        (r"PSA[:\s]+(\d+\.?\d*)\s*(?:ng[/ ]?ml)?", "PSA"),
        (r"(?:PSA\s*density|PSAD)[:\s]*(\d+\.?\d*)", "PSAD"),
        (r"PI-?RADS[:\s]*(\d)", "PI-RADS"),
        (r"ISUP[:\s]*(?:grade\s*group)?[:\s]*(\d)", "ISUP"),
        (r"Gleason[:\s]*(\d)\s*\+\s*(\d)", "Gleason"),
    ]
    for pattern, label in _specialized_patterns:
        for m in re.finditer(pattern, free_text, re.IGNORECASE):
            claim_text = m.group(0).strip()
            if len(claim_text) >= 3 and claim_text.lower() not in existing_texts:
                claims.append(Claim(
                    text=claim_text,
                    category=_categorize_claim(claim_text),
                ))
                existing_texts.add(claim_text.lower())

    return claims


def _check_value_in_transcript(value: str, transcript_lower: str) -> bool:
    """Check if a numeric value appears in the transcript."""
    if not value:
        return True
    if value in transcript_lower:
        return True
    # Allow partial match (e.g., "187" matches "187.0")
    short = value.split(".")[0]
    if short and len(short) >= 2 and short in transcript_lower:
        return True
    return False


def _extract_cited_value(text_lower: str, metric: str) -> float | None:
    """Extract a numeric value cited near a threshold keyword in claim text.

    Returns the first numeric value found in patterns like:
    - "PSA > 3.0", "PSA >= 3", "PSA threshold 3"
    - "PSAD 0.15", "PSA density >= 0.15", "PSAD of 0.15"
    - "PI-RADS 4", "PIRADS >= 4"
    - "ISUP GG 2", "ISUP grade group 3", "Gleason 7"
    - "0.2 ng/mL", "> 0.2", ">= 0.2"
    - "nadir + 2", "nadir+2"
    """
    patterns: list[str]

    if metric in ("psa", "psa_high_risk", "psa_high", "as_psa", "srt_trigger",
                   "psa_gray_zone", "bcr_rp", "bcr_rp_metastases",
                   "psa_persistence", "psa_recurrence_rp"):
        patterns = [
            r"(?:psa|psad)\s*(?:[><=≥≤]+\s*|threshold\s*(?:of\s*)?|cutoff\s*(?:of\s*)?|level\s*(?:of\s*)?|value\s*(?:of\s*)?|>\s*|>=\s*|>=\s*)?(\d+\.?\d*)\s*(?:ng[/ ]?ml|ng/ml/cc)?",
            r"(?:threshold|cutoff|level)\s*(?:of\s*)?(\d+\.?\d*)\s*(?:ng[/ ]?ml)?",
            r"(\d+\.?\d*)\s*ng[/ ]?ml",
            r">\s*(\d+\.?\d*)",
            r">=\s*(\d+\.?\d*)",
            r"≥\s*(\d+\.?\d*)",
        ]
    elif metric in ("psad", "as_psad", "psad_gray_zone_low", "psad_gray_zone_high",
                     "pirads3_psad", "psa_density_biopsy"):
        patterns = [
            r"(?:psa\s*density|psad|psa-density)\s*(?:[><=≥≤]+\s*|threshold\s*(?:of\s*)?|of\s*|>=\s*|>\s*|below\s*|above\s*|less\s*than\s*|greater\s*than\s*)?(\d+\.?\d*)",
            r"(?:density)\s*(?:[><=≥≤]+\s*|of\s*|below\s*|above\s*)?(\d+\.?\d*)",
            r"(\d+\.?\d*)\s*ng/ml/cc",
        ]
    elif metric in ("pirads", "pirads_low", "pirads_high"):
        patterns = [
            r"pi-?rads\s*(?:score\s*)?(?:[><=≥≤]+\s*|of\s*|is\s*)?(\d)",
            r"pirads\s*(?:[><=≥≤]+\s*|of\s*|is\s*)?(\d)",
        ]
    elif metric in ("isup_as", "isup_at", "isup_high_risk", "as_trigger_isup",
                     "isup_as_intermediate", "isup_gleason_mapping"):
        patterns = [
            r"isup\s*(?:grade\s*group\s*)?(?:gg\s*)?(?:[><=≥≤]+\s*)?(\d)",
            r"grade\s*group\s*(?:[><=≥≤]+\s*)?(\d)",
            r"gleason\s*(\d+)\s*+\s*(\d+)",
            r"gg\s*(\d)",
        ]
    elif metric in ("bcr_rt", "psa_recurrence_rt"):
        patterns = [
            r"nadir\s*\+\s*(\d+\.?\d*)",
            r"nadir\s*plus\s*(\d+\.?\d*)",
            r"phoenix\s*(?:nadir\s*\+\s*)?(\d+\.?\d*)",
            r"(\d+\.?\d*)\s*ng/ml.*nadir",
        ]
    elif metric in ("bcr_rp", "psa_recurrence_rp", "psa_persistence",
                     "bcr_rp_metastases", "srt_trigger"):
        patterns = [
            r"psa\s*[><=≥≤]+\s*(\d+\.?\d*)",
            r">\s*(\d+\.?\d*)\s*ng",
            r"(\d+\.?\d*)\s*ng/ml",
        ]
    else:
        # Generic numeric extraction
        patterns = [
            r"(?:threshold|cutoff|level|value)\s*(?:of\s*)?(\d+\.?\d*)",
            r">\s*(\d+\.?\d*)",
            r">=\s*(\d+\.?\d*)",
            r"≥\s*(\d+\.?\d*)",
            r"(\d+\.?\d*)\s*ng",
        ]

    for pattern in patterns:
        m = re.search(pattern, text_lower)
        if m:
            try:
                return float(m.group(1))
            except (ValueError, IndexError):
                continue
    return None


def _match_threshold_entry(claim_text_lower: str, entry: dict[str, Any]) -> bool:
    """Check if any keyword from a threshold entry appears in the claim text."""
    for kw in entry.get("keywords", []):
        if kw.lower() in claim_text_lower:
            return True
    return False


def _check_threshold_claims(claim: Claim, transcript_lower: str) -> Claim:
    """Verify threshold claims against the loaded clinical threshold library.

    This function checks if numeric threshold assertions in the claim text
    match known clinical thresholds from the EAU 2026 Guidelines. It uses
    the thresholds loaded from resources/thresholds.json (P1-A enhancement).

    For each threshold entry in the library:
    1. Check if any of its keywords appear in the claim text
    2. If yes, extract the cited numeric value from the claim
    3. Compare against the known threshold (within tolerance)
    4. Mark TRUE (matches), FALSE (significantly different), or UNVERIFIED

    The function also retains backward-compatible checks for PSA density
    and recurrence thresholds to ensure existing TRUE detections continue.
    """
    text_lower = claim.text.lower()

    # If already resolved by a previous check, skip
    if claim.status in ("TRUE", "FALSE"):
        return claim

    # --- Check against loaded threshold library ---
    matched_entries: list[dict[str, Any]] = []
    for entry in _THRESHOLDS:
        if _match_threshold_entry(text_lower, entry):
            matched_entries.append(entry)

    if not matched_entries:
        return claim

    for entry in matched_entries:
        threshold_val = entry.get("threshold")
        tolerance = entry.get("tolerance", 0.0)
        metric = entry.get("metric", "")
        direction = entry.get("direction", ">=")
        source = entry.get("source", "EAU 2026")
        description = entry.get("description", "")

        # Skip non-numeric thresholds (reference tables, criteria strings)
        if not isinstance(threshold_val, (int, float)):
            # For criteria-type thresholds (e.g., "ISUP GG1 + PSA < 10 + cT1-2"),
            # just verify the claim mentions the correct concepts
            continue

        # Extract cited value from the claim text
        cited_val = _extract_cited_value(text_lower, metric)
        if cited_val is None:
            continue

        # Determine if the cited value is consistent with the known threshold
        if direction in (">", ">=", "<", "<="):
            # For ">", ">=", "<", "<=" thresholds, check if cited value is close to threshold
            if abs(cited_val - float(threshold_val)) <= tolerance + 0.001:
                claim.status = "TRUE"
                claim.evidence = f"{description} (threshold {direction} {threshold_val} {entry.get('unit', '')}, {source})"
                return claim
            # Check if cited value is clearly wrong (e.g., PSAD threshold cited as 0.5 instead of 0.15)
            if tolerance > 0 and abs(cited_val - float(threshold_val)) > tolerance * 3:
                claim.status = "FALSE"
                claim.issue = f"Cited {metric} threshold {cited_val} differs significantly from standard {direction} {threshold_val} ({source})"
                return claim
        elif direction == "==":
            if abs(cited_val - float(threshold_val)) <= tolerance + 0.001:
                claim.status = "TRUE"
                claim.evidence = f"{description} (threshold == {threshold_val}, {source})"
                return claim
            if abs(cited_val - float(threshold_val)) > 1:
                claim.status = "FALSE"
                claim.issue = f"Cited {metric} value {cited_val} differs from standard {threshold_val} ({source})"
                return claim
        elif direction == "range":
            # For range thresholds (e.g., PSA gray zone 3-10)
            threshold_low = entry.get("threshold_low")
            threshold_high = entry.get("threshold_high")
            if threshold_low is not None and threshold_high is not None:
                if float(threshold_low) <= cited_val <= float(threshold_high):
                    claim.status = "TRUE"
                    claim.evidence = f"{description} (range {threshold_low}-{threshold_high}, {source})"
                    return claim

    # --- Backward-compatible PSA density check (retained for robustness) ---
    if "psa density" in text_lower or "psad" in text_lower:
        m = re.search(r"(?:psa\s*density|psad)[:\s]*(?:≥|>=|>|exceeds?|above|threshold[:\s]*)(\d+\.?\d*)", text_lower)
        if m:
            cited = float(m.group(1))
            if abs(cited - 0.15) < 0.01:
                if claim.status != "TRUE":
                    claim.status = "TRUE"
                    claim.evidence = "PSAD 0.15 is the correct EAU biopsy threshold"
            elif cited > 0.20 or cited < 0.10:
                if claim.status not in ("TRUE", "FALSE"):
                    claim.status = "FALSE"
                    claim.issue = f"Cited PSAD threshold {cited} differs from standard 0.15"
            else:
                if claim.status == "UNVERIFIED":
                    claim.status = "UNVERIFIED"

    # --- Backward-compatible recurrence check (retained for robustness) ---
    # Only check RP recurrence when context mentions RP, not RT
    has_rp_context = any(kw in text_lower for kw in
                         ["prostatectomy", "rp", "post-rp", "after rp", "post-prostatectomy",
                          "bcr rp", "recurrence rp", "bcr after rp"])
    has_rt_context = any(kw in text_lower for kw in
                         ["phoenix", "nadir", "rt", "radiotherapy", "post-rt", "after rt",
                          "radiation", "bcr rt", "recurrence rt"])
    if has_rp_context and not has_rt_context:
        if "recurrence" in text_lower or "bcr" in text_lower:
            if "0.2" in text_lower:
                if claim.status != "TRUE":
                    claim.status = "TRUE"
                    claim.evidence = "Post-RP PSA > 0.2 ng/mL is correct recurrence threshold (EAU 2026 §6.4.1)"
    elif has_rt_context:
        if "phoenix" in text_lower or "nadir" in text_lower:
            if "2" in text_lower:
                if claim.status != "TRUE":
                    claim.status = "TRUE"
                    claim.evidence = "Phoenix criteria (nadir+2) is correct RT recurrence threshold (EAU 2026 §7.3.5)"

    return claim


def atomic_fact_check(
    task: int,
    case_id: str,
    transcript: str,
    judgment: dict[str, Any],
    eligible: list[str] | None = None,
    model: BaseChatModel | None = None,
) -> FactCheckResult:
    """Run atomic fact verification on the judgment's free_text.

    This is a programmatic check (no LLM call). It verifies:
    1. Numeric values cited in free_text appear in the transcript.
    2. Decisive/important variables have supporting mentions in transcript.
    3. Threshold claims match known clinical thresholds.
    4. Confidence is calibrated to evidence strength.

    Args:
        task: Task number (1/2/3).
        case_id: Case identifier for logging.
        transcript: The agent's reasoning transcript.
        judgment: The parsed judgment dict (may be mutated for confidence fix).
        eligible: List of eligible variable names.

    Returns:
        FactCheckResult with claims and warnings.
    """
    result = FactCheckResult()
    free_text = judgment.get("free_text", "") or ""
    transcript_lower = transcript.lower()
    eligible = eligible or []

    # --- Step 1: Decompose free_text into atomic claims ---
    result.claims = decompose_claims(free_text, model=model, case_id=case_id)
    log.info("fact_check: %s decomposed %d claims from free_text", case_id, len(result.claims))

    # --- Step 2: Verify each claim ---
    for claim in result.claims:
        if claim.category == "threshold":
            claim = _check_threshold_claims(claim, transcript_lower)

        if claim.status == "UNVERIFIED":
            # Check if numeric values in the claim appear in transcript
            all_values_present = True
            for pattern, _label in _VALUE_PATTERNS:
                matches = re.findall(pattern, claim.text, re.IGNORECASE)
                for m in matches:
                    val = m if isinstance(m, str) else m[0]
                    if not _check_value_in_transcript(val, transcript_lower):
                        all_values_present = False
                        claim.status = "FALSE"
                        claim.issue = f"Value '{val}' not found in evidence transcript"
                        break
                if not all_values_present:
                    break

            if claim.status == "UNVERIFIED" and all_values_present:
                # Values are present — mark as TRUE (transcript-supported)
                if claim.category == "value":
                    claim.status = "TRUE"
                    claim.evidence = "Value found in transcript"

    # --- Step 3: Check variable weights have transcript support ---
    weights = judgment.get("variable_weights", {}) or {}
    for var, weight in weights.items():
        if weight in ("decisive", "important"):
            keywords = _VAR_KEYWORDS.get(var, [var])
            found = any(kw in transcript_lower for kw in keywords)
            if not found:
                result.warnings.append(
                    f"atomic: variable '{var}' rated '{weight}' but no mention in transcript"
                )

    # --- Step 4: Confidence calibration ---
    confidence = judgment.get("confidence", "")
    if confidence == "clear":
        should_downgrade = False

        # Downgrade if any FALSE claims
        false_claims = [c for c in result.claims if c.status == "FALSE"]
        if false_claims:
            should_downgrade = True
            result.warnings.append(
                f"atomic: {len(false_claims)} FALSE claims found → confidence should be borderline"
            )

        # Task-specific gray zone checks
        if task == 1:
            psa_match = re.search(r"PSA[:\s]+(\d+\.?\d*)", transcript, re.IGNORECASE)
            pirads_match = re.search(r"PI-?RADS[:\s]*(\d)", transcript, re.IGNORECASE)
            if psa_match and pirads_match:
                psa_val = float(psa_match.group(1))
                pirads_val = int(pirads_match.group(1))
                if 3 <= psa_val <= 10 and pirads_val == 3:
                    should_downgrade = True
                    result.warnings.append("atomic: gray zone (PSA 3-10 + PI-RADS 3) → borderline")

        elif task == 2:
            rec = judgment.get("treatment_recommendation", {}) or {}
            primary = rec.get("primary", "")
            if primary in ("active_surveillance", "continued_surveillance"):
                isup_match = re.search(r"ISUP[:\s]*(?:grade\s*group)?[:\s]*(\d)", transcript, re.IGNORECASE)
                if isup_match:
                    isup_val = int(isup_match.group(1))
                    if isup_val <= 2:
                        pirads_match = re.search(r"PI-?RADS[:\s]*(\d)", transcript, re.IGNORECASE)
                        if pirads_match and int(pirads_match.group(1)) >= 3:
                            should_downgrade = True
                            result.warnings.append(
                                "atomic: surveillance with PI-RADS ≥ 3 → borderline"
                            )

        if should_downgrade:
            judgment["confidence"] = "borderline"
            result.warnings.append("atomic: confidence downgraded clear → borderline (auto-fix)")
            log.info("fact_check: %s confidence downgraded clear → borderline", case_id)

    # --- Tally ---
    result.n_true = sum(1 for c in result.claims if c.status == "TRUE")
    result.n_false = sum(1 for c in result.claims if c.status == "FALSE")
    result.n_unverified = sum(1 for c in result.claims if c.status == "UNVERIFIED")
    result.has_false = result.n_false > 0

    # --- Collect fact-check flags for downstream free_text rewriting ---
    if result.has_false:
        judgment["_fact_check_flags"] = [
            {
                "text": c.text[:300],
                "issue": c.issue,
                "category": c.category,
            }
            for c in result.claims
            if c.status == "FALSE"
        ]

    log.info("fact_check: %s %s", case_id, result.summary())

    # === TRACE DUMP（不影响主流程，失败不报错）===
    try:
        import os as _os, json as _json, time as _time
        _trace_dir = _os.path.join(_os.environ.get("CHIMERA_OUTPUT_DIR", "output"), "trace", _trace_case_id(case_id))
        _os.makedirs(_trace_dir, exist_ok=True)
        with open(_os.path.join(_trace_dir, "fact_check.json"), "w") as f:
            _json.dump({"ts": _time.time(),
                        "n_claims": len(result.claims),
                        "claims": [{"text": c.text[:150], "category": c.category,
                                    "status": c.status, "evidence": c.evidence[:100],
                                    "issue": c.issue[:200]} for c in result.claims],
                        "summary": result.summary(),
                        "warnings": result.warnings,
                        "confidence_final": judgment.get("confidence")}, f, ensure_ascii=False)
    except Exception:
        pass

    return result
