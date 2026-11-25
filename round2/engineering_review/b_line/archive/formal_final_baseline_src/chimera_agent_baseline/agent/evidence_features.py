"""T1 feature candidates from successful, case-matched tool returns only.

No filesystem access, model calls, thresholds for diagnoses, formula-derived
clinical values, or selection using outcome labels. Conflicts remain missing.
"""
from __future__ import annotations

import ast
import calendar
import json
import math
import re
from datetime import date, datetime

from langchain_core.messages import ToolMessage

T1_OBSERVED_FEATURES = frozenset({"psa", "psad", "vol", "pirads_int"})
NUMBER = r"(?:\d+(?:\.\d+)?|\.\d+)"


def decode_payload(content):
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            try:
                content = ast.literal_eval(content)
            except (ValueError, SyntaxError):
                return None
    if isinstance(content, list):
        texts = [block.get("text") for block in content if isinstance(block, dict) and block.get("type") == "text"]
        return decode_payload(texts[0]) if len(texts) == 1 else None
    return content if isinstance(content, dict) else None


def date_interval(value):
    """Month precision is an interval, not an invented first-of-month date."""
    if not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%d-%m-%Y"):
        try:
            day = datetime.strptime(value.strip(), fmt).date()
            return (day, day)
        except ValueError:
            pass
    for fmt in ("%b %Y", "%B %Y", "%Y-%m"):
        try:
            dt = datetime.strptime(value.strip(), fmt)
            return (date(dt.year, dt.month, 1), date(dt.year, dt.month, calendar.monthrange(dt.year, dt.month)[1]))
        except ValueError:
            pass
    return None


def numeric_psa(value, unit=None):
    if isinstance(value, bool):
        return None
    text = str(value).strip()
    match = re.fullmatch(rf"({NUMBER})\s*(ng\s*/\s*ml)?", text, re.I)
    if not match or unit and re.fullmatch(r"ng\s*/\s*ml", str(unit), re.I) is None:
        return None
    number = float(match[1])
    return number if math.isfinite(number) and number >= 0 else None


