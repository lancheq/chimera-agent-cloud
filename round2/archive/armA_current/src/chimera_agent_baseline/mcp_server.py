"""MCP server exposing the per-task clinical tool registry.

Runs as a stdio-based MCP server, framework-agnostic (any MCP client
works — the runner uses :mod:`langchain_mcp_adapters`).

Usage::

    python -m chimera_agent_baseline.mcp_server \\
        --data-dir data/task1/agent_input --resource-dir resources \\
        --tool-registry task1

.. note::

    Tools are optional and not scored: only the final structured output
    (``Task1Output`` / ``Task2Output`` / ``Task3Output``) is evaluated.
    They mirror the masked "Extended EHR view" sections of the urologist
    forms and reveal those documents on request, so calling them gives
    the agent more evidence to reason over — add new ones, edit existing
    ones, or swap the registry freely.
"""

import argparse
import json
import logging

from mcp.server.fastmcp import FastMCP

from chimera_agent_baseline.features import FeatureStore
from chimera_agent_baseline.tools.base import CaseDataStore, ToolSpec
from chimera_agent_baseline.tools.definitions import TASK1_TOOLS, TASK2_TOOLS, TASK3_TOOLS
from chimera_agent_baseline.tools.predictor import make_predictor_tool
from chimera_agent_baseline.utils import setup_logging

_REGISTRIES: dict[str, list[ToolSpec]] = {
    "task1": TASK1_TOOLS,
    "task2": TASK2_TOOLS,
    "task3": TASK3_TOOLS,
}

log = logging.getLogger(__name__)


def create_server(
    data_dir: str,
    resource_dir: str | None = None,
    tools: list[ToolSpec] | None = None,
    name: str = "Chimera Tools",
    predictor_enabled: bool = False,
) -> FastMCP:
    """Create an MCP server with precomputed clinical tools.

    When *predictor_enabled* is true, an optional ``get_image_predictor`` tool
    is registered over the per-case ``features.json`` embeddings — it receives
    all origins (MRI / biopsy / prostatectomy) so participants can fuse them
    (see :mod:`chimera_agent_baseline.tools.predictor`). Off by default — the
    baseline does not consume embeddings.
    """
    mcp = FastMCP(name)
    store = CaseDataStore(data_dir)
    tools = tools if tools is not None else TASK1_TOOLS

    # -- Precomputed data tools ------------------------------------------------

    for tool_spec in tools:
        _register_precomputed_tool(mcp, store, tool_spec)

    # -- Optional image-embedding predictor (template, off by default) ---------

    if predictor_enabled:
        feature_store = FeatureStore(data_dir)
        mcp.tool()(make_predictor_tool(feature_store))

    # -- Knowledge retrieval (RAG) ---------------------------------------------

    guidelines_search = _load_guidelines_search(resource_dir)

    @mcp.tool()
    def search_guidelines(query: str) -> str:
        """Search clinical guidelines and protocols relevant to the query.

        Uses semantic similarity search over a knowledge base of clinical
        guidelines (e.g. EAU prostate-cancer guidelines, NCCN protocols).
        Returns the most relevant guideline passages. Free-text query;
        phrase as a clinical question.
        """
        if guidelines_search is None:
            return json.dumps(
                {
                    "query": query,
                    "results": [],
                    "note": "Guidelines DB not available. Run: make process-guidelines",
                }
            )
        results = guidelines_search.query(query)
        return json.dumps({"query": query, "results": results})

    return mcp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_guidelines_search(resource_dir: str | None):
    if resource_dir is None:
        return None
    try:
        from chimera_agent_baseline.rag import GuidelinesSearch

        return GuidelinesSearch(resource_dir)
    except FileNotFoundError:
        log.info("Guidelines DB not found in %s — search_guidelines will return empty results", resource_dir)
        return None
    except Exception:
        log.warning("Failed to load guidelines search", exc_info=True)
        return None


def _register_precomputed_tool(mcp: FastMCP, store: CaseDataStore, spec: ToolSpec) -> None:
    """Register a single precomputed-data tool on the MCP server."""
    fields = spec.fields

    def tool_fn(case_id: str) -> str:
        try:
            result = store.extract(case_id, fields)
        except KeyError as exc:
            return json.dumps({"error": str(exc)})

        if set(result.keys()) == {"case_id"}:
            return json.dumps({"case_id": case_id, "note": "No data available for this tool and case."})

        return json.dumps(result)

    tool_fn.__name__ = spec.name
    tool_fn.__doc__ = spec.description
    tool_fn.__annotations__ = {"case_id": str, "return": str}

    mcp.tool()(tool_fn)


# ---------------------------------------------------------------------------
# CLI entry-point (stdio transport)
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Chimera MCP tool server")
    parser.add_argument("--data-dir", required=True, help="Path to per-case agent input directory")
    parser.add_argument("--resource-dir", default=None, help="Path to resources directory (guidelines_db/, etc.)")
    parser.add_argument(
        "--tool-registry",
        choices=sorted(_REGISTRIES),
        default="task1",
        help="Which ToolSpec registry to expose. 'task1' = biopsy-decision "
        "tools (PSA trend, labs, MRI report, pathology report, previous "
        "notes, family history); 'task2' = treatment-decision tools "
        "(PSA trend + labs dropped, pathology returns richer per-core data).",
    )
    parser.add_argument(
        "--enable-predictor",
        action="store_true",
        help="Register the optional get_image_predictor tool over features.json embeddings (default: off).",
    )
    parser.add_argument("--log-level", default="INFO", help="Log level (DEBUG, INFO, WARNING, ERROR)")
    args = parser.parse_args()

    setup_logging(args.log_level)
    server = create_server(
        args.data_dir,
        resource_dir=args.resource_dir,
        tools=_REGISTRIES[args.tool_registry],
        predictor_enabled=args.enable_predictor,
    )
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
