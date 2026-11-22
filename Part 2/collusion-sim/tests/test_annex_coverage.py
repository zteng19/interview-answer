"""Every supplied annex record is exercised by at least one scripted scenario, and every
MO can be produced by the system (not just stored)."""
import pytest

from collusion_sim import planner
from collusion_sim.config import load_yaml
from collusion_sim.runner import run_scenario


def test_every_excerpt_seeds_a_scenario(cfg):
    used = {s for sc in cfg.scenarios["scenarios"] for s in sc["sources"]}
    excerpts = {r["id"] for r in load_yaml("data/annex_b.yaml")["records"]}
    assert excerpts - used == set()


def test_some_mos_are_rediscovered_not_seeded(cfg):
    """MO-04, MO-05 and MO-08 are never given to actors; discovery runs reach them anyway."""
    seeded = {s for sc in cfg.scenarios["scenarios"] for s in sc["sources"] if s.startswith("MO-")}
    assert {"MO-04", "MO-05", "MO-08"}.isdisjoint(seeded)


def test_every_mo_is_annotated(cfg):
    assert set(cfg.annotations["annotations"]) == {f"MO-0{i}" for i in range(1, 9)}


def test_every_mo_appears_in_some_card(cfg, tmp_path):
    seen = set()
    for sc in cfg.scenarios["scenarios"]:
        for card in run_scenario(sc["id"], tmp_path, cfg=cfg, built_by="pipeline")["cards"]:
            seen |= {m.mo_id for m in card.mo_matches}
    assert seen == {f"MO-0{i}" for i in range(1, 9)}


def test_related_party_collapses_to_one_effective_actor(cfg):
    plain = planner.analyse("OBJ-6", cfg)
    rp = planner.analyse("OBJ-6", cfg, related_party=["provider", "employer"])
    assert plain["card_type"] == "collusion" and plain["minimal"] == [["provider", "employer"]]
    assert rp["card_type"] == "single-actor" and rp["effective_minimal"] == [["employer+provider"]]


def test_related_party_does_not_affect_unrelated_actors(cfg):
    rp = planner.analyse("OBJ-1", cfg, related_party=["provider", "employer"])
    assert rp["card_type"] == "collusion"           # trainee + provider still two effective actors


@pytest.mark.parametrize("objective,expected", [("OBJ-5", [["employer"]]), ("OBJ-7", [["provider"]])])
def test_new_objectives(cfg, objective, expected):
    assert planner.analyse(objective, cfg)["minimal"] == expected


def test_audit_needs_false_records(cfg):
    cfg.actions.pop("present_false_records_at_audit")
    assert planner.analyse("OBJ-7", cfg)["minimal"] == []
