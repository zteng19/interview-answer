"""Registry enforcement: discovery filter, approval status, expiry, version pinning, kill switch, limits."""

from __future__ import annotations

import pytest
from conftest import AGENT, CASEWORKER, DISABLED, REPORTING

from swda_agent.agent import ServiceRecommendationAgent
from swda_agent.approval import revoke
from swda_agent.errors import GatewayError


def _ids(gateway, token):
    return [a.agent_id for a in gateway.discover(gateway.authenticate(token))]


def test_discovery_lists_only_approved_unexpired_permitted_agents(gateway):
    # The registry also holds a "submitted" agent and an expired one; neither is listed.
    assert _ids(gateway, CASEWORKER) == [AGENT]


def test_client_not_permitted_sees_nothing_and_cannot_call(gateway, request_payload):
    assert _ids(gateway, REPORTING) == []
    with pytest.raises(GatewayError) as e:
        gateway.invoke(REPORTING, AGENT, request_payload)
    assert e.value.code == "CLIENT_NOT_PERMITTED"


@pytest.mark.parametrize("token", ["", None, "guess", DISABLED])
def test_invalid_or_disabled_credentials_are_rejected(gateway, request_payload, token):
    with pytest.raises(GatewayError) as e:
        gateway.invoke(token, AGENT, request_payload)
    assert e.value.code == "UNAUTHENTICATED"


def test_unapproved_agent_cannot_be_called(gateway, request_payload):
    with pytest.raises(GatewayError) as e:
        gateway.invoke(CASEWORKER, "case-summary-agent", request_payload)
    assert e.value.code == "AGENT_NOT_APPROVED"


def test_expired_agent_cannot_be_called(gateway, request_payload):
    with pytest.raises(GatewayError) as e:
        gateway.invoke(CASEWORKER, "legacy-triage-agent", request_payload)
    assert e.value.code == "AGENT_EXPIRED"


def test_approval_expires_without_redeployment(gateway, request_payload, clock):
    gateway.invoke(CASEWORKER, AGENT, request_payload)
    clock.advance(200 * 86400)  # past expires_on 2027-05-01
    assert _ids(gateway, CASEWORKER) == []
    with pytest.raises(GatewayError) as e:
        gateway.invoke(CASEWORKER, AGENT, request_payload)
    assert e.value.code == "AGENT_EXPIRED"


def test_running_code_must_match_approved_version(gateway, request_payload, monkeypatch):
    # Simulates a modified agent deployed without approval.
    monkeypatch.setattr(ServiceRecommendationAgent, "VERSION", "1.0.1")
    assert _ids(gateway, CASEWORKER) == []
    with pytest.raises(GatewayError) as e:
        gateway.invoke(CASEWORKER, AGENT, request_payload)
    assert e.value.code == "VERSION_MISMATCH"


def test_kill_switch_takes_effect_within_cache_ttl(gateway, store, request_payload, clock):
    gateway.invoke(CASEWORKER, AGENT, request_payload)  # loads registry into the cache
    revoke(AGENT, store)
    clock.advance(30)
    gateway.invoke(CASEWORKER, AGENT, request_payload)  # still cached: allowed (documented <= 60 s window)
    clock.advance(30)
    with pytest.raises(GatewayError) as e:
        gateway.invoke(CASEWORKER, AGENT, request_payload)
    assert e.value.code == "AGENT_NOT_APPROVED"
    assert _ids(gateway, CASEWORKER) == []


def test_rate_limit_per_client_per_minute(gateway, request_payload, clock):
    for _ in range(30):
        gateway.invoke(CASEWORKER, AGENT, request_payload)
    with pytest.raises(GatewayError) as e:
        gateway.invoke(CASEWORKER, AGENT, request_payload)
    assert e.value.code == "RATE_LIMITED"
    clock.advance(60)
    gateway.invoke(CASEWORKER, AGENT, request_payload)  # new window
