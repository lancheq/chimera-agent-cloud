"""OpenAI-compatible chat model with reasoning-content propagation.

Some OpenAI-compatible servers (notably ``llama-server`` from llama.cpp,
serving Qwen3.x and similar models) emit chain-of-thought into a separate
``reasoning_content`` field on the assistant message:

.. code-block:: json

    {
      "role": "assistant",
      "content": "2 + 2 = 4",
      "reasoning_content": "Step 1: ... Step 2: ..."
    }

Vanilla :class:`langchain_openai.ChatOpenAI` discards that field — only
``content`` makes it into :class:`~langchain_core.messages.AIMessage`. For
the agent we want to keep the trace so participants can inspect what the
model deliberated about. This subclass copies ``reasoning_content`` into
``AIMessage.additional_kwargs["reasoning_content"]`` after the parent
class builds the result. The ReAct loop is unaffected — it routes only
on ``tool_calls``.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI


class ChatOpenAICompat(ChatOpenAI):
    """:class:`ChatOpenAI` that preserves ``reasoning_content`` in messages."""

    def _create_chat_result(self, response: Any, generation_info: dict | None = None):
        result = super()._create_chat_result(response, generation_info)

        # ``response`` may be an openai SDK BaseModel or a dict, depending
        # on the call path (raw_response.parse() returns BaseModel; the
        # non-raw path occasionally returns dict). Normalise to a list of
        # raw choice dicts.
        choices = _extract_choices(response)
        if not choices:
            return result

        for gen, choice in zip(result.generations, choices, strict=False):
            msg = gen.message
            if not isinstance(msg, AIMessage):
                continue
            rc = _extract_reasoning_content(choice)
            if rc:
                msg.additional_kwargs = {**(msg.additional_kwargs or {}), "reasoning_content": rc}
        return result


def _extract_choices(response: Any) -> list[dict]:
    if response is None:
        return []
    if isinstance(response, dict):
        return list(response.get("choices") or [])
    # openai SDK BaseModel: .choices is a list of Choice models with
    # .model_dump() support.
    raw = getattr(response, "choices", None) or []
    out: list[dict] = []
    for c in raw:
        if isinstance(c, dict):
            out.append(c)
        elif hasattr(c, "model_dump"):
            out.append(c.model_dump())
        else:
            out.append({})
    return out


def _extract_reasoning_content(choice: dict) -> str | None:
    """Pull the reasoning text out of a single choice dict.

    Servers vary: most put it on ``message.reasoning_content``, but a few
    nest it under ``message.reasoning`` or expose a ``thoughts`` array.
    Returns the first non-empty string we find.
    """
    msg = choice.get("message") or {}
    for key in ("reasoning_content", "reasoning"):
        val = msg.get(key)
        if isinstance(val, str) and val.strip():
            return val
    # Some providers return [{"type": "text", "text": "..."}, ...]
    val = msg.get("reasoning_content") or msg.get("reasoning")
    if isinstance(val, list):
        parts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in val]
        joined = "".join(parts).strip()
        if joined:
            return joined
    return None
