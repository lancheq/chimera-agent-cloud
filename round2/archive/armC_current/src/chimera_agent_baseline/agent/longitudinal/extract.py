"""Recover PSA observations from the four clinical sections that carry them.

Only sections the agent has actually *revealed* are passed in, so this
module cannot create evidence the agent did not pay for. Four extractors,
in descending order of reliability:

  psa_trend          structured rows  -> MEASURED, weight 0.8 (month precision)
  laboratory_results structured rows  -> MEASURED, weight 1.0 (day precision)
  radiology_report   prose mentions   -> QUOTED,   weight 0.5
  previous_notes     prose mentions   -> QUOTED,   weight 0.5

Prose extraction is deliberately conservative: it never spans sentences,
and it drops any number followed by a density unit (/mL, /cc).
"""

from __future__ import annotations

import datetime as dt
import re

from .schema import MEASURED, QUOTED, Observation, to_years

MONTHS = {m: i for i, m in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1)}

_D_MON_Y = re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})\b")
_MON_Y = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{4})\b")
_ISO_M = re.compile(r"\b(\d{4})-(\d{2})\b")
_ISO_D = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

# A PSA number only counts when the unit is ng/mL and is NOT followed by a
# density denominator.  ">60" style inequalities are rejected outright.
_PSA_NUM = re.compile(r"(?P<op>[<>=]{0,2})\s*(?P<v>\d+(?:[.,]\d+)?)\s*ng/mL(?!\s*[/])")


def parse_date(text: str) -> tuple[dt.date, str] | None:
    """Return (representative date, precision). Month precision -> day 15."""
    if not text:
        return None
    m = _ISO_D.search(text)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        try:
            return dt.date(y, mo, d), "day"
        except ValueError:
            return None
    m = _D_MON_Y.search(text)
    if m:
        d, mon, y = m.group(1), m.group(2), m.group(3)
        mi = MONTHS.get(mon[:3].lower())
        if mi:
            try:
                return dt.date(int(y), mi, int(d)), "day"
            except ValueError:
                return None
    m = _MON_Y.search(text)
    if m:
        mon, y = m.group(1), m.group(2)
        mi = MONTHS.get(mon[:3].lower())
        if mi:
            try:
                return dt.date(int(y), mi, 15), "month"
            except ValueError:
                return None
    m = _ISO_M.search(text)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return dt.date(y, mo, 15), "month"
    return None


_WEIGHT = {"day": 1.0, "month": 0.8, "year": 0.5}


def _obs(value, date, precision, section, locator, nature, date_raw, notes=""):
    return Observation(
        metric="PSA", value=float(value), unit="ng/mL", date_raw=date_raw,
        date=date.isoformat(), precision=precision, t=to_years(date),
        section=section, locator=locator, nature=nature,
        weight=_WEIGHT[precision], notes=notes)


# --- structured sections --------------------------------------------------

def from_psa_trend(rows) -> list[Observation]:
    out = []
    for i, row in enumerate(rows or []):
        if not isinstance(row, dict):
            continue
        val = row.get("val", row.get("value"))
        raw_date = row.get("date", "")
        parsed = parse_date(str(raw_date))
        if val is None or parsed is None:
            continue
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        d, prec = parsed
        out.append(_obs(v, d, prec, "psa_trend", f"psa_trend[{i}]", MEASURED,
                        str(raw_date)))
    return out


def from_laboratory_results(rows) -> list[Observation]:
    out = []
    for i, row in enumerate(rows or []):
        if not isinstance(row, dict) or str(row.get("name", "")).strip().lower() != "psa":
            continue  # excludes "Free PSA" and "%Free PSA"
        parsed = parse_date(str(row.get("date", "")))
        m = _PSA_NUM.search(str(row.get("val", "")))
        if parsed is None or m is None or m.group("op"):
            continue
        d, prec = parsed
        out.append(_obs(float(m.group("v").replace(",", ".")), d, prec,
                        "laboratory_results",
                        f"laboratory_results[{i}]({row.get('date')})",
                        MEASURED, str(row.get("date"))))
    return out


