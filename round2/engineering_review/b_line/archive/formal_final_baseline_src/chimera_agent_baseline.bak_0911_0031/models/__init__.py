"""Model loading.

Two backends are supported, selected via ``cfg.model.provider``:

* ``vllm``   -- in-process inference via :mod:`vllm` (default for the
  Grand Challenge container).
* ``openai`` -- any OpenAI-compatible HTTP endpoint (llama.cpp's
  ``llama-server``, vLLM's OpenAI server, OpenAI itself, etc.). Useful
  for swapping models without rebuilding vLLM, and for verifying that
  the orchestration is provider-neutral.

The downstream graph only depends on :class:`langchain_core.language_models.BaseChatModel`,
which both backends return.
"""

import logging
import os
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from omegaconf import DictConfig

log = logging.getLogger(__name__)


def load_model(cfg: DictConfig) -> BaseChatModel:
    """Instantiate the chat model selected by ``cfg.model.provider``."""
    provider = cfg.model.get("provider", "vllm")
    if provider == "vllm":
        return _load_vllm(cfg)
    if provider == "openai":
        return _load_openai(cfg)
    raise ValueError(f"Unknown model.provider: {provider!r} (expected 'vllm' or 'openai')")


def _load_vllm(cfg: DictConfig) -> BaseChatModel:
    try:
        from vllm import LLM, SamplingParams
    except ImportError as exc:
        raise ImportError("vLLM is required for provider='vllm'. Install with: pip install vllm") from exc

    from chimera_agent_baseline.models.vllm_offline import ChatVLLM

    model_path = _resolve_model_path(cfg)
    log.info("Loading vLLM model from %s", model_path)

    #version sander
    #llm = LLM(
        #model=model_path,
        #dtype="auto",
        #max_model_len=cfg.generation.get("max_model_len", 32768),
        #gpu_memory_utilization=cfg.generation.get("gpu_memory_utilization", 0.9),
    #)

    tp_size = int(cfg.generation.get("tensor_parallel_size", 1))
    log.info("vLLM tensor_parallel_size=%s", tp_size)

    llm = LLM(
        model=model_path,
        dtype="auto",
        max_model_len=cfg.generation.get("max_model_len", 32768),
        gpu_memory_utilization=cfg.generation.get("gpu_memory_utilization", 0.9),
        tensor_parallel_size=tp_size,
        enforce_eager=True,
        disable_custom_all_reduce=True,
    )

    params = SamplingParams(
        temperature=cfg.generation.temperature,
        max_tokens=cfg.generation.max_new_tokens,
        top_p=cfg.generation.top_p,
    )

    tool_parser = cfg.model.get("tool_parser", "gemma4")
    return ChatVLLM(llm=llm, sampling_params=params, tool_parser=tool_parser)


def _load_openai(cfg: DictConfig) -> BaseChatModel:
    """Build a :class:`ChatOpenAI` pointing at any OpenAI-compatible server.

    Reads:
      * ``cfg.model.model_id``  -> served model name
      * ``cfg.model.base_url``  -> e.g. ``http://localhost:8765/v1``
      * ``cfg.model.api_key_env`` (default ``LLAMA_API_KEY``) -> env var
        holding the bearer token. Falls back to ``OPENAI_API_KEY``.
        Empty / missing keys send a placeholder so llama.cpp's optional
        ``--api-key`` enforcement is happy when unset locally.

    Tool calling: ``ChatOpenAI`` natively supports ``bind_tools``, which
    is what the ReAct loop needs. The terminal form_fill node does NOT
    use function calling — it goes through prompt + PydanticOutputParser
    so any model behind any OpenAI-compatible server works.
    """
    try:
        from chimera_agent_baseline.models.openai_compat import ChatOpenAICompat as ChatOpenAI
    except ImportError as exc:
        raise ImportError(
            "langchain-openai is required for provider='openai'. Install with: pip install langchain-openai"
        ) from exc

    base_url = cfg.model.get("base_url")
    if not base_url:
        raise ValueError("cfg.model.base_url is required when provider='openai'")

    api_key_env = cfg.model.get("api_key_env", "LLAMA_API_KEY")
    api_key = os.environ.get(api_key_env) or os.environ.get("OPENAI_API_KEY") or "no-key"

    log.info("Loading OpenAI-compatible model %s from %s", cfg.model.model_id, base_url)

    extra_body = cfg.model.get("extra_body")
    if extra_body is not None:
        # OmegaConf gives us a DictConfig; ChatOpenAI wants a plain dict.
        from omegaconf import OmegaConf

        extra_body = OmegaConf.to_container(extra_body, resolve=True)

    return ChatOpenAI(
        model=cfg.model.model_id,
        base_url=base_url,
        api_key=api_key,
        temperature=cfg.generation.temperature,
        max_tokens=cfg.generation.max_new_tokens,
        top_p=cfg.generation.top_p,
        timeout=cfg.model.get("request_timeout", 600),
        extra_body=extra_body or {},
    )


def _resolve_model_path(cfg: DictConfig) -> str:
    """Return local model dir if non-empty, else the HF hub model ID."""
    model_dir = Path(cfg.paths.model_dir)
    if model_dir.exists() and any(f for f in model_dir.iterdir() if not f.name.startswith(".")):
        return str(model_dir)
    return cfg.model.model_id
