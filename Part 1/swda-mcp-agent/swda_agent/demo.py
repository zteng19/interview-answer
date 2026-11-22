"""End-to-end demo over the real MCP protocol.

Each scene starts the MCP server as a subprocess (stdio transport), connects with the
official MCP client, and acts as one calling application. Nothing is mocked between the
client and the gateway.
"""

from __future__ import annotations

import json
import shutil
import sys
from contextlib import AsyncExitStack
from typing import Any

import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import ROOT

DEMO_DIR = ROOT / "out" / "demo"
SAMPLES = ROOT / "samples"
FIXED_NOW = "2026-11-23T09:00:00+08:00"  # pinned so registry expiry dates give the same result every run
AGENT = "service-recommendation-agent"


def _tokens() -> dict[str, str]:
    with (ROOT / "examples" / "dev_tokens.yaml").open(encoding="utf-8") as f:
        return dict(yaml.safe_load(f))


def _params(token: str | None) -> StdioServerParameters:
    env = {
        "SWDA_REGISTRY_PATH": str(DEMO_DIR / "registry.yaml"),
        "SWDA_TRACE_PATH": str(DEMO_DIR / "traces.jsonl"),
        "SWDA_AUDIT_PATH": str(DEMO_DIR / "audit.jsonl"),
        "SWDA_FIXED_NOW": FIXED_NOW,
        "PYTHONUTF8": "1",
    }
    if token is not None:
        env["SWDA_CLIENT_TOKEN"] = token
    return StdioServerParameters(command=sys.executable, args=["-m", "swda_agent", "serve"], env=env, cwd=str(ROOT))


class Session:
    """One calling application connected over MCP."""

    def __init__(self, token: str | None) -> None:
        self.params = _params(token)
        self._stack = AsyncExitStack()

    async def __aenter__(self) -> Session:
        read, write = await self._stack.enter_async_context(stdio_client(self.params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._stack.aclose()

    async def tools(self) -> list[str]:
        return [t.name for t in (await self._session.list_tools()).tools]

    async def call(self, name: str, args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        result = await self._session.call_tool(name, args)
        return (not result.isError), dict(result.structuredContent or {})


def _line(label: str, value: object) -> None:
    print(f"  {label:<44} {value}")


async def run_demo() -> int:
    if DEMO_DIR.exists():
        shutil.rmtree(DEMO_DIR)
    DEMO_DIR.mkdir(parents=True)
    shutil.copy(ROOT / "config" / "registry.yaml", DEMO_DIR / "registry.yaml")  # demo may revoke; keep config clean
    tokens = _tokens()
    requests = [
        json.loads(x)
        for x in (ROOT / "examples" / "sample_requests.jsonl").read_text(encoding="utf-8").splitlines()
        if x
    ]
    outcomes: list[tuple[str, bool]] = []
    recommendations: list[dict[str, Any]] = []

    def expect(label: str, ok: bool) -> None:
        outcomes.append((label, ok))
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

    print("\n1. caseworker-app: discovery and recommendations")
    async with Session(tokens["caseworker-app"]) as s:
        listed = await s.tools()
        _line("tools/list returns", listed)
        expect("only the approved, unexpired, permitted agent is listed", listed == [AGENT])
        for req in requests:
            ok, out = await s.call(AGENT, req)
            recommendations.append(out)
            top = [f"{r['programme_id']}{'' if r['eligible'] else '(x)'}" for r in out.get("recommendations", [])]
            _line(
                f"{req['request_id']} -> {out.get('category')}",
                f"{out.get('next_step')} {top} flags={out.get('flags')}",
            )
            expect(f"{req['request_id']} returns a schema-valid recommendation", ok)
        ok, out = await s.call(AGENT, {"request_id": "BAD", "channel": "fax", "text": "", "applicant": {}, "extra": 1})
        _line("malformed request", out)
        expect("malformed request rejected with INVALID_REQUEST", not ok and out.get("error") == "INVALID_REQUEST")
        ok, out = await s.call(AGENT, {**requests[0], "text": "x" * 20000})
        expect("oversized request rejected with PAYLOAD_TOO_LARGE", not ok and out.get("error") == "PAYLOAD_TOO_LARGE")
        ok, out = await s.call("legacy-triage-agent", requests[0])
        expect("expired agent cannot be called (AGENT_EXPIRED)", not ok and out.get("error") == "AGENT_EXPIRED")
        ok, out = await s.call("case-summary-agent", requests[0])
        expect(
            "unapproved agent cannot be called (AGENT_NOT_APPROVED)",
            not ok and out.get("error") == "AGENT_NOT_APPROVED",
        )

    print("\n2. reporting-service: authenticated, but not approved for this agent")
    async with Session(tokens["reporting-service"]) as s:
        listed = await s.tools()
        _line("tools/list returns", listed)
        ok, out = await s.call(AGENT, requests[0])
        expect(
            "agent hidden and call denied (CLIENT_NOT_PERMITTED)",
            listed == [] and out.get("error") == "CLIENT_NOT_PERMITTED",
        )

    print("\n3. unknown and disabled credentials")
    for label, token in [
        ("invalid token", "not-a-real-token"),
        ("disabled client", tokens["retired-batch-client"]),
        ("no token", None),
    ]:
        async with Session(token) as s:
            listed = await s.tools()
            ok, out = await s.call(AGENT, requests[0])
            expect(
                f"{label}: nothing listed, call denied (UNAUTHENTICATED)",
                listed == [] and out.get("error") == "UNAUTHENTICATED",
            )

    print("\n4. kill switch: revoke the agent in the registry")
    from .adapters import YamlRegistryStore
    from .approval import revoke

    revoke(AGENT, YamlRegistryStore(DEMO_DIR / "registry.yaml"))
    async with Session(tokens["caseworker-app"]) as s:
        listed = await s.tools()
        ok, out = await s.call(AGENT, requests[0])
        _line("after revoke", f"listed={listed} error={out.get('error')}")
        expect(
            "revoked agent disappears and calls are denied", listed == [] and out.get("error") == "AGENT_NOT_APPROVED"
        )

    # Evidence for reviewers: outputs, plus the trace and audit files the server wrote.
    SAMPLES.mkdir(exist_ok=True)
    (SAMPLES / "recommendations.json").write_text(json.dumps(recommendations, indent=2) + "\n", encoding="utf-8")
    shutil.copy(DEMO_DIR / "traces.jsonl", SAMPLES / "traces.jsonl")
    shutil.copy(DEMO_DIR / "audit.jsonl", SAMPLES / "audit.jsonl")

    passed = sum(ok for _, ok in outcomes)
    print(f"\nDemo checks: {passed}/{len(outcomes)} passed")
    print(f"Traces: {DEMO_DIR / 'traces.jsonl'}\nAudit:  {DEMO_DIR / 'audit.jsonl'}\nCopied to samples/ for review.")
    return 0 if passed == len(outcomes) else 1
