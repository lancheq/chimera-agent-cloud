"""MCP server exposing the per-task clinical tool registry.

Runs as a stdio-based MCP server, framework-agnostic (any MCP client
works — the runner uses :mod:`langchain_mcp_adapters`).

Usage::

    python -m chimera_agent_baseline.mcp_server \\
        --data-dir data/task1/agent_input --resource-dir resources \\
        --tool-registry task1

.. note::

    Clinical tools reveal masked precomputed EHR sections. Official output
    scoring includes revealed-section usage; that score is distinct from
    the opt-in per-case execution budget implemented by --access-session.
"""

import argparse
import json
import logging
import os

from mcp.server.fastmcp import FastMCP

from chimera_agent_baseline.features import FeatureStore
from chimera_agent_baseline.tools.base import CaseDataStore, ToolSpec
from chimera_agent_baseline.tools.definitions import TASK1_TOOLS, TASK2_TOOLS, TASK3_TOOLS
from chimera_agent_baseline.tools.predictor import make_predictor_tool
from chimera_agent_baseline.utils import setup_logging
from chimera_agent_baseline.runtime_audit import ComponentFailure, component_event
from chimera_agent_baseline.information_access import InformationAccess, AccessDenied, BoundCaseDataStore
from mcp.server.fastmcp.exceptions import ToolError

_REGISTRIES: dict[str, list[ToolSpec]] = {
    "task1": TASK1_TOOLS,
    "task2": TASK2_TOOLS,
    "task3": TASK3_TOOLS,
}

log = logging.getLogger(__name__)


def create_server(
    data_dir: str,
    resource_dir: str | None = None,
    reranker_dir: str | None = None,
    tools: list[ToolSpec] | None = None,
    name: str = "Chimera Tools",
    predictor_enabled: bool = False,
    access_config: dict | None = None,
) -> FastMCP:
    """Create an MCP server with precomputed clinical tools.

    When *predictor_enabled* is true, an optional ``get_image_predictor`` tool
    is registered over the per-case ``features.json`` embeddings — it receives
    all origins (MRI / biopsy / prostatectomy) so participants can fuse them
    (see :mod:`chimera_agent_baseline.tools.predictor`). Off by default — the
    baseline does not consume embeddings.
    """
    mcp = FastMCP(name)
    if access_config is not None and 'pilot_tools' in access_config:
        from chimera_agent_baseline.case_pilot import verify_child_isolation
        verify_child_isolation(data_dir)
    access = InformationAccess.from_config(data_dir, access_config) if access_config is not None else None
    packager = None
    if access_config is not None and access_config.get('evidence_package') is not None:
        from chimera_agent_baseline.evidence_package import EvidencePackager
        packager = EvidencePackager(access_config['evidence_package'], access, data_dir)
    if access is not None and predictor_enabled:
        raise ValueError('Guarded image predictor needs verified modality authorization; cannot enable yet')
    store = BoundCaseDataStore(data_dir,access) if access is not None else CaseDataStore(data_dir)
    tools = tools if tools is not None else TASK1_TOOLS
    if access_config is not None and 'pilot_tools' in access_config:
        tools = [spec for spec in tools if spec.name in access.contract['tools']]
    if access is not None:
        for spec in tools:
            expected=access.contract['tools'].get(spec.name)
            if expected is None or tuple(expected['fields'])!=spec.fields:
                raise ValueError('Tool registry differs from the bound access contract')

    # -- Precomputed data tools ------------------------------------------------

    for tool_spec in tools:
        _register_precomputed_tool(mcp, store, tool_spec, access=access)

    # -- Optional image-embedding predictor (template, off by default) ---------

    if predictor_enabled:
        feature_store = FeatureStore(data_dir)
        mcp.tool()(make_predictor_tool(feature_store))

    # -- Knowledge retrieval (RAG) ---------------------------------------------

    guidelines_search = None if access is not None else _load_guidelines_search(resource_dir, reranker_dir)

    def guarded_search(query):
        nonlocal guidelines_search
        if guidelines_search is None:
            from chimera_agent_baseline.knowledge_access import create_search
            guidelines_search = create_search(access_config['knowledge_manifest'], access.binding['knowledge_snapshot_id'])
        hits = guidelines_search.query(query)
        return packager.package(query, hits) if packager is not None else {'query': query, 'results': hits}

    @mcp.tool()
    def search_guidelines(query: str) -> str:
        """Search clinical guidelines and protocols relevant to the query.

        Uses semantic similarity search over a knowledge base of clinical
        guidelines (e.g. EAU prostate-cancer guidelines, NCCN protocols).
        Returns the most relevant guideline passages. Free-text query;
        phrase as a clinical question.
        """
        if access is not None:
            if packager is not None:
                from chimera_agent_baseline.evidence_package import load_overlay
                load_overlay(packager.config['overlay_path'], packager.config['overlay_sha256'])
            # Admission precedes lazy verified loading; cached queries never
            # invoke a backend and unverified resources fail explicitly.
            try:
                value=access.execute('search_guidelines',access.binding['case_id'],
                    lambda: guarded_search(query),query=query)
                if packager is not None:
                    packager.validate_delivery(value)
            except AccessDenied as exc:
                raise ToolError('information_access:'+str(exc)) from None
            return json.dumps(value)
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