# --- prose sections -------------------------------------------------------

_SENT_SPLIT = re.compile(r"(?<=[.;])\s+")
_NEG = re.compile(r"\b(free\s+psa|%\s*free|psa\s+density|psad)\b", re.I)


def _sentences(text: str):
    pos = 0
    for chunk in _SENT_SPLIT.split(text):
        yield pos, chunk
        pos += len(chunk) + 1


def from_previous_notes(notes) -> list[Observation]:
    """Mine PSA values from GP / urology notes.

    A sentence qualifies only if it names PSA, carries an ng/mL value, and
    does not also mention free PSA or PSA density.  Date priority:
    nearest date earlier in the same note -> the note's own date field.
    """
    out = []
    for i, note in enumerate(notes or []):
        if not isinstance(note, dict):
            continue
        text = str(note.get("text", ""))
        note_date = parse_date(str(note.get("date", "")))
        last_seen_date = note_date
        for off, sent in _sentences(text):
            dm = re.search(r"\b\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}\b|\b[A-Za-z]{3,9}\s+\d{4}\b", sent)
            sent_date = parse_date(dm.group(0)) if dm else None
            if sent_date:
                last_seen_date = sent_date
            if re.search(r"\bpsa\b", sent, re.I) and not _NEG.search(sent):
                for m in _PSA_NUM.finditer(sent):
                    if m.group("op"):
                        continue
                    anchor = sent_date or last_seen_date
                    if anchor is None:
                        continue
                    d, prec = anchor
                    span = f"previous_notes[{i}].text:{off + m.start()}-{off + m.end()}"
                    out.append(_obs(float(m.group("v").replace(",", ".")), d, "day",
                                    "previous_notes", span, QUOTED,
                                    dm.group(0) if sent_date else str(note.get("date")),
                                    notes=sent.strip()[:120]))
    return out


_RAD_REF_IN = re.compile(r"(\d+(?:[.,]\d+)?)\s*ng/mL\s+in\s+([A-Za-z]{3,9}\.?\s+\d{4})")
_RAD_BARE = re.compile(r"(?:now|currently|current)\s+(\d+(?:[.,]\d+)?)\s*ng/mL", re.I)
_RAD_PLAIN = re.compile(r"PSA(?:\s+(?:of|at|was|=))?\s*(\d+(?:[.,]\d+)?)\s*ng/mL(?!\s*[/])", re.I)


def from_radiology(report: str) -> list[Observation]:
    """Mine the MRI report. Narrative values are QUOTED, never fitted direct."""
    if not report:
        return []
    out = []
    report_date = parse_date(report)
    dm = re.search(r"(?:Date|Subject)\s*:[^\n|]*?(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})", report)
    if dm:
        report_date = parse_date(dm.group(1))

    for m in _RAD_REF_IN.finditer(report):
        parsed = parse_date(m.group(2))
        if not parsed:
            continue
        d, prec = parsed
        out.append(_obs(float(m.group(1).replace(",", ".")), d, prec,
                        "radiology_report", f"radiology_report:{m.start()}-{m.end()}",
                        QUOTED, m.group(2), notes="value cited inside imaging report"))

    if report_date:
        d, _ = report_date
        for rx, why in ((_RAD_BARE, "cited as current at time of imaging"),
                        (_RAD_PLAIN, "cited in imaging indication")):
            for m in rx.finditer(report):
                tail = report[m.end():m.end() + 8]
                if tail.lstrip().startswith("/"):   # PSA density
                    continue
                out.append(_obs(float(m.group(1).replace(",", ".")), d, "day",
                                "radiology_report", f"radiology_report:{m.start()}-{m.end()}",
                                QUOTED, d.isoformat(), notes=why))
    return out


EXTRACTORS = {
    "psa_trend": from_psa_trend,
    "laboratory_results": from_laboratory_results,
    "previous_notes": from_previous_notes,
    "radiology_report": from_radiology,
}
