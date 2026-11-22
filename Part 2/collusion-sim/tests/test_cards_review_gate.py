"""Points 4, 6, 7 and 9: card content, origin labels, Review Agent code checks (mutation
tests), MO classification and the safety gate."""
import copy

import pytest

from collusion_sim import gate, review
from collusion_sim.runner import run_scenario
from collusion_sim.sanitiser import SourceStore


@pytest.fixture(scope="module")
def s3_card(tmp_path_factory):
    return run_scenario("disc_s3", tmp_path_factory.mktemp("o"), built_by="pipeline")["cards"][0]


def test_card_has_actors_contributions_benefits_flows(s3_card):
    assert {p.actor for p in s3_card.participants} == {"provider", "trainee"}
    assert len(s3_card.steps) == 6 and s3_card.benefits and s3_card.flows
    assert {f.type for f in s3_card.flows} >= {"money", "value", "documents"}


def test_origin_is_recorded_per_field(s3_card):
    st = s3_card.steps[0]
    assert st.origin["action"] == "SIMULATED" and st.origin["preconditions"] == "ASSUMPTION"
    assert s3_card.feasible_subsets_origin == "ASSUMPTION"


def test_source_label_only_for_verbatim_unflagged_text(s3_card):
    store = SourceStore()
    for s in s3_card.statements:
        if s.origin == "SOURCE":
            assert s.text in store.get(s.source_id).normalised
        if s.author == "actor":
            assert s.origin == "SIMULATED"               # citing S3 never turns it into SOURCE


def test_review_suggestions_are_assumptions(s3_card):
    assert s3_card.screening_signals and all(x.origin == "ASSUMPTION" and x.author == "review_agent"
                                             for x in s3_card.screening_signals)


def test_disclaimer_and_pending_status(s3_card):
    assert s3_card.disclaimer == "SIMULATED – NOT A FINDING OF FACT"
    assert s3_card.status == "pending_human_review"


# ---------------------------------------------------------------- Review Agent mutation tests
def test_review_catches_bad_citation(s3_card):
    c = copy.deepcopy(s3_card)
    next(s for s in c.statements if s.supported_by).supported_by[0].source_id = "S9"
    assert any(x.startswith("REVIEW_BAD_CITATION") for x in review.code_checks(c, SourceStore()))


def test_review_catches_span_not_in_source(s3_card):
    c = copy.deepcopy(s3_card)
    next(s for s in c.statements if s.supported_by).supported_by[0].span = "words that are not there"
    assert any(x.startswith("REVIEW_SPAN_NOT_FOUND") for x in review.code_checks(c, SourceStore()))


def test_review_catches_simulated_text_labelled_as_source(s3_card):
    c = copy.deepcopy(s3_card)
    sim = next(s for s in c.statements if s.origin == "SIMULATED")
    sim.origin, sim.source_id = "SOURCE", "S3"
    assert any(x.startswith("REVIEW_BAD_SOURCE_LABEL") for x in review.code_checks(c, SourceStore()))


def test_revision_codes_never_carry_mo_ids():
    assert review.strip_mo(["REVIEW_X:MO-04"]) == ["REVIEW_X:[mo]"]


# ---------------------------------------------------------------- safety gate
def test_gate_blocks_concrete_detail(s3_card):
    c = copy.deepcopy(s3_card)
    c.steps[0].text = "Use this step-by-step template to avoid detection"
    assert gate.precheck(c)["blocked"]


def test_gate_blocks_named_entities(s3_card):
    c = copy.deepcopy(s3_card)
    c.flows[0].description = "Paid to Mr Tan of Acme Pte Ltd"
    codes = gate.precheck(c)["codes"]
    assert any(x.startswith("NAMED_PERSON") for x in codes) and any(x.startswith("NAMED_ORGANISATION") for x in codes)


def test_gate_allows_annex_wording(s3_card):
    assert not gate.precheck(s3_card)["blocked"]


def test_classifier_is_marked_not_demonstrated(s3_card):
    assert s3_card.safety_gate["classifier"]["demonstrated"] is False


def test_action_library_passes_gate(cfg):
    assert gate.check_library(cfg) == []


def test_novelty_new_when_no_mechanism_matches(tmp_path):
    card = run_scenario("disc_s4", tmp_path, built_by="pipeline")["cards"][0]
    assert card.novelty == "new" and card.mo_matches == []
    assert any("two reviewers" in n for n in card.review_notes)
