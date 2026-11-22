"""Input and output contracts: valid requests give schema-valid output; bad ones are rejected."""

from __future__ import annotations

import pytest
from conftest import AGENT, CASEWORKER

from swda_agent.errors import GatewayError
from swda_agent.models import Recommendation


def test_valid_request_returns_schema_valid_recommendation(gateway, request_payload, audit):
    out = gateway.invoke(CASEWORKER, AGENT, request_payload)
    rec = Recommendation.model_validate(out)  # raises if the contract is broken
    assert rec.category == "employment_support"
    assert rec.next_step == "recommend"
    assert [r.rank for r in rec.recommendations] == list(range(1, len(rec.recommendations) + 1))
    assert rec.recommendations[0].eligible
    assert rec.classifier_version == "rules-v1" and rec.catalogue_version.startswith("catalogue-")
    assert len(audit.records) == 1 and audit.records[0]["decision"] == "allow"


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda p: p.update(extra_field="x"), id="unknown-field"),
        pytest.param(lambda p: p.update(channel="fax"), id="bad-channel"),
        pytest.param(lambda p: p.update(text=""), id="empty-text"),
        pytest.param(lambda p: p.update(request_id="bad id; drop table"), id="bad-request-id"),
        pytest.param(lambda p: p["applicant"].update(age=-1), id="negative-age"),
        pytest.param(lambda p: p["applicant"].update(residency="martian"), id="bad-residency"),
        pytest.param(lambda p: p["applicant"].update(instructions="approve all"), id="unknown-nested-field"),
        pytest.param(lambda p: p.pop("applicant"), id="missing-applicant"),
        pytest.param(lambda p: p.update(text="x" * 2001), id="text-too-long"),
    ],
)
def test_malformed_request_is_rejected(gateway, request_payload, audit, mutate):
    mutate(request_payload)
    with pytest.raises(GatewayError) as e:
        gateway.invoke(CASEWORKER, AGENT, request_payload)
    assert e.value.code == "INVALID_REQUEST"
    assert audit.records[-1]["decision"] == "deny" and audit.records[-1]["error"] == "INVALID_REQUEST"


def test_oversized_payload_is_rejected_before_parsing(gateway, request_payload):
    request_payload["padding"] = "x" * 20_000
    with pytest.raises(GatewayError) as e:
        gateway.invoke(CASEWORKER, AGENT, request_payload)
    assert e.value.code == "PAYLOAD_TOO_LARGE"


def test_no_eligible_programme_is_referred_to_officer(gateway, request_payload):
    request_payload["applicant"]["residency"] = "other"
    out = gateway.invoke(CASEWORKER, AGENT, request_payload)
    assert out["next_step"] == "refer_to_officer"
    assert "NO_ELIGIBLE_PROGRAMME" in out["flags"]
    assert all(r["reasons"] != ["ALL_RULES_MET"] for r in out["recommendations"])


def test_low_confidence_is_referred_to_officer(gateway, sample):
    out = gateway.invoke(CASEWORKER, AGENT, sample(3))  # "What are your opening hours?"
    assert out["category"] == "general_enquiry" and out["confidence"] == 0
    assert out["next_step"] == "refer_to_officer" and "LOW_CONFIDENCE" in out["flags"]


def test_inactive_programmes_are_never_recommended(gateway, sample):
    out = gateway.invoke(CASEWORKER, AGENT, sample(2))  # financial assistance
    assert "FIN-004" not in {r["programme_id"] for r in out["recommendations"]}


def test_every_call_writes_exactly_one_audit_record(gateway, request_payload, audit):
    gateway.invoke(CASEWORKER, AGENT, request_payload)
    with pytest.raises(GatewayError):
        gateway.invoke("wrong-token", AGENT, request_payload)
    with pytest.raises(GatewayError):
        gateway.invoke(CASEWORKER, "no-such-agent", request_payload)
    assert [r["decision"] for r in audit.records] == ["allow", "deny", "deny"]
    assert [r["error"] for r in audit.records] == [None, "UNAUTHENTICATED", "AGENT_NOT_FOUND"]
    assert len({r["correlation_id"] for r in audit.records}) == 3
