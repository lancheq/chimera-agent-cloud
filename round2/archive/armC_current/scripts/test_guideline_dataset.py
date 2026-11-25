"""Test the medical handbook dataset for usability in RAG / atomic fact queries.

Validates:
1. JSONL parseability and schema completeness
2. QA grounding references exist in the unified dataset
3. Keyword search (simulating RAG retrieval)
4. Data quality checks (control chars, empty fields, text length)
5. Task-tag coverage for T1/T2/T3
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

DATASET_DIR = Path(__file__).resolve().parent.parent / "data_backup" / "guideline_dataset"
UNIFIED_PATH = DATASET_DIR / "normalized" / "guidelines_unified.jsonl"
EAU_NORM_PATH = DATASET_DIR / "normalized" / "eau_normalized.jsonl"
QA_PATH = DATASET_DIR / "qa" / "guideline_qa.jsonl"

REQUIRED_FIELDS = {"id", "source", "version", "year", "section_id", "section_title", "page", "text", "task_tags"}
QA_REQUIRED_FIELDS = {"id", "task", "question", "answer", "decision", "confidence", "grounding", "evidence_level"}

# Control characters that shouldn't appear in clean text
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

passed = 0
failed = 0
warnings = 0


def ok(msg):
    global passed
    passed += 1
    print(f"  [PASS] {msg}")


def fail(msg):
    global failed
    failed += 1
    print(f"  [FAIL] {msg}")


def warn(msg):
    global warnings
    warnings += 1
    print(f"  [WARN] {msg}")


def load_jsonl(path):
    records = []
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                fail(f"{path.name} line {i}: JSON parse error: {e}")
    return records


def test_parseability_and_schema(records, name, required_fields):
    """Test 1: All records parse and have required fields."""
    print(f"\n{'='*60}")
    print(f"Test: {name} — parseability & schema ({len(records)} records)")
    print(f"{'='*60}")

    if not records:
        fail(f"{name}: no records loaded")
        return

    missing_fields = Counter()
    for r in records:
        for field in required_fields:
            if field not in r:
                missing_fields[field] += 1

    if missing_fields:
        for field, count in missing_fields.items():
            fail(f"{name}: missing field '{field}' in {count} records")
    else:
        ok(f"All {len(records)} records have required fields: {sorted(required_fields)}")

    # Check ID uniqueness
    ids = [r["id"] for r in records]
    dupes = [id_ for id_, count in Counter(ids).items() if count > 1]
    if dupes:
        fail(f"{name}: {len(dupes)} duplicate IDs: {dupes[:5]}")
    else:
        ok(f"All {len(ids)} IDs are unique")


def test_data_quality(records, name):
    """Test 2: Data quality — control chars, empty text, short text."""
    print(f"\n{'='*60}")
    print(f"Test: {name} — data quality")
    print(f"{'='*60}")

    ctrl_count = 0
    empty_text = 0
    short_text = 0
    empty_section = 0

    for r in records:
        text = r.get("text", "")
        if not text.strip():
            empty_text += 1
        elif len(text.strip()) < 100:
            short_text += 1

        if _CTRL_RE.search(text):
            ctrl_count += 1

        if not r.get("section_id", "").strip():
            empty_section += 1

    if empty_text:
        fail(f"{name}: {empty_text} records with empty text")
    else:
        ok(f"No empty text records")

    if short_text:
        warn(f"{name}: {short_text} records with text < 100 chars (may be TOC residuals)")
    else:
        ok(f"All records have text >= 100 chars")

    if ctrl_count:
        warn(f"{name}: {ctrl_count} records contain control characters (e.g. \\u0007 BEL)")
    else:
        ok(f"No control characters found in text")

    if empty_section:
        warn(f"{name}: {empty_section} records with empty section_id")
    else:
        ok(f"All records have section_id")

    # Text length stats
    lengths = [len(r.get("text", "")) for r in records]
    avg_len = sum(lengths) / len(lengths) if lengths else 0
    print(f"  Text length: min={min(lengths)}, max={max(lengths)}, avg={avg_len:.0f}")


def test_task_coverage(records):
    """Test 3: Task tag distribution covers T1/T2/T3."""
    print(f"\n{'='*60}")
    print(f"Test: Task tag coverage")
    print(f"{'='*60}")

    tag_counts = Counter()
    for r in records:
        for t in r.get("task_tags", []):
            tag_counts[t] += 1

    for task in ["T1", "T2", "T3", "GENERAL"]:
        if tag_counts.get(task, 0) > 0:
            ok(f"{task}: {tag_counts[task]} records")
        else:
            fail(f"{task}: 0 records — missing coverage!")

    print(f"  Full distribution: {dict(tag_counts)}")


def test_qa_grounding(qa_records, unified_records):
    """Test 4: QA grounding references exist in the unified dataset."""
    print(f"\n{'='*60}")
    print(f"Test: QA grounding validation ({len(qa_records)} QA pairs)")
    print(f"{'='*60}")

    # Build section_id index
    section_ids = {r["section_id"] for r in unified_records if r.get("section_id")}

    total_grounding = 0
    valid_grounding = 0
    invalid_refs = []

    for qa in qa_records:
        for g in qa.get("grounding", []):
            total_grounding += 1
            sid = g.get("section_id", "")
            if sid in section_ids:
                valid_grounding += 1
            else:
                invalid_refs.append(f"{qa['id']} -> section_id '{sid}'")

    if total_grounding == 0:
        fail("No grounding references found in QA pairs")
    else:
        hit_rate = valid_grounding / total_grounding * 100
        if hit_rate == 100:
            ok(f"All {total_grounding} grounding references valid (100% hit rate)")
        elif hit_rate >= 80:
            warn(f"{valid_grounding}/{total_grounding} grounding references valid ({hit_rate:.0f}%)")
            for ref in invalid_refs[:5]:
                print(f"    Invalid: {ref}")
        else:
            fail(f"Only {valid_grounding}/{total_grounding} grounding references valid ({hit_rate:.0f}%)")
            for ref in invalid_refs[:5]:
                print(f"    Invalid: {ref}")

    # Check QA task distribution
    qa_tasks = Counter(qa["task"] for qa in qa_records)
    print(f"  QA task distribution: {dict(qa_tasks)}")
    for task in ["T1", "T2", "T3"]:
        if qa_tasks.get(task, 0) > 0:
            ok(f"QA {task}: {qa_tasks[task]} pairs")
        else:
            fail(f"QA {task}: 0 pairs — missing!")


def test_keyword_search(unified_records):
    """Test 5: Simulate RAG retrieval with keyword search for key clinical scenarios."""
    print(f"\n{'='*60}")
    print(f"Test: Keyword search (simulating RAG retrieval)")
    print(f"{'='*60}")

    test_queries = [
        {
            "label": "T1: PI-RADS + PSA density biopsy decision",
            "keywords": ["PI-RADS", "PSA density", "biopsy"],
            "expect_section": "5.4.2.2",
        },
        {
            "label": "T1: Biopsy recommendations",
            "keywords": ["biopsy", "recommendation"],
            "expect_section": "5.6.4",
        },
        {
            "label": "T2: Low-risk management / active surveillance",
            "keywords": ["low-risk", "active surveillance"],
            "expect_section": "6.3.1",
        },
        {
            "label": "T3: BCR definition / biochemical recurrence",
            "keywords": ["biochemical", "recurrence", "BCR"],
            "expect_section": "6.4.1",
        },
        {
            "label": "T3: Salvage radiotherapy",
            "keywords": ["salvage", "radiotherapy"],
            "expect_section": "6.4.5",
        },
    ]

    for q in test_queries:
        scored = []
        for r in unified_records:
            text_lower = r.get("text", "").lower()
            section_lower = r.get("section_title", "").lower()
            combined = text_lower + " " + section_lower
            score = sum(1 for kw in q["keywords"] if kw.lower() in combined)
            if score > 0:
                scored.append((score, r))

        scored.sort(key=lambda x: -x[0])
        top = scored[:3] if scored else []

        if not top:
            fail(f"{q['label']}: no results found")
            continue

        top_sections = [r[1].get("section_id", "") for r in top]
        # Check if expected section is in top results
        found = any(q["expect_section"] in s for s in top_sections)

        if found:
            ok(f"{q['label']}: found section {q['expect_section']} in top-3 results")
        else:
            warn(f"{q['label']}: expected section {q['expect_section']} not in top-3 (got: {top_sections})")

        print(f"    Top results: {[(s[1]['section_id'], s[1]['section_title'][:50]) for s in top]}")


def test_chromadb_compat(unified_records):
    """Test 6: Verify data can be loaded into ChromaDB format."""
    print(f"\n{'='*60}")
    print(f"Test: ChromaDB compatibility check")
    print(f"{'='*60}")

    # Simulate what process_guidelines.py / rag.py expects
    chroma_format = []
    for r in unified_records:
        chroma_format.append({
            "id": r["id"],
            "text": r["text"],
            "metadata": {
                "page": r.get("page"),
                "section": f"{r.get('section_id', '')} {r.get('section_title', '')}".strip(),
                "source": r.get("source", ""),
                "task_tags": ",".join(r.get("task_tags", [])),
            },
        })

    ok(f"All {len(chroma_format)} records convertible to ChromaDB format")
    print(f"  Sample ChromaDB entry:")
    sample = chroma_format[1] if len(chroma_format) > 1 else chroma_format[0]
    print(f"    id: {sample['id']}")
    print(f"    text: {sample['text'][:100]}...")
    print(f"    metadata: {sample['metadata']}")


def main():
    print("=" * 60)
    print("Medical Handbook Dataset Usability Test")
    print(f"Dataset: {DATASET_DIR}")
    print("=" * 60)

    # Load data
    unified = load_jsonl(UNIFIED_PATH)
    eau_norm = load_jsonl(EAU_NORM_PATH)
    qa = load_jsonl(QA_PATH)

    print(f"\nLoaded: unified={len(unified)}, eau_normalized={len(eau_norm)}, qa={len(qa)}")

    # Run tests
    test_parseability_and_schema(unified, "guidelines_unified", REQUIRED_FIELDS)
    test_parseability_and_schema(qa, "guideline_qa", QA_REQUIRED_FIELDS)
    test_data_quality(unified, "guidelines_unified")
    test_task_coverage(unified)
    test_qa_grounding(qa, unified)
    test_keyword_search(unified)
    test_chromadb_compat(unified)

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY: {passed} passed, {failed} failed, {warnings} warnings")
    print(f"{'='*60}")

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
