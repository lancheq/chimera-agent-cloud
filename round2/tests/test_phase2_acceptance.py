import json

from tools.phase2_acceptance import CLINICAL, prepare
from tools.review_phase2_acceptance import decode_tool_payload


def test_acceptance_selection_and_socket_staging_never_read_labels(tmp_path):
    release = tmp_path / "release"
    specs = [(1, "low", 2, None), (1, "mid", 3, None), (1, "high", 5, None),
             (2, "grade_low", 2, 1), (2, "grade_high", 5, 4), (3, "surgery", 5, None)]
    for task, cid, score, grade in specs:
        case = release / f"task{task}" / cid
        case.mkdir(parents=True)
        (case / "structured-prompt.json").write_text(json.dumps({"case_id": cid, "age": 65}))
        clinical = {"radiology_report": f"PI-RADS {score}. Prostate volume 30 mL.",
                    "AI_predicted_isup_grade_group": grade}
        (case / f"{CLINICAL[task]}-clinical-data.json").write_text(json.dumps(clinical))
        (case / f"{CLINICAL[task]}.json").write_text("INVALID LABEL MUST NOT BE READ")
    target = tmp_path / "run"
    protocol = prepare(release, target)
    assert len(protocol["cases"]) == 6
    assert protocol["selection_uses_labels"] is False
    for case in protocol["cases"]:
        socket_root = target / "cases" / case["name"] / "sockets/input"
        assert not (socket_root / f"{CLINICAL[case['task']]}.json").exists()
        staged_prompt = json.loads((socket_root / "structured-prompt.json").read_text())
        assert "pirads" not in staged_prompt  # no hidden report field injected
        assert "task" not in staged_prompt  # the real adapter supplies it


def test_decode_adapter_blocks_preserves_exact_source_text():
    payload = {"case_id": "gc-case", "radiology_report": "source — PI-RADS 3"}
    block = [{"type": "text", "text": json.dumps(payload)}]
    assert decode_tool_payload(str(block)) == payload
    assert decode_tool_payload(json.dumps(block)) == payload
    assert decode_tool_payload(json.dumps(payload)) == payload
    assert decode_tool_payload("[{'type': 'text', 'text': 'truncated") is None
