"""Point 5 (minimal actor sets) and the realism checks."""
from types import SimpleNamespace

from collusion_sim import planner, realism
from collusion_sim.models import Contribution, FlowIn, Proposal


def test_collusion_needed_when_checks_need_two_actors(cfg):
    assert planner.analyse("OBJ-1", cfg)["minimal"] == [["trainee", "provider"]]


def test_single_actor_credit_claim(cfg):
    a = planner.analyse("OBJ-2", cfg)
    assert a["minimal"] == [["trainee"]] and a["card_type"] == "single-actor"


def test_sponsor_kickback_needs_provider_and_employer_with_unwitting_trainee(cfg):
    assert planner.analyse("OBJ-3", cfg)["minimal"] == [["provider", "employer"]]


def test_provider_alone_for_results_scheme(cfg):
    assert planner.analyse("OBJ-4", cfg)["minimal"] == [["provider"]]


def test_all_seven_subsets_tested(cfg):
    assert planner.analyse("OBJ-1", cfg)["subsets_tested"] == 7


def test_without_checks_collusion_would_collapse(cfg):
    """Shows why SWDA checks are modelled: remove the attendance check and the provider
    could complete the objective alone."""
    cfg.world["checks"]["CHK-ATTENDANCE"]["requires"] = ["attendance_recorded"]
    assert planner.analyse("OBJ-1", cfg)["minimal"] == [["provider"]]


def _c(i, actor, action, added_by=None, benefit=None, flows=(), unwitting=False):
    return Contribution(id=f"P1.{i}", proposal_id="P1", added_by=added_by or actor, actor=actor, action=action,
                        text=action, benefit=benefit, flows=list(flows), unwitting=unwitting, trace_ref=i)


def test_preconditions_must_hold_in_some_order(cfg):
    p = Proposal(id="P1", proposer="provider", objective_id="OBJ-4",
                 contributions=[_c(1, "provider", "submit_course_claim", benefit="fees")])
    r = realism.check(p, {"provider"}, cfg)
    assert not r["passed"] and any(c.startswith("REALISM_PRECONDITION_UNMET") for c in r["codes"])


def test_roles_and_victim_flag(cfg):
    flow = FlowIn(**{"from": "trainee", "to": "provider", "type": "money", "description": "fee"})
    p = Proposal(id="P1", proposer="provider", objective_id="OBJ-4", contributions=[
        _c(1, "trainee", "enrol", added_by="provider", unwitting=True, flows=[flow]),
        _c(2, "trainee", "attend", added_by="provider", unwitting=True),
        _c(3, "provider", "record_attendance"),
        _c(4, "provider", "submit_course_claim"),
        _c(5, "provider", "record_results_regardless_of_performance", benefit="funding")])
    r = realism.check(p, {"provider"}, cfg)
    assert r["passed"]
    roles = {x["actor"]: (x["role"], x["victim"]) for x in r["participants"]}
    assert roles == {"provider": ("colluder", False), "trainee": ("unwitting", True)}


def test_forbidden_effect_fails(cfg):
    p = Proposal(id="P1", proposer="trainee", objective_id="OBJ-2", contributions=[
        _c(1, "trainee", "enrol", benefit="x"), _c(2, "trainee", "attend"), _c(3, "trainee", "submit_credit_claim")])
    r = realism.check(p, {"trainee"}, cfg)
    assert any(c.startswith("REALISM_FORBIDDEN_EFFECT") for c in r["codes"])
