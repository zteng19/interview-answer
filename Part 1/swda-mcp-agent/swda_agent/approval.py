"""Agent approval workflow (Part 1B section 3.10, component 10a).

PoC version of the Step Functions workflow:
  submit  -> automated checks -> status "submitted"   (or "draft" with the failed checks)
  approve -> one record per role; the owner cannot approve; one person holds one role
           -> status "approved" once product_owner, security and data_owner have all approved
  revoke  -> kill switch: status "revoked"; the gateway stops serving within the cache TTL
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from .agent import AGENT_IMPLEMENTATIONS
from .models import (
    CLASSIFICATION_RANK,
    REQUIRED_APPROVER_ROLES,
    AgentManifest,
    AgentRecord,
    Approval,
    Registry,
)
from .ports import Clock, RegistryStore
from .tools import TOOL_CATALOGUE

MAX_APPROVAL_DAYS = 180


class ApprovalError(Exception):
    pass


def automated_checks(manifest: AgentManifest, evidence_dir: Path, clock: Clock) -> dict[str, bool]:
    tools = [TOOL_CATALOGUE.get(t) for t in manifest.requested_tools]
    evidence_file = evidence_dir / f"{manifest.agent_id}-{manifest.version}.json"
    evaluation_passed = False
    if evidence_file.exists():
        evidence = json.loads(evidence_file.read_text(encoding="utf-8"))
        evaluation_passed = bool(evidence.get("passed")) and evidence.get("agent_version") == manifest.version
    impl = AGENT_IMPLEMENTATIONS.get(manifest.agent_id)
    return {
        "tools_exist_in_catalogue": all(t is not None for t in tools),
        "tools_are_read_only": all(t is not None and t.read_only for t in tools),
        "tools_within_data_classification": all(
            t is not None
            and CLASSIFICATION_RANK[t.data_classification] <= CLASSIFICATION_RANK[manifest.data_classification]
            for t in tools
        ),
        "tools_within_code_allowlist": impl is not None and set(manifest.requested_tools) <= impl.ALLOWED_TOOLS,
        "expiry_within_180_days": manifest.expires_on <= clock.now().date() + timedelta(days=MAX_APPROVAL_DAYS),
        "evaluation_passed": evaluation_passed,
    }


def _replace(registry: Registry, record: AgentRecord) -> Registry:
    others = [a for a in registry.agents if (a.agent_id, a.version) != (record.agent_id, record.version)]
    return registry.model_copy(update={"agents": [*others, record]})


def _find(registry: Registry, agent_id: str, version: str) -> AgentRecord:
    for a in registry.agents:
        if (a.agent_id, a.version) == (agent_id, version):
            return a
    raise ApprovalError(f"No registry record for {agent_id} v{version}")


def submit(manifest: AgentManifest, store: RegistryStore, evidence_dir: Path, clock: Clock) -> AgentRecord:
    registry = store.load()
    if any(
        (a.agent_id, a.version) == (manifest.agent_id, manifest.version) and a.status == "approved"
        for a in registry.agents
    ):
        raise ApprovalError("This version is already approved; submit a new version instead")
    checks = automated_checks(manifest, evidence_dir, clock)
    record = AgentRecord(
        agent_id=manifest.agent_id,
        version=manifest.version,
        status="submitted" if all(checks.values()) else "draft",
        owner=manifest.owner,
        purpose=manifest.purpose,
        intended_users=manifest.intended_users,
        data_classification=manifest.data_classification,
        classifier_version=manifest.classifier_version,
        prompt_version=manifest.prompt_version,
        image_digest=manifest.image_digest,
        allowed_tools=manifest.requested_tools,
        allowed_clients=manifest.allowed_clients,
        expires_on=manifest.expires_on,
        evidence=manifest.evidence,
        automated_checks=checks,
    )
    store.save(_replace(registry, record))
    if record.status == "draft":
        failed = sorted(k for k, ok in checks.items() if not ok)
        raise ApprovalError(f"Automated checks failed: {', '.join(failed)}")
    return record


def approve(agent_id: str, version: str, approver: str, role: str, store: RegistryStore, clock: Clock) -> AgentRecord:
    if role not in REQUIRED_APPROVER_ROLES:
        raise ApprovalError(f"Unknown role '{role}'")
    registry = store.load()
    record = _find(registry, agent_id, version)
    if record.status != "submitted":
        raise ApprovalError(f"Cannot approve an agent in status '{record.status}'")
    if approver == record.owner:
        raise ApprovalError("Separation of duties: the owner cannot approve their own agent")
    if any(a.approver == approver for a in record.approvals):
        raise ApprovalError("Separation of duties: one person can approve in only one role")
    if any(a.role == role for a in record.approvals):
        raise ApprovalError(f"Role '{role}' has already approved")
    approvals = [*record.approvals, Approval(approver=approver, role=role, approved_on=clock.now().date())]
    complete = {a.role for a in approvals} >= REQUIRED_APPROVER_ROLES
    record = record.model_copy(update={"approvals": approvals, "status": "approved" if complete else "submitted"})
    store.save(_replace(registry, record))
    return record


def revoke(agent_id: str, store: RegistryStore) -> list[AgentRecord]:
    registry = store.load()
    changed = [a.model_copy(update={"status": "revoked"}) for a in registry.agents if a.agent_id == agent_id]
    if not changed:
        raise ApprovalError(f"No agent '{agent_id}'")
    for record in changed:
        registry = _replace(registry, record)
    store.save(registry)
    return changed
