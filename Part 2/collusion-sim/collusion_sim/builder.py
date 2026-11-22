"""Pattern builder (deterministic code; points 4 and 6).

Labels record ORIGIN, never citation:
  actor message / contribution text  → SIMULATED
  preconditions, effects, profile, objective, annotations → ASSUMPTION
  verbatim quote from an UNFLAGGED annex record → SOURCE
Citations become supported_by links; their support is judged later by the Review Agent.
"""
from __future__ import annotations

from .planner import order_steps
from .models import Benefit, Flow, PatternCard, Participant, Statement, Step, SupportLink


def build(run, proposal, realism: dict, planner: dict) -> PatternCard:
    cfg, store = run.cfg, run.store
    actions = cfg.actions
    objective = cfg.world["objectives"][proposal.objective_id]
    steps, flows, benefits, statements = [], [], [], []

    active = order_steps([c for c in proposal.contributions if not c.superseded], actions, cfg.world)
    for i, c in enumerate(active):
        spec = actions[c.action]
        steps.append(Step(
            index=i, actor=c.actor, action=c.action, text=c.text,
            preconditions=spec["pre"], effects=spec["eff"], mechanisms=spec.get("mechanisms", []),
            unwitting=c.unwitting, trace_ref=c.trace_ref,
            origin={"action": "SIMULATED", "text": "SIMULATED",
                    "preconditions": "ASSUMPTION", "effects": "ASSUMPTION", "mechanisms": "ASSUMPTION"}))
        if c.benefit:
            benefits.append(Benefit(actor=c.added_by, description=c.benefit))
        for f in c.flows:
            flows.append(Flow(**{"from": f.from_}, to=f.to, type=f.type, description=f.description,
                              visibility="proposal", trace_ref=c.trace_ref))
        links = []
        for cite in c.cites:
            rec = store.get(cite.source_id)
            if rec is not None and rec.flags:
                links.append(SupportLink(source_id=cite.source_id, span=None,
                                         note="source flagged by sanitiser: span withheld"))
            else:
                links.append(SupportLink(source_id=cite.source_id, span=cite.span))
                if rec is not None and cite.span and cite.span in rec.normalised:
                    statements.append(Statement(text=cite.span, origin="SOURCE", author="us",
                                                source_id=cite.source_id))
        statements.append(Statement(text=c.text, origin="SIMULATED", author="actor",
                                    supported_by=links, trace_ref=c.trace_ref,
                                    depends_on_assumptions=[f"action:{c.action}"]))
    for sa in proposal.side_agreements:
        for f in sa["flows"]:
            flows.append(Flow(**{"from": f.from_}, to=f.to, type=f.type, description=f.description,
                              visibility="private_side_agreement", trace_ref=sa["trace_ref"]))

    for p in realism["participants"]:
        goal = cfg.profiles["roles"][p["actor"]]["goals"]
        statements.append(Statement(text=f"{p['actor']} goal (profile): {goal}", origin="ASSUMPTION", author="us"))

    if planner.get("related_party"):
        statements.append(Statement(text="Scenario parameter: " + " and ".join(planner["related_party"]) +
                                    " are under common control, so they count as one effective actor",
                                    origin="ASSUMPTION", author="us"))
    assumptions = [
        f"Objective {proposal.objective_id}: {objective['text']} (world {cfg.world['version']})",
        "Preconditions, effects and SWDA checks come from our action library and world model",
        f"MO annotations {cfg.annotations['version']} are our interpretation of Annex A",
        "feasible_subsets is derived from the action library, not from agent behaviour",
    ]
    pid = f"PC-{run.scenario['id']}-{proposal.id}" + (f"-r{run.rerun_index}" if run.rerun_index else "")
    return PatternCard(
        card_type=planner["card_type"] or "collusion",
        pattern_id=pid, parent_pattern_id=run.parent_pattern_id, run_id=run.run_id,
        scenario_id=run.scenario["id"], scenario_mode=run.scenario["mode"], versions=cfg.versions(),
        objective_id=proposal.objective_id, objective_text=objective["text"],
        initial_state=cfg.world["initial_state"],
        participants=[Participant(**{**p, "role": "single_actor"})
                      if planner["card_type"] == "single-actor" and p["role"] == "colluder" else Participant(**p)
                      for p in realism["participants"]],
        steps=steps, flows=flows, benefits=benefits, feasible_subsets=planner["minimal"],
        related_party=planner.get("related_party", []), effective_subsets=planner.get("effective_minimal", []),
        statements=statements, assumptions=assumptions,
        realism={"passed": realism["passed"], "codes": realism["codes"]},
        built_by=run.built_by,
    )
