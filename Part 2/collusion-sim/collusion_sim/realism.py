"""Realism checks (code, pass/fail) – this IS the plausibility decision (points 4 and 3).

Roles by rule:
  colluder  = accepted the committed proposal (the proposer counts as accepting)
  unwitting = has a step, never accepted or saw the proposal
  victim    = flag: is the source of a money/value flow and has no benefit
A colluder with no benefit fails the check.
"""
from __future__ import annotations

from .planner import apply_checks, order_steps


def check(proposal, accepted: set[str], cfg) -> dict:
    actions, world = cfg.actions, cfg.world
    objective = world["objectives"][proposal.objective_id]
    codes: list[str] = []
    steps = order_steps([c for c in proposal.contributions if not c.superseded], actions, world)
    state = apply_checks(frozenset(world["initial_state"]), world["checks"])
    for i, c in enumerate(steps):
        spec = actions.get(c.action)
        if spec is None or spec["actor"] != c.actor:
            codes.append(f"REALISM_ACTION_NOT_IN_LIBRARY:steps[{i}]")
            continue
        if not set(spec["pre"]) <= state:
            codes.append(f"REALISM_PRECONDITION_UNMET:steps[{i}]")
        if set(spec["eff"]) & set(objective.get("forbidden", [])):
            codes.append(f"REALISM_FORBIDDEN_EFFECT:steps[{i}]")
        state = apply_checks(state | frozenset(spec["eff"]), world["checks"])
    if not set(objective["goal"]) <= state:
        codes.append("REALISM_OBJECTIVE_NOT_REACHED")

    actors_with_steps = {c.actor for c in steps}
    colluders = accepted & (actors_with_steps | {proposal.proposer})
    unwitting = {c.actor for c in steps if c.unwitting} - colluders
    benefits = {c.added_by for c in steps if c.benefit}      # benefit stated by the contributor
    for f in [f for c in steps for f in c.flows] + [f for sa in proposal.side_agreements for f in sa["flows"]]:
        if f.type in ("money", "value") and f.to in colluders:
            benefits.add(f.to)
    for actor in sorted(colluders):
        if actor not in benefits:
            codes.append(f"REALISM_NO_BENEFIT:{actor}")
    victims = set()
    for f in [f for c in steps for f in c.flows]:
        if f.type in ("money", "value") and f.from_ in (colluders | unwitting) and f.from_ not in benefits:
            victims.add(f.from_)
    participants = [{"actor": a, "role": "colluder", "victim": a in victims} for a in sorted(colluders)] + \
                   [{"actor": a, "role": "unwitting", "victim": a in victims} for a in sorted(unwitting)]
    return {"passed": not codes, "codes": codes, "participants": participants,
            "final_state_facts": sorted(state)}
