"""Policy enforcer: per-role tool allowlist AND argument checks (point 3).

Least privilege is about arguments, not just tool names: all three actors can call
propose_contribution, but each may only propose its OWN actions – except a
can_be_unwitting action of another actor, which becomes an unwitting step.
"""
from __future__ import annotations

from .models import ProposeContributionArgs, SendMessageArgs, ProposalRefArgs

ACTOR_TOOLS = {"propose_contribution", "send_message", "accept", "decline", "decline_all"}

# which tools each round allows
ROUND_TOOLS = {
    "R1": {"propose_contribution"},                                   # offers only
    "R2": {"propose_contribution", "send_message"},
    "R3": {"accept", "decline", "decline_all"},
    "R4": {"propose_contribution", "send_message", "accept", "decline"},
}


class Denied(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class PolicyEnforcer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.actions = cfg.actions
        self.max_chars = cfg.limits["max_chars"]
        self.objectives = cfg.world["objectives"]

    def check(self, caller: str, round_name: str, tool: str, args, proposals: dict) -> None:
        if tool not in ACTOR_TOOLS:
            raise Denied(f"TOOL_NOT_ALLOWED:{tool}")
        if tool not in ROUND_TOOLS[round_name]:
            raise Denied(f"TOOL_NOT_IN_ROUND:{tool}")
        if tool == "propose_contribution":
            self._check_contribution(caller, round_name, args, proposals)
        elif tool == "send_message":
            self._check_message(caller, args, proposals)
        elif tool in ("accept", "decline"):
            p = proposals.get(args.proposal_id)
            if p is None or caller not in p.audience:
                raise Denied("PROPOSAL_NOT_VISIBLE")

    def _check_contribution(self, caller, round_name, a: ProposeContributionArgs, proposals):
        spec = self.actions.get(a.action)
        if spec is None:
            raise Denied(f"UNKNOWN_ACTION:{a.action}")
        actor = a.actor or caller
        if actor == caller:
            if spec["actor"] != caller:
                raise Denied(f"ACTION_OUTSIDE_ROLE:{a.action}")
        else:
            if round_name == "R1":
                raise Denied("OFFER_MUST_BE_OWN_ACTION")
            if spec["actor"] != actor or not spec.get("can_be_unwitting", False):
                raise Denied(f"NOT_UNWITTING_CAPABLE:{a.action}")
        if len(a.text) > self.max_chars:
            raise Denied("TEXT_TOO_LONG")
        if round_name == "R1":
            if a.proposal_id is not None:
                raise Denied("R1_IS_OFFERS_ONLY")
            return
        if a.proposal_id is None:
            raise Denied("PROPOSAL_ID_REQUIRED")
        p = proposals.get(a.proposal_id)
        if p is None:
            if a.objective_id not in self.objectives:
                raise Denied("OBJECTIVE_REQUIRED_FROM_FIXED_LIST")
        elif caller not in p.audience:
            raise Denied("PROPOSAL_NOT_VISIBLE")
        if a.revises is not None:
            if round_name != "R4":
                raise Denied("REVISE_ONLY_IN_R4")
            if p is None or not any(c.id == a.revises for c in p.contributions):
                raise Denied("REVISES_UNKNOWN_CONTRIBUTION")

    def _check_message(self, caller, a: SendMessageArgs, proposals):
        allowed = set(self.cfg.profiles["roles"][caller]["private_with"])
        if caller in a.to or not a.to:
            raise Denied("BAD_RECIPIENTS")
        if not set(a.to) <= allowed:
            raise Denied("RECIPIENT_NOT_ALLOWED")
        if len(a.body) > self.max_chars:
            raise Denied("TEXT_TOO_LONG")
        if a.proposal_ref is not None:
            p = proposals.get(a.proposal_ref)
            if p is None or caller not in p.audience:
                raise Denied("PROPOSAL_NOT_VISIBLE")
