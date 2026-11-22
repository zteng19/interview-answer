"""Point 9 (human approval) and point 10 (traces)."""
import json

import pytest

from collusion_sim.approval import ApprovalError, ApprovalService, citations
from collusion_sim.runner import run_scenario
from collusion_sim.trace import verify_manifest, verify_trace


@pytest.fixture
def queued(out):
    res = run_scenario("disc_s3", out, built_by="pipeline")
    card = res["cards"][0]
    return ApprovalService(out, ["alice", "bob"]), card, res


def test_only_allowlisted_reviewers(queued):
    svc, card, _ = queued
    with pytest.raises(ApprovalError):
        svc.decide(card.pattern_id, "approve_for_testing", "ok", citations(card), as_reviewer="mallory")


def test_override_is_logged_as_weakened_control(queued):
    svc, card, _ = queued
    d = svc.decide(card.pattern_id, "approve_for_testing", "checked", citations(card), as_reviewer="alice")
    assert d.weakened_control is True


def test_checklist_must_cover_every_citation(queued):
    svc, card, _ = queued
    with pytest.raises(ApprovalError, match="checklist"):
        svc.decide(card.pattern_id, "approve_for_testing", "ok", ["S3"], as_reviewer="alice")


def test_rationale_is_mandatory(queued):
    svc, card, _ = queued
    with pytest.raises(ApprovalError, match="rationale"):
        svc.decide(card.pattern_id, "approve_for_testing", " ", citations(card), as_reviewer="alice")


def test_approval_is_hash_bound(queued, out):
    svc, card, _ = queued
    svc.decide(card.pattern_id, "approve_for_testing", "checked", citations(card), as_reviewer="alice")
    assert svc.verify(card.pattern_id)
    path = out / "queue" / f"{card.pattern_id}.json"
    data = json.loads(path.read_text())
    data["steps"][0]["text"] = "edited after approval"
    path.write_text(json.dumps(data))
    assert not svc.verify(card.pattern_id)


def test_approved_card_enters_register_as_hypothesis(queued, out):
    svc, card, _ = queued
    svc.decide(card.pattern_id, "approve_for_testing", "checked", citations(card), as_reviewer="alice")
    entry = json.loads((out / "register.jsonl").read_text().splitlines()[0])
    assert entry["status"] == "approved_for_testing" and "Hypothesis" in entry["note"]


def test_new_patterns_need_two_reviewers(out):
    card = run_scenario("disc_s4", out, built_by="pipeline")["cards"][0]
    svc = ApprovalService(out, ["alice", "bob"])
    svc.decide(card.pattern_id, "approve_for_testing", "ok", citations(card), as_reviewer="alice")
    assert not (out / "register.jsonl").exists()
    with pytest.raises(ApprovalError):
        svc.decide(card.pattern_id, "approve_for_testing", "ok", citations(card), as_reviewer="alice")
    svc.decide(card.pattern_id, "approve_for_testing", "ok", citations(card), as_reviewer="bob")
    assert (out / "register.jsonl").exists()


def test_request_changes_creates_linked_rerun(queued, out):
    svc, card, _ = queued
    svc.decide(card.pattern_id, "request_changes", "flows unclear", citations(card), as_reviewer="alice",
               reviewer_codes=["UNCLEAR_FLOW:flows[0]"])
    req = json.loads((out / "rerun_requests" / f"{card.pattern_id}.json").read_text())
    res = run_scenario(req["scenario_id"], out, parent_pattern_id=req["parent_pattern_id"],
                       reviewer_codes=req["reviewer_codes"], rerun_index=1, built_by="pipeline")
    assert res["cards"][0].parent_pattern_id == card.pattern_id


def test_trace_chain_detects_tampering(queued, out):
    _, _, res = queued
    path = out / "traces" / f"{res['report'].run_id}.jsonl"
    assert verify_trace(path)[0]
    lines = path.read_text().splitlines()
    ev = json.loads(lines[3]); ev["payload"]["tampered"] = True
    lines[3] = json.dumps(ev)
    path.write_text("\n".join(lines) + "\n")
    assert not verify_trace(path)[0]


def test_manifest_head_detects_deleted_runs(out):
    h1 = run_scenario("neg_s5", out, built_by="pipeline")["head_hash"]
    h2 = run_scenario("recall_mo03", out, built_by="pipeline")["head_hash"]
    manifest = out / "traces" / "manifest.jsonl"
    assert verify_manifest(manifest, h2)[0]
    manifest.write_text(manifest.read_text().splitlines()[0] + "\n")     # delete the last run
    assert not verify_manifest(manifest, h2)[0]
    assert h1 != h2


def test_trace_records_sent_and_delivered_and_sanitiser_flags(out):
    res = run_scenario("adv_injected_s2", out, built_by="pipeline")
    ev = [json.loads(l) for l in (out / "traces" / f"{res['report'].run_id}.jsonl").read_text().splitlines()]
    kinds = {e["kind"] for e in ev}
    assert {"message_sent", "message_delivered", "message_blocked", "validation_error"} <= kinds
    assert "S2" in ev[0]["payload"]["sanitiser_flags"]
