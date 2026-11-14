"""Verify env propagation in a real stdio MCP child, including secret exclusion."""

import asyncio
import json
import sys

from langchain_mcp_adapters.client import MultiServerMCPClient

from chimera_agent_baseline.run import _mcp_environment


def test_real_mcp_child_receives_socket_and_switches_without_api_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("CHIMERA_EMBED_SOCKET", "/tmp/round2_test_only.sock")
    monkeypatch.setenv("CHIMERA_RERANKER", "off")
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret-must-not-cross")
    server = tmp_path / "env_server.py"
    server.write_text('''import os, json
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("env-test")
@mcp.tool()
def inspect_env() -> str:
    """Return selected runtime settings, never a secret value."""
    return json.dumps({"socket": os.environ.get("CHIMERA_EMBED_SOCKET"),
                       "reranker": os.environ.get("CHIMERA_RERANKER"),
                       "offline": os.environ.get("HF_HUB_OFFLINE"),
                       "has_api_secret": "OPENAI_API_KEY" in os.environ})
mcp.run(transport="stdio")
''')

    async def inspect():
        client = MultiServerMCPClient({"test": {
            "command": sys.executable, "args": [str(server)], "transport": "stdio", "env": _mcp_environment(),
        }})
        tools = await client.get_tools()
        return await tools[0].ainvoke({})

    result = asyncio.run(inspect())
    # Adapters represent text responses as content-block lists.
    if isinstance(result, list):
        result = next(block["text"] for block in result if block.get("type") == "text")
    payload = json.loads(result)
    assert payload == {"socket": "/tmp/round2_test_only.sock", "reranker": "off", "offline": "1", "has_api_secret": False}
