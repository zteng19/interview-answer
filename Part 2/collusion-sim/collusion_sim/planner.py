"""Minimal actor-set analysis (point 5).

Question asked: which sets of KNOWING actors could reach the objective by ANY path?
A subset may use all of its own actions plus other actors' can_be_unwitting actions.
SWDA checks are system rules applied automatically, so actors become necessary
because of the checks. The result depends entirely on the action library, so it is
labelled ASSUMPTION on the card, and planner paths are never written to cards.
"""
from __future__ import annotations

from collections import deque
from itertools import combinations

from .models import ROLES


def apply_checks(state: frozenset, checks: dict) -> frozenset:
    s = set(state)
    changed = True
    while changed:
        changed = False
        for chk in checks.values():
            if set(chk["requires"]) <= s and not set(chk["adds"]) <= s:
                s |= set(chk["adds"])
                changed = True
    return frozenset(s)


def order_steps(contributions, actions: dict, world: dict) -> list:
    """Assemble contributions into an executable order: repeatedly take the first
    contribution (in proposal order) whose preconditions hold. Anything left over is
    appended unchanged, and the realism check will then fail on it."""
    remaining = list(contributions)
    state = apply_checks(frozenset(world["initial_state"]), world["checks"])
    ordered = []
    while remaining:
        pick = next((c for c in remaining
                     if c.action in actions and set(actions[c.action]["pre"]) <= state), None)
        if pick is None:
            ordered += remaining
            break
        remaining.remove(pick)
        ordered.append(pick)
        state = apply_checks(state | frozenset(actions[pick.action]["eff"]), world["checks"])
    return ordered


def reachable(subset: tuple[str, ...], objective: dict, actions: dict, world: dict, max_depth: int = 12) -> bool:
    allowed = {name: a for name, a in actions.items()
               if a["actor"] in subset or a.get("can_be_unwitting", False)}
    forbidden = set(objective.get("forbidden", []))
    allowed = {n: a for n, a in allowed.items() if not (set(a["eff"]) & forbidden)}
    goal = set(objective["goal"])
    start = apply_checks(frozenset(world["initial_state"]), world["checks"])
    seen = {start}
    q = deque([(start, 0)])
    while q:
        state, d = q.popleft()
        if goal <= state:
            return True
        if d >= max_depth:
            continue
        for a in allowed.values():
            if set(a["pre"]) <= state and not set(a["eff"]) <= state:
                nxt = apply_checks(state | frozenset(a["eff"]), world["checks"])
                if nxt not in seen:
                    seen.add(nxt)
                    q.append((nxt, d + 1))
    return False


def effective(subset, related_party: list[str] | None) -> list[str]:
    """Related-party roles (one owner behind both, e.g. MO-01) collapse to one effective actor."""
    rp = set(related_party or [])
    out = {("+".join(sorted(rp)) if a in rp and len(rp) > 1 else a) for a in subset}
    return sorted(out)


def analyse(objective_id: str, cfg, related_party: list[str] | None = None) -> dict:
    objective = cfg.world["objectives"][objective_id]
    feasible = []
    for k in (1, 2, 3):
        for subset in combinations(ROLES, k):
            if reachable(subset, objective, cfg.actions, cfg.world):
                feasible.append(list(subset))
    minimal = [s for s in feasible if not any(set(o) < set(s) for o in feasible)]
    eff = []
    for s in minimal:
        e = effective(s, related_party)
        if e not in eff:
            eff.append(e)
    smallest = min((len(s) for s in eff), default=None)
    if smallest is None:
        card_type = None
    else:
        card_type = "single-actor" if smallest == 1 else "collusion"
    return {"feasible": feasible, "minimal": minimal, "effective_minimal": eff,
            "card_type": card_type, "subsets_tested": 7, "related_party": list(related_party or [])}
