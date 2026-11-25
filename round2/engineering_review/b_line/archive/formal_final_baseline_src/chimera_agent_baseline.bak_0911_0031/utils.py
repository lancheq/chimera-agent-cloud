"""Shared utilities for Chimera Agent Baseline."""

import logging

LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"

# Third-party loggers that are excessively noisy at INFO level.
_QUIET_LOGGERS = [
    "vllm",
    "httpx",
    "httpcore",
    "chromadb",
    "mcp",
]


def setup_logging(level: str = "INFO") -> None:
    """Configure logging for the application.

    Call this once from each entry point (``run.py``, ``inference.py``).
    The MCP server subprocess has its own ``setup_logging`` call since
    it runs in a separate process.

    Args:
        level: Root log level (DEBUG, INFO, WARNING, ERROR). Noisy
            third-party loggers (vLLM, httpx, chromadb) are clamped to
            WARNING unless *level* is DEBUG.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        level=numeric_level,
        format=LOG_FORMAT,
        force=True,  # override any prior basicConfig
    )

    # Quiet down noisy libraries unless we're in DEBUG mode.
    if numeric_level > logging.DEBUG:
        for name in _QUIET_LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)
