"""Review Agent (point 7). Own boundary: reads the card (untrusted, after the gate's rule
pre-check) and the full Annex A + B. It has NO approve tool and cannot set status.

Split between CODE checks and LLM JUDGEMENT:
  code:      citation resolves · quoted span exists verbatim · origin labels consistent
  judgement: does the span support the claim? (scripted mock in the PoC)
  code:      MO matching by our annotated mechanism rule, novelty, source_support rubric
"""
from __future__ import annotations

import re

from .models import MOMatch, Suggestion

MO_ID = re.compile(r"MO-\d{2}")


def code_checks(card, store) -> list[str]:
    codes = []
    for i, s in enumerate(card.statements):
        if s.origin not in ("SOURCE", "SIMULATED", "ASSUMPTION"):
            codes.append(f"REVIEW_UNLABELLED:statements[{i}]")
        if s.origin == "SOURCE":
            rec = store.get(s.source_id or "")
            if rec is None or rec.flags or s.text not in rec.normalised:
                codes.append(f"REVIEW_BAD_SOURCE_LABEL:statements[{i}]")
        if s.origin == "SIMULATED" and s.trace_ref is None:
            codes.append(f"REVIEW_NO_TRACE_REF:statements[{i}]")
        for j, link in enumerate(s.supported_by):
            rec = store.get(link.source_id)
            if rec is None:
                codes.append(f"REVIEW_BAD_CITATION:statements[{i}].supported_by[{j}]")
            elif link.span and link.span not in rec.normalised:
                codes.append(f"REVIEW_SPAN_NOT_FOUND:statements[{i}].supported_by[{j}]")
    for i, st in enumerate(card.steps):
        if set(st.origin) != {"action", "text", "preconditions", "effects", "mechanisms"}:
            codes.append(f"REVIEW_UNLABELLED:steps[{i}]")
    return codes


def mo_match(card, cfg, store) -> tuple[list[MOMatch], str]:
    mechs = {m for st in card.steps for m in st.mechanisms} | \
        set(cfg.world["objectives"][card.objective_id].get("mechanisms", []))
    knowing = {p.actor for p in card.participants if p.role in ("colluder", "single_actor")}
    roles_with_steps = {st.actor for st in card.steps}
    if len(card.related_party) > 1 and set(card.related_party) <= roles_with_steps:
        mechs.add("entity_plays_two_roles")       # one owner acting as both provider and employer
    matches, covered_known = [], set()
    for mo_id, ann in cfg.annotations["annotations"].items():
        shared = mechs & set(ann["mechanisms"])
        if not shared:
            continue
        known = set(ann["mechanisms"]) <= mechs and set(ann["actors"]) == knowing
        matches.append(MOMatch(mo_id=mo_id, relation="known" if known else "variation",
                               basis=store.get(mo_id).basis))
        if known:
            covered_known |= shared
    if not matches:
        novelty = "new"
    elif mechs <= covered_known:
        novelty = "known"
    else:
        novelty = "variation"
    return matches, novelty


def source_support(card) -> int:
    """0 none · 1 unverified excerpt only · 2 matches an observed MO mechanism ·
    3 several observed MOs. Rewards similarity to known MOs, so it is REPORTED but
    never used as a threshold for 'new'."""
    observed = [m for m in card.mo_matches if m.basis.startswith("Observed")]
    if len(observed) >= 2:
        return 3
    if len(observed) == 1:
        return 2
    excerpt = any(l.verdict == "supports" for s in card.statements for l in s.supported_by)
    return 1 if excerpt else 0


def review(card, cfg, store, llm, pattern_key: str) -> dict:
    codes = code_checks(card, store)
    if codes:
        return {"revision_codes": codes}
    judgement = llm.review(pattern_key)                 # LLM judgement (scripted mock)
    for v in judgement.get("verdicts", []):
        for s in card.statements:
            if f"action:{v['action']}" in s.depends_on_assumptions:
                for link in s.supported_by:
                    if link.source_id == v["source_id"]:
                        link.verdict = v["verdict"]
    card.mo_matches, card.novelty = mo_match(card, cfg, store)
    card.source_support = source_support(card)
    card.screening_signals = [Suggestion(text=t) for t in judgement.get("screening_signals", [])]
    card.data_needed_for_testing = [Suggestion(text=t) for t in judgement.get("data_needed", [])]
    card.review_notes = list(judgement.get("notes", []))
    if card.novelty == "new":
        card.review_notes.append("NEW pattern: flagged 'emerging, low evidence'; needs two reviewers")
    return {"revision_codes": []}


def strip_mo(codes: list[str]) -> list[str]:
    """Revision feedback to actors carries codes and field pointers only – no MO IDs."""
    return [MO_ID.sub("[mo]", c) for c in codes]
