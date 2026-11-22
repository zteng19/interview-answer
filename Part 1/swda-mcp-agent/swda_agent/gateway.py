"""Gateway logic: the single enforcement point between MCP clients and agents.

Implements the runtime steps in Part 1B section 3.10:
authenticate -> discover -> authorise -> validate -> guard -> limit -> execute -> audit.
In production this runs as the MCP gateway (10e) in front of the MCP server (10f).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections import defaultdict
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from . import guard
from .agent import AGENT_IMPLEMENTATIONS
from .errors import GatewayError
from .models import AgentRecord, ClientRecord, Recommendation, Registry, ServiceRequest
from .ports import Clock, IdFactory, RegistryStore, TraceSink, uuid_ids
from .tools import ToolBox, ToolInvoker


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Gateway:
    def __init__(
        self,
        *,
        registry_store: RegistryStore,
        toolbox: ToolBox,
        trace: TraceSink,
        audit: TraceSink,
        clock: Clock,
        ids: IdFactory = uuid_ids,
        max_payload_bytes: int = 16384,
        tool_timeout_s: float = 2.0,
        implementations: dict[str, Any] | None = None,
    ) -> None:
        self._store = registry_store
        self._box = toolbox
        self._trace = trace
        self._audit = audit
        self._clock = clock
        self._ids = ids
        self._max_payload = max_payload_bytes
        self._timeout = tool_timeout_s
        self._impls = implementations if implementations is not None else AGENT_IMPLEMENTATIONS
        self._cached: Registry | None = None
        self._loaded_at: datetime | None = None
        self._windows: dict[tuple[str, str], int] = defaultdict(int)

    # ------------------------------------------------------------------ registry cache
    def registry(self) -> Registry:
        """Registry with a short cache. Revocations take effect within cache_ttl_seconds (kill switch)."""
        now = self._clock.now()
        if (
            self._cached is None
            or self._loaded_at is None
            or (now - self._loaded_at).total_seconds() >= self._cached.cache_ttl_seconds
        ):
            self._cached = self._store.load()
            self._loaded_at = now
        return self._cached

    # ------------------------------------------------------------------ 1. authenticate
    def authenticate(self, token: str | None) -> ClientRecord:
        if token:
            digest = hash_token(token)
            for client in self.registry().clients:
                if hmac.compare_digest(client.token_sha256, digest) and client.status == "active":
                    return client
        raise GatewayError("UNAUTHENTICATED", "Missing, invalid or disabled client credential")

    # ------------------------------------------------------------------ 2. discover
    def _agent_problem(self, client: ClientRecord, agent: AgentRecord) -> str | None:
        """Return why this client may not use this agent, or None if it may."""
        if agent.status != "approved":
            return "AGENT_NOT_APPROVED"
        if agent.expires_on < self._clock.now().date():
            return "AGENT_EXPIRED"
        impl = self._impls.get(agent.agent_id)
        if impl is None:
            return "AGENT_NOT_FOUND"
        if impl.VERSION != agent.version:
            return "VERSION_MISMATCH"  # the running code is not the version that was approved
        if client.client_id not in agent.allowed_clients:
            return "CLIENT_NOT_PERMITTED"
        return None

    def discover(self, client: ClientRecord) -> list[AgentRecord]:
        return [a for a in self.registry().agents if self._agent_problem(client, a) is None]

    def _resolve(self, client: ClientRecord, agent_id: str) -> AgentRecord:
        """Pick the usable registry record for this agent; several versions may be registered."""
        records = [a for a in self.registry().agents if a.agent_id == agent_id]
        if not records:
            raise GatewayError("AGENT_NOT_FOUND", f"No agent '{agent_id}'")
        problems = [(self._agent_problem(client, a), a) for a in records]
        for problem, record in problems:
            if problem is None:
                return record
        # Report the problem of the record matching the running code, if there is one.
        impl = self._impls.get(agent_id)
        problem, record = next(
            ((p, r) for p, r in problems if impl is not None and r.version == impl.VERSION), problems[-1]
        )
        assert problem is not None
        raise GatewayError(problem, f"Agent '{agent_id}' v{record.version} is not available ({problem})")

    def describe(self, agent: AgentRecord) -> str:
        return str(self._impls[agent.agent_id].DESCRIPTION)

    # ------------------------------------------------------------------ 3-8. invoke
    def invoke(self, token: str | None, agent_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        corr = self._ids()
        started = time.perf_counter()
        audit: dict[str, Any] = {
            "event": "agent_call",
            "ts": self._clock.now().isoformat(),
            "correlation_id": corr,
            "client_id": None,
            "agent_id": agent_id,
            "agent_version": None,
            "decision": "deny",
            "error": None,
            "tools": [],
            "flags": [],
        }
        try:
            client = self.authenticate(token)
            audit["client_id"] = client.client_id

            # 3. authorise: caller x agent (tool-level checks happen inside the invoker)
            agent = self._resolve(client, agent_id)
            audit["agent_version"] = agent.version

            # 6. limit (before doing any work)
            self._rate_limit(client)

            # 4. validate input
            size = len(json.dumps(payload, default=str).encode("utf-8"))
            if size > self._max_payload:
                raise GatewayError("PAYLOAD_TOO_LARGE", f"Payload is {size} bytes; limit is {self._max_payload}")
            try:
                req = ServiceRequest.model_validate(payload)
            except ValidationError as e:
                fields = sorted({".".join(str(p) for p in err["loc"]) for err in e.errors()})
                raise GatewayError("INVALID_REQUEST", f"Invalid fields: {', '.join(fields)}") from e

            # 5. guard: flag, never obey. Only masked, truncated text reaches the trace.
            self._trace.emit(
                {
                    "event": "request_received",
                    "correlation_id": corr,
                    "request_id": req.request_id,
                    "channel": req.channel,
                    "text_sha256": guard.text_fingerprint(req.text),
                    "text_len": len(req.text),
                    "text_excerpt_masked": guard.safe_excerpt(req.text),
                    "flags": guard.scan(req.text),
                }
            )

            # 7. execute with the tools both the code and the approval allow
            impl = self._impls[agent.agent_id]()
            tools = ToolInvoker(
                toolbox=self._box,
                agent_allowlist=impl.ALLOWED_TOOLS,
                registry_allowlist=frozenset(agent.allowed_tools),
                trace=self._trace,
                correlation_id=corr,
                timeout_s=self._timeout,
            )
            try:
                result = impl.run(req, tools, corr)
            finally:
                audit["tools"] = tools.calls
            try:
                out = Recommendation.model_validate(result.model_dump())
            except ValidationError as e:
                raise GatewayError("INVALID_AGENT_OUTPUT", "Agent output failed schema") from e

            audit.update(decision="allow", flags=out.flags, next_step=out.next_step, category=out.category)
            return out.model_dump(mode="json")
        except GatewayError as e:
            audit["error"] = e.code
            raise
        except Exception as e:  # never leak internals to the caller
            audit["error"] = "INTERNAL_ERROR"
            raise GatewayError("INTERNAL_ERROR", "Unexpected error; see trace") from e
        finally:
            # 8. audit: exactly one record per call, allowed or denied. No request text.
            audit["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            self._audit.emit(audit)

    def _rate_limit(self, client: ClientRecord) -> None:
        window = self._clock.now().strftime("%Y%m%d%H%M")
        key = (client.client_id, window)
        for stale in [k for k in self._windows if k[0] == client.client_id and k != key]:
            del self._windows[stale]
        self._windows[key] += 1
        if self._windows[key] > client.rate_limit_per_minute:
            raise GatewayError("RATE_LIMITED", f"Limit of {client.rate_limit_per_minute} calls per minute exceeded")
