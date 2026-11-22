"""MCP adapter: exposes approved agents as MCP tools, using the official MCP Python SDK.

This is the only module that imports the SDK. All policy lives in the gateway, so the
transport can change (stdio here; streamable HTTP behind the ALB in production) without
touching the controls.

Identity: over stdio the client process starts this server and passes its credential in
SWDA_CLIENT_TOKEN. Over HTTP in production the gateway reads a Cognito-issued bearer token
from each request instead.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .app import build_gateway
from .config import Settings
from .errors import GatewayError
from .gateway import Gateway
from .models import Recommendation, ServiceRequest
from .ports import Clock, FakeClock


def build_server(gateway: Gateway, client_token: str | None) -> Server:
    server: Server = Server("swda-agent-gateway")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        # Discovery is filtered per caller: unapproved, expired, revoked or
        # not-permitted agents are simply not listed.
        try:
            client = gateway.authenticate(client_token)
        except GatewayError:
            return []
        return [
            types.Tool(
                name=agent.agent_id,
                title=f"{agent.agent_id} v{agent.version}",
                description=gateway.describe(agent),
                inputSchema=ServiceRequest.model_json_schema(),
                outputSchema=Recommendation.model_json_schema(),
            )
            for agent in gateway.discover(client)
        ]

    # validate_input=False: the gateway is the single place that validates, so that
    # every rejection gets the same error codes and an audit record.
    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        try:
            result = gateway.invoke(client_token, name, arguments or {})
        except GatewayError as e:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(e.to_dict()))],
                structuredContent=e.to_dict(),
                isError=True,
            )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(result))],
            structuredContent=result,
            isError=False,
        )

    return server


def _clock_from_env() -> Clock | None:
    fixed = os.environ.get("SWDA_FIXED_NOW")
    return FakeClock(datetime.fromisoformat(fixed)) if fixed else None


async def serve_stdio() -> None:
    # The SDK warns when a caller invokes a tool it was not shown. That is an expected,
    # audited denial here, so keep stderr quiet.
    logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.ERROR)
    gateway = build_gateway(Settings.from_env(), clock=_clock_from_env())
    server = build_server(gateway, os.environ.get("SWDA_CLIENT_TOKEN"))
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
