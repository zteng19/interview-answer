"""Least privilege: two independent allowlists (agent code and registry approval) guard every tool call."""

from __future__ import annotations

import pytest
from conftest import AGENT, CASEWORKER

from swda_agent.adapters import MemoryTraceSink
from swda_agent.agent import ServiceRecommendationAgent
from swda_agent.errors import GatewayError
from swda_agent.gateway import Gateway
from swda_agent.ports import SequentialIds
from swda_agent.tools import ToolInvoker


def _invoker(toolbox, agent_tools, registry_tools, trace=None):
    return ToolInvoker(
        toolbox=toolbox,
        agent_allowlist=frozenset(agent_tools),
        registry_allowlist=frozenset(registry_tools),
        trace=trace or MemoryTraceSink(),
        correlation_id="t",
        timeout_s=1.0,
    )


def test_tool_outside_agent_code_allowlist_is_denied_even_if_registry_allows(toolbox):
    trace = MemoryTraceSink()
    inv = _invoker(toolbox, {"classify_request"}, {"classify_request", "get_case_history"}, trace)
    with pytest.raises(GatewayError) as e:
        inv.invoke("get_case_history", {"case_id": "C1"})
    assert e.value.code == "TOOL_DENIED"
    assert trace.records[-1] == {
        "event": "tool_denied",
        "correlation_id": "t",
        "tool": "get_case_history",
        "reason": "not_in_agent_allowlist",
    }


def test_tool_outside_registry_approval_is_denied_even_if_code_allows(toolbox):
    inv = _invoker(toolbox, {"classify_request", "update_case_status"}, {"classify_request"})
    with pytest.raises(GatewayError, match="not_in_registry_allowlist"):
        inv.invoke("update_case_status", {"case_id": "C1"})


def test_unknown_tool_is_denied(toolbox):
    inv = _invoker(toolbox, {"rm_rf"}, {"rm_rf"})
    with pytest.raises(GatewayError, match="unknown_tool"):
        inv.invoke("rm_rf", {})


def test_tool_inputs_are_schema_checked(toolbox):
    inv = _invoker(toolbox, {"lookup_programmes"}, {"lookup_programmes"})
    with pytest.raises(GatewayError) as e:
        inv.invoke("lookup_programmes", {"category": "'; DROP TABLE programmes; --"})
    assert e.value.code == "INVALID_TOOL_INPUT"


class _CompromisedAgent(ServiceRecommendationAgent):
    """An agent whose code has been changed to reach for case history it was never approved for."""

    ALLOWED_TOOLS = frozenset({*ServiceRecommendationAgent.ALLOWED_TOOLS, "get_case_history"})

    def run(self, req, tools, correlation_id):  # type: ignore[no-untyped-def]
        tools.invoke("get_case_history", {"case_id": "C-1"})
        return super().run(req, tools, correlation_id)


def test_registry_policy_stops_an_agent_whose_code_overreaches(store, toolbox, clock, request_payload):
    trace, audit = MemoryTraceSink(), MemoryTraceSink()
    gw = Gateway(
        registry_store=store,
        toolbox=toolbox,
        trace=trace,
        audit=audit,
        clock=clock,
        ids=SequentialIds(),
        implementations={AGENT: _CompromisedAgent},
    )
    with pytest.raises(GatewayError) as e:
        gw.invoke(CASEWORKER, AGENT, request_payload)
    assert e.value.code == "TOOL_DENIED"
    assert audit.records[-1]["error"] == "TOOL_DENIED" and audit.records[-1]["tools"] == []
    assert any(r.get("reason") == "not_in_registry_allowlist" for r in trace.records)


def test_narrowing_the_registry_grant_takes_effect(gateway, store, request_payload, clock):
    reg = store.load()
    agents = [
        a.model_copy(update={"allowed_tools": ["classify_request", "lookup_programmes"]}) if a.agent_id == AGENT else a
        for a in reg.agents
    ]
    store.save(reg.model_copy(update={"agents": agents}))
    clock.advance(60)
    with pytest.raises(GatewayError) as e:
        gateway.invoke(CASEWORKER, AGENT, request_payload)
    assert e.value.code == "TOOL_DENIED"


def test_normal_call_uses_only_the_three_approved_tools(gateway, request_payload, audit):
    gateway.invoke(CASEWORKER, AGENT, request_payload)
    assert set(audit.records[-1]["tools"]) == {"classify_request", "lookup_programmes", "check_eligibility"}
