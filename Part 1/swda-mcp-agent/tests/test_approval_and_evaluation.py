"""Approval workflow (automated checks, separation of duties) and the evaluation release gate."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from conftest import AGENT, CASEWORKER

from swda_agent.approval import ApprovalError, approve, submit
from swda_agent.config import ROOT
from swda_agent.errors import GatewayError
from swda_agent.evaluate import evaluate
from swda_agent.models import AgentManifest
from swda_agent.tools import ClassifierRules, ToolBox

MANIFESTS = ROOT / "manifests"


def _manifest(name: str) -> AgentManifest:
    with (MANIFESTS / name).open(encoding="utf-8") as f:
        return AgentManifest.model_validate(yaml.safe_load(f))


@pytest.fixture
def evidence_dir(tmp_path, toolbox) -> Path:
    d = tmp_path / "evidence"
    d.mkdir()
    report = evaluate(toolbox, ROOT / "data/eval_requests.jsonl", ROOT / "data/injection_suite.jsonl")
    (d / f"{AGENT}-1.0.0.json").write_text(json.dumps(report), encoding="utf-8")
    return d


@pytest.fixture
def empty_store(store):
    reg = store.load()
    store.save(reg.model_copy(update={"agents": [a for a in reg.agents if a.agent_id != AGENT]}))
    return store


def test_full_approval_flow_makes_agent_discoverable(empty_store, evidence_dir, clock, gateway, request_payload):
    with pytest.raises(GatewayError, match="No agent"):
        gateway.invoke(CASEWORKER, AGENT, request_payload)  # not registered yet

    rec = submit(_manifest("service-recommendation-agent-1.0.0.yaml"), empty_store, evidence_dir, clock)
    assert rec.status == "submitted" and all(rec.automated_checks.values())

    approve(AGENT, "1.0.0", "po.lim", "product_owner", empty_store, clock)
    approve(AGENT, "1.0.0", "sec.tan", "security", empty_store, clock)
    clock.advance(60)
    assert gateway.discover(gateway.authenticate(CASEWORKER)) == []  # 2 of 3 roles: still not live

    rec = approve(AGENT, "1.0.0", "data.ng", "data_owner", empty_store, clock)
    assert rec.status == "approved"
    clock.advance(60)
    assert [a.agent_id for a in gateway.discover(gateway.authenticate(CASEWORKER))] == [AGENT]
    gateway.invoke(CASEWORKER, AGENT, request_payload)


def test_owner_cannot_approve_own_agent(empty_store, evidence_dir, clock):
    submit(_manifest("service-recommendation-agent-1.0.0.yaml"), empty_store, evidence_dir, clock)
    with pytest.raises(ApprovalError, match="owner cannot approve"):
        approve(AGENT, "1.0.0", "dev.koh", "product_owner", empty_store, clock)


def test_one_person_cannot_hold_two_roles(empty_store, evidence_dir, clock):
    submit(_manifest("service-recommendation-agent-1.0.0.yaml"), empty_store, evidence_dir, clock)
    approve(AGENT, "1.0.0", "po.lim", "product_owner", empty_store, clock)
    with pytest.raises(ApprovalError, match="only one role"):
        approve(AGENT, "1.0.0", "po.lim", "security", empty_store, clock)


def test_over_privileged_manifest_fails_automated_checks(empty_store, evidence_dir, clock):
    with pytest.raises(ApprovalError) as e:
        submit(_manifest("over-privileged-agent-0.1.0.yaml"), empty_store, evidence_dir, clock)
    for check in (
        "tools_are_read_only",
        "tools_within_data_classification",
        "tools_within_code_allowlist",
        "expiry_within_180_days",
        "evaluation_passed",
    ):
        assert check in str(e.value)
    draft = next(a for a in empty_store.load().agents if a.version == "0.1.0")
    assert draft.status == "draft"
    with pytest.raises(ApprovalError, match="status 'draft'"):
        approve(AGENT, "0.1.0", "po.lim", "product_owner", empty_store, clock)


def test_submission_without_evaluation_evidence_is_rejected(empty_store, tmp_path, clock):
    with pytest.raises(ApprovalError, match="evaluation_passed"):
        submit(_manifest("service-recommendation-agent-1.0.0.yaml"), empty_store, tmp_path, clock)


def test_evaluation_meets_release_thresholds(toolbox):
    report = evaluate(toolbox, ROOT / "data/eval_requests.jsonl", ROOT / "data/injection_suite.jsonl")
    assert report["passed"], report["failures"]
    assert report["counts"] == {"eval_cases": 20, "injection_cases": 12}


def test_evaluation_gate_fails_when_the_classifier_regresses(tmp_path, toolbox):
    rules = yaml.safe_load((ROOT / "config/classifier_rules.yaml").read_text(encoding="utf-8"))
    del rules["categories"]["eldercare_support"]  # a "bad release" that drops a category
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump(rules), encoding="utf-8")
    broken = ToolBox(ClassifierRules.load(path), toolbox.catalogue)
    report = evaluate(broken, ROOT / "data/eval_requests.jsonl", ROOT / "data/injection_suite.jsonl")
    assert not report["passed"]
    assert report["metrics"]["category_accuracy"] < 0.9


def test_committed_registry_is_valid_and_has_no_plain_tokens():
    text = (ROOT / "config/registry.yaml").read_text(encoding="utf-8")
    assert "dev-caseworker-app-token" not in text
    assert "token_sha256" in text
