"""Point 2 (commit rule), point 8 (schemas, retries, limits) and every final state."""
from conftest import P, turn

from collusion_sim.models import FinalState


def solo_script(objective="OBJ-2"):
    return {"agents": {"trainee": {"R2": [turn(
        P(proposal_id="P1", objective_id=objective, action="enrol", text="Trainee enrols", benefit="Credits paid out"),
        P(proposal_id="P1", action="submit_credit_claim", text="Trainee submits a credit claim"))]}}}


def test_solo_commit_produces_single_actor_card(run_inline):
    res = run_inline(solo_script())
    assert res["final_state"] == FinalState.CARD_ISSUED
    assert res["cards"][0].card_type == "single-actor"


def test_proposer_can_withdraw_by_declining(run_inline):
    s = solo_script()
    s["agents"]["trainee"]["R3"] = [turn({"tool": "decline", "args": {"proposal_id": "P1"}})]
    assert run_inline(s)["final_state"] == FinalState.NO_SCHEME


def test_knowing_step_owner_must_accept(run_inline):
    script = {"agents": {
        "provider": {"R2": [turn(
            P(proposal_id="P1", objective_id="OBJ-1", action="record_attendance", text="a", benefit="fees"),
            P(proposal_id="P1", action="submit_course_claim", text="b"),
            {"tool": "send_message", "args": {"channel": "private", "to": ["trainee"], "proposal_ref": "P1", "body": "P1"}})]},
        "trainee": {"R2": [turn(P(proposal_id="P1", action="enrol", text="c", benefit="cash"),
                                P(proposal_id="P1", action="confirm_attendance_without_attending", text="d"))],
                    "R3": [turn({"tool": "decline", "args": {"proposal_id": "P1"}})]}}}
    assert run_inline(script)["final_state"] == FinalState.NO_SCHEME


def test_each_valid_commit_gets_its_own_card(run_inline):
    script = {"agents": {
        "trainee": {"R2": [turn(
            P(proposal_id="T1", objective_id="OBJ-2", action="enrol", text="Trainee enrols", benefit="payout"),
            P(proposal_id="T1", action="submit_credit_claim", text="Trainee submits a credit claim"))]},
        "provider": {"R2": [turn(
            P(proposal_id="P1", objective_id="OBJ-4", action="record_results_regardless_of_performance",
              text="Provider records passing results regardless of performance", benefit="funding"),
            P(proposal_id="P1", action="record_attendance", text="Provider records attendance"),
            P(proposal_id="P1", action="submit_course_claim", text="Provider submits the course claim"),
            P(proposal_id="P1", actor="trainee", action="enrol", text="Trainee enrols"),
            P(proposal_id="P1", actor="trainee", action="attend", text="Trainee attends"))]}}}
    res = run_inline(script)
    assert sorted(c.pattern_id for c in res["cards"]) == ["PC-inline-P1", "PC-inline-T1"]


def test_decline_all_is_not_an_abstention(run_inline):
    script = {"agents": {r: {"R3": [turn({"tool": "decline_all", "args": {}})]} for r in ("trainee", "provider", "employer")}}
    assert run_inline(script)["final_state"] == FinalState.NO_SCHEME


def test_invalid_output_retried_then_abstain_fails_run(run_inline):
    bad = {"_raw": "not json"}
    res = run_inline({"agents": {"trainee": {"R1": [bad, bad, bad]}}})
    assert res["final_state"] == FinalState.FAILED
    assert res["report"].counts["retries"] == 3
    assert "ABSTAINED:trainee" in res["report"].reason_codes


def test_status_field_in_agent_output_is_rejected(run_inline):
    res = run_inline({"agents": {"trainee": {"R1": [{"status": "approved", "tool_calls": []}, turn()]}}})
    assert res["report"].counts["retries"] == 1


def test_bad_tool_arguments_fail_schema(run_inline):
    res = run_inline({"agents": {"trainee": {"R1": [turn(P(action="enrol")), turn()]}}})   # missing text
    assert res["report"].counts["retries"] == 1


def test_turn_timeout_on_injected_clock(run_inline):
    res = run_inline({"agents": {"trainee": {"R1": [{"_latency": 45, "tool_calls": []}]}}})
    assert res["final_state"] == FinalState.BUDGET_EXCEEDED
    assert "TURN_TIMEOUT" in res["report"].reason_codes


def test_token_budget(run_inline, cfg):
    cfg.run["limits"]["token_budget"] = 30
    res = run_inline(solo_script(), cfg=cfg)
    assert res["final_state"] == FinalState.BUDGET_EXCEEDED
    assert "TOKEN_BUDGET" in res["report"].reason_codes


def test_realism_failure_rejects_in_pipeline(run_inline):
    s = solo_script()
    s["agents"]["trainee"]["R2"][0]["tool_calls"][0]["args"].pop("benefit")   # colluder with no benefit
    res = run_inline(s)
    assert res["final_state"] == FinalState.REJECTED_IN_PIPELINE
    assert "REALISM_NO_BENEFIT:trainee" in res["report"].reason_codes


def test_revision_not_recommitted_is_rejected(run_inline):
    s = solo_script()
    s["agents"]["trainee"]["R2"][0]["tool_calls"][1]["args"]["text"] = "Trainee uses a claim template"
    s["agents"]["trainee"]["R4"] = [turn({"tool": "decline", "args": {"proposal_id": "P1"}})]
    res = run_inline(s)
    assert res["final_state"] == FinalState.REJECTED_IN_PIPELINE
    assert res["report"].rounds_used == 4


def test_revision_round_fixes_card(run_inline):
    s = solo_script()
    s["agents"]["trainee"]["R2"][0]["tool_calls"][1]["args"]["text"] = "Trainee uses a claim template"
    s["agents"]["trainee"]["R4"] = [turn(P(proposal_id="P1", revises="P1.2", action="submit_credit_claim",
                                            text="Trainee submits a credit claim without attending"))]
    res = run_inline(s)
    assert res["final_state"] == FinalState.CARD_ISSUED
    assert res["report"].rounds_used == 4


def test_run_report_is_metadata_only():
    import pytest
    from collusion_sim.models import RunReport
    with pytest.raises(Exception):
        RunReport(run_id="r", scenario_id="s", final_state="NO_SCHEME", rounds_used=3, counts={},
                  reason_codes=["Provider says: record fake attendance"], card_ids=[], trace_ref="t")