def _load_guidelines_search(resource_dir: str | None, reranker_dir: str | None = None):
    if resource_dir is None:
        return None
    try:
        from chimera_agent_baseline.rag import GuidelinesSearch

        search = GuidelinesSearch(resource_dir, reranker_dir=reranker_dir)
        component_event("guidelines_load", configured=True, status="executed", resource_dir=str(resource_dir))
        return search
    except ComponentFailure:
        raise
    except FileNotFoundError as exc:
        component_event("guidelines_load", configured=True, status="fallback", reason=str(exc),
                        fallback="empty_guidelines", required=True)
        log.info("Guidelines DB not found in %s — search_guidelines will return empty results", resource_dir)
        return None
    except Exception as exc:
        component_event("guidelines_load", configured=True, status="fallback", reason=str(exc),
                        fallback="empty_guidelines", required=True)
        log.warning("Failed to load guidelines search", exc_info=True)
        return None


def _register_precomputed_tool(mcp: FastMCP, store: CaseDataStore, spec: ToolSpec, access: InformationAccess | None = None) -> None:
    """Register a single precomputed-data tool on the MCP server."""
    fields = spec.fields

    def tool_fn(case_id: str) -> str:
        if access is not None:
            try:
                result=access.execute(spec.name,case_id,lambda:store.extract(case_id,fields))
            except AccessDenied as exc:
                raise ToolError('information_access:'+str(exc)) from None
            if set(result)=={'case_id'}:result['note']='No data available for this tool and case.'
            return json.dumps(result)
        try:
            result = store.extract(case_id, fields)
        except KeyError as exc:
            component_event("case_tool_lookup", configured=True, status="fallback", reason=str(exc),
                            fallback="error_payload", required=True, tool=spec.name, case_id=case_id)
            return json.dumps({"error": str(exc)})

        component_event("case_tool_lookup", configured=True, status="executed", tool=spec.name, case_id=case_id)

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
    parser.add_argument("--reranker-dir", default=None, help="Path to reranker model dir (optional; defaults to resource-dir)")
    parser.add_argument(
        "--embed-socket",
        default=None,
        help="Path to the embedding-service unix socket. MCP SDK strips the parent env, "
        "so CHIMERA_EMBED_SOCKET cannot reach this process; run.py forwards it here "
        "instead (2026-10-27). Must be set before chimera_agent_baseline.rag is imported.",
    )
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
    parser.add_argument('--access-session',default=None,help='Opt-in trusted per-case JSON: immutable source binding, SQLite ledger and budgets. Not accepted from model tool arguments.')
    args = parser.parse_args()

    # 必须在 rag 模块（其模块级 SOCKET_PATH 读环境变量）被导入之前生效。
    if args.embed_socket:
        os.environ["CHIMERA_EMBED_SOCKET"] = args.embed_socket

    setup_logging(args.log_level)
    server = create_server(
        args.data_dir,
        resource_dir=args.resource_dir,
        reranker_dir=args.reranker_dir,
        tools=_REGISTRIES[args.tool_registry],
        predictor_enabled=args.enable_predictor,
        access_config=json.loads(open(args.access_session).read()) if args.access_session else None,
    )
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
