"""Guard against renamed heldout copies, cross-task leakage and label peeking."""

import json

from tools.audit_cohorts import audit


def make_case(tmp_path, source, cid, *, task=1, marker="same", alias=False):
    case = tmp_path / source / f"task{task}" / "agent_input" / cid
    case.mkdir(parents=True)
    prompt = {"case_id": cid, "task": task, "psa": 5}
    (case / "structured-prompt.json").write_text(json.dumps(prompt))
    if alias:
        (case / "prompt.json").write_text(json.dumps({**prompt, "psa": 10}))
    slug = "prostate-biopsy-decision" if task == 1 else "prostate-treatment-decision"
    (case / f"{slug}-clinical-data.json").write_text(json.dumps({"case_id": cid, "radiology_report": marker}))
    # Deliberately invalid label JSON proves the audit never opens labels.
    (case / f"{slug}.json").write_text("DO NOT READ HOLDOUT LABELS")
    return case


def sources(*roles):
    return [{"name": name, "root": name, "role": role} for name, role in roles]


def test_renamed_copy_across_train_holdout_is_flagged(tmp_path):
    make_case(tmp_path, "train", "A")
    make_case(tmp_path, "test", "renamed")
    report = audit(sources(("train", "train"), ("test", "holdout")), tmp_path)
    assert report["summary"]["decision_files_present"] == 2
    assert report["errors"] == []
    assert report["summary"]["potential_cross_partition_leakage"] == 1
    assert report["overlap_candidates"][0]["basis"] == "clinical"


def test_prompt_equality_alone_does_not_assert_duplicate_patient(tmp_path):
    make_case(tmp_path, "train", "A", marker="report A")
    make_case(tmp_path, "test", "B", marker="report B")
    report = audit(sources(("train", "train"), ("test", "holdout")), tmp_path)
    assert report["overlap_candidates"] == []


def test_shared_embedding_detects_cross_task_contact(tmp_path):
    a = make_case(tmp_path, "train", "A", marker="A")
    b = make_case(tmp_path, "test", "B", task=2, marker="B")
    for case in (a, b):
        (case / "prostate-modality-level-neural-representations.json").write_text('{"MRI image": [[1, 2]]}')
    report = audit(sources(("train", "train"), ("test", "holdout")), tmp_path)
    assert report["summary"]["potential_cross_partition_leakage"] == 1
    assert report["overlap_candidates"][0]["basis"] == "embeddings"


def test_anonymized_name_does_not_assign_label_provenance(tmp_path):
    make_case(tmp_path, "train", "PT-pseudo_example")
    report = audit(sources(("train", "legacy_exposed")), tmp_path)
    assert report["cases"][0]["label_provenance"] == "unknown"
    assert report["cases"][0]["role"] == "legacy_exposed"


def test_conflicting_runtime_prompt_aliases_are_reported(tmp_path):
    make_case(tmp_path, "train", "A", alias=True)
    report = audit(sources(("train", "legacy_exposed")), tmp_path)
    assert any(e["error"] == "prompt_alias_conflict" for e in report["errors"])


def test_missing_roots_fail_instead_of_reporting_an_empty_clean_cohort(tmp_path):
    report = audit(sources(("missing", "holdout")), tmp_path)
    assert report["errors"][0]["error"] == "missing_root"