def assemble_t1_observed_features(messages, case_id):
    candidates = {name: [] for name in T1_OBSERVED_FEATURES}
    rejected = []

    def add(name, value, message, *, field, quote, date_text=None):
        interval = date_interval(date_text)
        candidates[name].append({"value": value, "tool": message.name,
            "tool_call_id": message.tool_call_id, "source_field": field, "quote": quote,
            "date": date_text, "date_interval": [d.isoformat() for d in interval] if interval else None})

    for message in messages:
        if not isinstance(message, ToolMessage) or message.name not in {"get_mri_report", "get_psa_trend", "get_lab_results"}:
            continue
        if getattr(message, "status", "success") == "error":
            rejected.append({"tool": message.name, "reason": "tool_error"})
            continue
        payload = decode_payload(message.content)
        if not payload or payload.get("case_id") != case_id or "error" in payload:
            rejected.append({"tool": message.name, "reason": "invalid_or_mismatched_case_payload"})
            continue
        if message.name == "get_mri_report":
            text = payload.get("radiology_report")
            if not isinstance(text, str):
                continue
            # Prefer the report's explicit conclusion for PI-RADS. Never use a
            # version number or the maximum of disagreeing lesion scores.
            sections = re.split(r"\b(?:IMPRESSION|CONCLUSION|ASSESSMENT)\s*:", text, flags=re.I)
            impression = sections[-1]
            impression = re.sub(r"PI-?RADS\s*(?:v|version\s*)\d+(?:\.\d+)?", "PI-RADS", impression, flags=re.I)
            for match in re.finditer(r"PI-?RADS(?:\s*(?:score|category))?[^\d.]{0,20}([1-5])(?!\d|\.\d)", impression, re.I):
                if re.search(r"[<>≤≥~≈]", match[0]) or re.match(r"\s*(?:-|–|/|to\b|or\b)\s*[1-5]", impression[match.end():], re.I):
                    rejected.append({"tool": message.name, "reason": "non_exact_pirads", "quote": match[0]})
                    continue
                add("pirads_int", float(match[1]), message, field="radiology_report", quote=match[0])
            patterns = {
                "vol": rf"(?:prostat\w*\s+volume|volume\s+of\s+the\s+prostate)[^\d.\-]{{0,24}}({NUMBER})\s*(mL|cc|cm3)\b",
                "psad": rf"(?:PSA\s*density|PSAD|prostate[\s-]+specific\s+antigen\s+density)[^\d.\-]{{0,24}}({NUMBER})(?![\deEa-zA-Z]|\.\d)",
            }
            # Explicit prose equivalents, with an organ reference and units.
            volume_patterns = [patterns["vol"],
                rf"(?:gland|prostate)\s+measures\s+({NUMBER})\s*(mL|cc|cm3)\s+in\s+(?:total\s+)?volume",
                rf"prostat\w*\s+dimensions\s+yield\s+a\s+total\s+volume\s+of\s+({NUMBER})\s*(mL|cc|cm3)\b"]
            for name, pattern in patterns.items():
                for match in re.finditer("|".join(f"(?:{p})" for p in volume_patterns) if name == "vol" else pattern, text, re.I):
                    groups = [g for g in match.groups() if g is not None]
                    # Reject bounds, approximations and numeric ranges rather
                    # than turning them into precise observations.
                    prefix = re.split(NUMBER, match[0], maxsplit=1)[0]
                    suffix = text[match.end():]
                    if re.search(r"[<>≤≥~≈]", prefix) or re.match(r"\s*(?:-|–|to\b)\s*\d", suffix):
                        rejected.append({"tool": message.name, "reason": "non_exact_measurement", "quote": match[0]})
                        continue
                    value = float(groups[0])
                    unit = re.match(r"\s*((?:ng|ug|µg|μg|mg)\s*/\s*[a-zA-Z0-9³²/]+)", text[match.end():], re.I)
                    if name == "psad" and unit and re.sub(r"\s", "", unit[1]).lower() not in {
                        "ng/ml/ml", "ng/ml/cc", "ng/ml/cm3", "ng/ml²"}:
                        rejected.append({"tool": message.name, "reason": "unsupported_psad_unit", "unit": unit[1]})
                        continue
                    if value >= 0 and (name != "vol" or value > 0):
                        add(name, value, message, field="radiology_report", quote=match[0])
        else:
            field = "psa_trend" if message.name == "get_psa_trend" else "laboratory_results"
            entries = payload.get(field)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if field == "laboratory_results" and str(entry.get("name", "")).strip().lower() not in {
                    "psa", "total psa", "prostate specific antigen", "prostate-specific antigen"}:
                    continue
                value = numeric_psa(entry.get("val"), entry.get("unit"))
                if value is not None:
                    add("psa", value, message, field=field, quote=str(entry.get("val")), date_text=entry.get("date"))

    fields, outcomes = {}, {}
    for name, values in sorted(candidates.items()):
        eligible = values
        if name == "psa":
            # A result is superseded only when its entire time interval is
            # earlier than another result. Unknown dates cannot be ordered.
            eligible = [c for c in values if not c["date_interval"] or not any(
                other["date_interval"] and c["date_interval"][1] < other["date_interval"][0]
                for other in values)]
        unique = {c["value"] for c in eligible}
        if len(unique) == 1:
            fields[name] = next(iter(unique))
            status = "observed"
        else:
            status = "conflict" if unique else "not_observed"
        outcomes[name] = {"status": status, "candidates": values,
                          "selected_candidate_ids": [values.index(c) for c in eligible] if len(unique) == 1 else []}
    return {"schema_version": 1, "case_id": case_id, "fields": fields, "evidence": outcomes,
            "rejected_tool_payloads": rejected,
            "unavailable_fields": {"cspca": "not supplied by the existing tools; never read from hidden clinical JSON"}}
