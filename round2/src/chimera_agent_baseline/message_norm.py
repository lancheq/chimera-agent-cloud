"""HTTP 序列化边界的消息归一化。

只处理「表示格式差异」，不做任何语义修补：
- MCP 返回的文本块数组 -> 字符串（多块按原顺序拼接，保留边界标记）
- assistant 的 content=None + tool_calls 保持原样
- 工具调用 id 原样保留

以下情形一律显式报错，绝不代 Agent 编造：
- 工具结果缺少 tool_call_id
- 工具结果找不到对应的 assistant.tool_calls
- 非文本内容块（图片、音频等本链路不支持）
- 未知的消息键（不静默丢弃）

归一化发生在 MeteredEndpoint 调用 submit 之前，因此后续 request_meter 的
协议校验与计量针对的是**归一化后实际要发出的 body**。
"""
from __future__ import annotations

import copy

from chimera_agent_baseline.runtime_audit import RuntimeFatal

_ROLES = {"system", "user", "assistant", "tool"}
_BASE_KEYS = {"role", "content", "name"}
_BLOCK_SEP = "\n"


def _blocks_to_text(blocks, *, role: str) -> str:
    if not isinstance(blocks, list) or not blocks:
        raise RuntimeFatal(f"Unsupported content block list for role {role!r}")
    parts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            raise RuntimeFatal(f"Non-object content block for role {role!r}")
        if block.get("type") != "text" or not isinstance(block.get("text"), str):
            raise RuntimeFatal(
                f"Unsupported content block type {block.get('type')!r} for role {role!r}")
        parts.append(block["text"])
    return _BLOCK_SEP.join(parts)


def _normalize_message(message, index: int) -> dict:
    if not isinstance(message, dict):
        raise RuntimeFatal(f"message[{index}] is not an object")
    role = message.get("role")
    if not isinstance(role, str) or role not in _ROLES:
        raise RuntimeFatal(f"message[{index}] unsupported role {role!r}")

    allowed = set(_BASE_KEYS)
    if role == "assistant":
        allowed.add("tool_calls")
    if role == "tool":
        allowed.add("tool_call_id")
    extra = set(message) - allowed
    if extra:
        raise RuntimeFatal(f"message[{index}] unsupported keys {sorted(extra)}")

    out = dict(message)
    content = out.get("content")

    if isinstance(content, list):
        out["content"] = _blocks_to_text(content, role=role)
    elif content is None:
        if not (role == "assistant" and out.get("tool_calls")):
            raise RuntimeFatal(f"message[{index}] content is null without tool_calls")
    elif not isinstance(content, str):
        raise RuntimeFatal(f"message[{index}] unsupported content type {type(content).__name__}")

    if role == "tool":
        cid = out.get("tool_call_id")
        if not isinstance(cid, str) or not cid:
            raise RuntimeFatal(f"message[{index}] tool result without tool_call_id")

    if role == "assistant" and out.get("tool_calls") is not None:
        calls = out["tool_calls"]
        if not isinstance(calls, list) or not calls:
            raise RuntimeFatal(f"message[{index}] malformed tool_calls")
        for call in calls:
            if not isinstance(call, dict) or not isinstance(call.get("id"), str) or not call["id"]:
                raise RuntimeFatal(f"message[{index}] tool_call without id")
    return out


def normalize_messages(messages) -> list[dict]:
    if not isinstance(messages, list) or not messages:
        raise RuntimeFatal("normalize: empty or non-list messages")
    out = [_normalize_message(m, i) for i, m in enumerate(messages)]

    # 顺序一致性：tool 结果必须紧跟其声明所在轮次，且不得孤立存在
    pending: set[str] = set()
    for i, m in enumerate(out):
        if pending and m["role"] != "tool":
            raise RuntimeFatal(
                f"normalize: assistant tool_calls at {i} have no tool result")
        if m["role"] == "assistant":
            pending = {c["id"] for c in (m.get("tool_calls") or [])}
        elif m["role"] == "tool":
            cid = m["tool_call_id"]
            if cid not in pending:
                raise RuntimeFatal(f"normalize: orphan tool result {cid!r}")
            pending.discard(cid)
    return out


def normalize_payload(payload: dict) -> dict:
    """返回归一化后的新 payload；不修改入参。"""
    if not isinstance(payload, dict):
        raise RuntimeFatal("normalize: payload is not an object")
    if "messages" not in payload:
        return payload
    out = copy.deepcopy(payload)
    out["messages"] = normalize_messages(payload["messages"])
    return out
