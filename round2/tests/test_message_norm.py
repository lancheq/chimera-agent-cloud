"""归一化正负对照：只修表示格式，不修语义。零真实调用。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from chimera_agent_baseline.message_norm import (  # noqa: E402
    normalize_messages, normalize_payload,
)
from chimera_agent_baseline.runtime_audit import RuntimeFatal  # noqa: E402

SYS = {"role": "system", "content": "s"}
USR = {"role": "user", "content": "u"}


def tool_msg(cid, blocks):
    return {"role": "tool", "tool_call_id": cid, "content": blocks}


def assistant_with_calls(*ids):
    return {"role": "assistant", "content": None,
            "tool_calls": [{"id": i, "type": "function",
                            "function": {"name": "t", "arguments": "{}"}} for i in ids]}


# --- 正向：无损等价转换 -------------------------------------------------
def test_single_text_block_to_string():
    out = normalize_messages([SYS, USR, assistant_with_calls("c1"),
                              tool_msg("c1", [{"type": "text", "text": "R1"}])])
    assert out[3]["content"] == "R1"
    assert out[2]["content"] is None and len(out[2]["tool_calls"]) == 1


def test_multi_blocks_keep_order_and_boundary():
    out = normalize_messages([SYS, USR, assistant_with_calls("c1"),
                              tool_msg("c1", [{"type": "text", "text": "A"},
                                              {"type": "text", "text": "B"}])])
    assert out[3]["content"] == "A\nB"


def test_parallel_tool_calls_preserved():
    out = normalize_messages([
        SYS, USR, assistant_with_calls("c1", "c2"),
        tool_msg("c1", [{"type": "text", "text": "r1"}]),
        tool_msg("c2", [{"type": "text", "text": "r2"}]),
    ])
    ids = [m["tool_call_id"] for m in out if m["role"] == "tool"]
    assert ids == ["c1", "c2"]
    assert [c["id"] for c in out[2]["tool_calls"]] == ["c1", "c2"]


def test_payload_not_mutated():
    payload = {"model": "m", "messages": [SYS, USR, assistant_with_calls("c1"),
                                          tool_msg("c1", [{"type": "text", "text": "R"}])]}
    before = payload["messages"][3]["content"]
    out = normalize_payload(payload)
    assert payload["messages"][3]["content"] is before      # 原对象未被改
    assert out["messages"][3]["content"] == "R"


# --- 负向：语义错误一律拒绝，不代编造 -----------------------------------
def test_tool_without_id_rejected():
    with pytest.raises(RuntimeFatal, match="without tool_call_id"):
        normalize_messages([SYS, USR, assistant_with_calls("c1"),
                            {"role": "tool", "content": "x"}])


def test_orphan_tool_result_rejected():
    with pytest.raises(RuntimeFatal, match="orphan tool result"):
        normalize_messages([SYS, USR, {"role": "assistant", "content": "no calls"},
                            tool_msg("c9", [{"type": "text", "text": "x"}])])


def test_missing_tool_result_rejected():
    with pytest.raises(RuntimeFatal, match="no tool result"):
        normalize_messages([SYS, USR, assistant_with_calls("c1"),
                            {"role": "assistant", "content": "skipped result"}])


def test_tool_call_without_id_rejected():
    with pytest.raises(RuntimeFatal, match="tool_call without id"):
        normalize_messages([SYS, USR,
                            {"role": "assistant", "content": None,
                             "tool_calls": [{"type": "function",
                                             "function": {"name": "t", "arguments": "{}"}}]}])


def test_non_text_block_rejected():
    with pytest.raises(RuntimeFatal, match="Unsupported content block type"):
        normalize_messages([SYS, USR, assistant_with_calls("c1"),
                            tool_msg("c1", [{"type": "image_url",
                                             "image_url": {"url": "x"}}])])


def test_unknown_metadata_rejected_not_dropped():
    with pytest.raises(RuntimeFatal, match="unsupported keys"):
        normalize_messages([{"role": "system", "content": "s", "cache_hint": True}])


def test_null_content_assistant_without_calls_rejected():
    with pytest.raises(RuntimeFatal, match="null without tool_calls"):
        normalize_messages([SYS, {"role": "assistant", "content": None}])


def test_empty_messages_rejected():
    with pytest.raises(RuntimeFatal, match="empty or non-list"):
        normalize_messages([])
