"""vLLM offline model backend.

Wraps :class:`vllm.LLM` as a LangChain :class:`BaseChatModel` so it can be
used directly with ``bind_tools()`` and the ReAct graph — no HTTP server
needed.  Tool-call parsing uses vLLM's built-in Gemma 4 parser.

This is the recommended backend for the Grand Challenge container where
everything runs in a single process.

Requires::

    pip install vllm
"""

import json
import logging
from typing import Any
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, model_validator

log = logging.getLogger(__name__)


class ChatVLLM(BaseChatModel):
    """LangChain chat model backed by vLLM offline inference.

    Supports ``bind_tools()`` and structured ``tool_calls`` in responses,
    which is required by the LangGraph ReAct loop.

    Usage::

        from vllm import LLM, SamplingParams
        from chimera_agent_baseline.models.vllm_offline import ChatVLLM

        llm = LLM(model="./model", dtype="auto")
        model = ChatVLLM(
            llm=llm,
            sampling_params=SamplingParams(temperature=1.0, max_tokens=4096),
        )
        model_with_tools = model.bind_tools(tools)
        response = model_with_tools.invoke(messages)
    """

    llm: Any = Field(exclude=True)
    """A :class:`vllm.LLM` instance."""

    sampling_params: Any = Field(exclude=True)
    """A :class:`vllm.SamplingParams` instance."""

    tokenizer: Any = Field(default=None, exclude=True)
    """Tokenizer for decoding with special tokens.  Auto-resolved from *llm*."""

    tool_parser: str = Field(default="gemma4")
    """Name of the vLLM tool-call parser (gemma4, hermes, llama, …)."""

    bound_tools: list[dict] = Field(default_factory=list)
    """OpenAI-format tool schemas, set via :meth:`bind_tools`."""

    model_config = {"arbitrary_types_allowed": True}

    @model_validator(mode="after")
    def _resolve_tokenizer(self) -> "ChatVLLM":
        if self.tokenizer is None:
            self.tokenizer = self.llm.get_tokenizer()
        return self

    # -- LangChain interface ---------------------------------------------------

    @property
    def _llm_type(self) -> str:
        return "vllm-offline"

    def bind_tools(self, tools: list, **kwargs: Any) -> "ChatVLLM":
        """Return a copy of this model with tools bound."""
        from langchain_core.utils.function_calling import convert_to_openai_tool

        formatted = [convert_to_openai_tool(t) for t in tools]
        return self.model_copy(update={"bound_tools": formatted, "tool_parser": self.tool_parser})

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        msg_dicts = _to_openai_messages(messages)
        tools = self.bound_tools or None

        outputs = self.llm.chat(
            messages=msg_dicts,
            sampling_params=self.sampling_params,
            tools=tools,
        )

        output = outputs[0].outputs[0]

        # Decode WITH special tokens so the tool-call parser can see markers
        full_text = self.tokenizer.decode(output.token_ids, skip_special_tokens=False)
        clean_text = output.text  # readable version without special tokens

        # Parse tool calls using the configured parser
        tool_calls = _parse_tool_calls(full_text, self.tool_parser)

        if tool_calls:
            lc_tool_calls = [
                {
                    "name": tc["name"],
                    "args": tc["arguments"],
                    "id": str(uuid4()),
                    "type": "tool_call",
                }
                for tc in tool_calls
            ]
            # Preserve any reasoning text the model produced before the
            # tool calls (e.g. "I need to check the Gleason grading...").
            # Gemma 4 E2B doesn't produce this, but larger models might.
            reasoning = _extract_reasoning_prefix(clean_text, tool_calls)
            message = AIMessage(content=reasoning, tool_calls=lc_tool_calls)
        else:
            message = AIMessage(content=clean_text)

        return ChatResult(generations=[ChatGeneration(message=message)])


# ---------------------------------------------------------------------------
# Message conversion
# ---------------------------------------------------------------------------


def _to_openai_messages(messages: list[BaseMessage]) -> list[dict]:
    """Convert LangChain messages to OpenAI-format dicts for vLLM chat."""
    result = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            result.append({"role": "system", "content": msg.content})
        elif isinstance(msg, HumanMessage):
            result.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AIMessage):
            entry: dict = {"role": "assistant"}
            if msg.tool_calls:
                entry["content"] = None
                entry["tool_calls"] = [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["args"]) if isinstance(tc["args"], dict) else tc["args"],
                        },
                    }
                    for tc in msg.tool_calls
                ]
            else:
                entry["content"] = msg.content
            result.append(entry)
        elif isinstance(msg, ToolMessage):
            result.append(
                {
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content,
                }
            )
        else:
            result.append({"role": "user", "content": str(msg.content)})
    return result


def _extract_reasoning_prefix(clean_text: str, tool_calls: list[dict]) -> str:
    """Extract any reasoning text that appeared before the first tool call.

    Models like Llama 3 or larger Gemma variants may produce text like
    "I need to check the clinical data first" before their tool calls.
    Gemma 4 E2B typically doesn't, but preserving this enables richer
    reasoning traces for participants using bigger models.
    """
    if not clean_text:
        return ""

    # Find where the first tool call starts in the clean text
    first_tool = tool_calls[0]["name"] if tool_calls else ""
    # Look for "call:<name>" pattern (Gemma) or the function name
    for marker in [f"call:{first_tool}", first_tool]:
        idx = clean_text.find(marker)
        if idx > 0:
            return clean_text[:idx].strip()

    return ""


# ---------------------------------------------------------------------------
# Tool-call parsing — dispatches to vLLM's offline parser-utils module
# matching the configured ``tool_parser`` name. Standard vLLM ships
# ``<name>_utils.parse_tool_calls`` for gemma4, hermes, llama, mistral,
# pythonic, and others. If you need a model with a parser not in vLLM,
# either contribute the matching ``*_utils`` module upstream or use the
# OpenAI-compatible provider (configs/experiment/qwen_local.yaml) instead.
# ---------------------------------------------------------------------------


def _parse_tool_calls(text: str, parser: str = "gemma4") -> list[dict]:
    """Extract tool calls from decoded model output via vLLM's parser utils.

    A missing parser module or one without ``parse_tool_calls`` means the
    agent could never issue a tool call, which would silently degrade the
    whole ReAct loop — so this raises instead of returning an empty list.
    """
    import importlib

    module_path = f"vllm.tool_parsers.{parser}_utils"
    try:
        mod = importlib.import_module(module_path)
    except ImportError as exc:
        raise RuntimeError(
            f"Could not import vLLM tool-parser module {module_path!r} for "
            f"model.tool_parser={parser!r}. Set model.tool_parser to a parser "
            f"shipped by your vLLM install, or use the OpenAI-compatible provider "
            f"(configs/experiment/qwen_local.yaml)."
        ) from exc
    try:
        return mod.parse_tool_calls(text, strict=False)
    except AttributeError as exc:
        raise RuntimeError(
            f"vLLM module {module_path!r} has no parse_tool_calls(); it cannot back model.tool_parser={parser!r}."
        ) from exc
