"""End to end over the real MCP protocol: the server runs as a subprocess, the official client calls it."""

from __future__ import annotations

import shutil
import sys

import anyio
from conftest import AGENT, CASEWORKER, REPORTING
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from swda_agent.config import ROOT
from swda_agent.models import Recommendation


def _params(tmp_path, token):
    shutil.copy(ROOT / "config/registry.yaml", tmp_path / "registry.yaml")
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "swda_agent", "serve"],
        cwd=str(ROOT),
        env={
            "SWDA_CLIENT_TOKEN": token,
            "SWDA_REGISTRY_PATH": str(tmp_path / "registry.yaml"),
            "SWDA_TRACE_PATH": str(tmp_path / "traces.jsonl"),
            "SWDA_AUDIT_PATH": str(tmp_path / "audit.jsonl"),
            "SWDA_FIXED_NOW": "2026-11-23T09:00:00+08:00",
            "PYTHONUTF8": "1",
        },
    )


def _session(tmp_path, token, body):
    async def main():
        async with stdio_client(_params(tmp_path, token)) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await body(session)

    return anyio.run(main)


def test_mcp_discovery_and_call(tmp_path, request_payload):
    async def body(session):
        tools = (await session.list_tools()).tools
        result = await session.call_tool(AGENT, request_payload)
        return tools, result

    tools, result = _session(tmp_path, CASEWORKER, body)
    assert [t.name for t in tools] == [AGENT]
    assert tools[0].inputSchema["properties"]["applicant"]
    assert not result.isError
    rec = Recommendation.model_validate(result.structuredContent)
    assert rec.category == "employment_support"
    assert (tmp_path / "audit.jsonl").read_text(encoding="utf-8").count('"decision": "allow"') == 1


def test_mcp_denies_unpermitted_client_with_error_code(tmp_path, request_payload):
    async def body(session):
        tools = (await session.list_tools()).tools
        result = await session.call_tool(AGENT, request_payload)
        return tools, result

    tools, result = _session(tmp_path, REPORTING, body)
    assert tools == []
    assert result.isError and result.structuredContent == {
        "error": "CLIENT_NOT_PERMITTED",
        "message": f"Agent '{AGENT}' v1.0.0 is not available (CLIENT_NOT_PERMITTED)",
    }
