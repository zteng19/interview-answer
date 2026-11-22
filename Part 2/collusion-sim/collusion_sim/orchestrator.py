"""Orchestrator (deterministic code – not an LLM).

Rounds (shared budget, max 4):
  R1 propose  – offers of own actions drafted privately, revealed together at round end
  R2 negotiate – proposals and public/private messages, all via the bus
  R3 commit   – accept / decline a proposal_id (proposer counts as accepting), decline_all
  R4 revise   – only if review or gate asks; commit rule re-applied

Commit rule: the proposer counts as accepting; every actor with a step must accept, or the
step is a can_be_unwitting action added by another actor (that actor never sees the
proposal); a solo scheme contains only the proposer's steps. Each valid commit → its own card.

Final states: CARD_ISSUED · NO_SCHEME · FAILED · BUDGET_EXCEEDED · REJECTED_IN_PIPELINE.
Decline is valid; abstain (invalid output after retries) ends the run as FAILED.
"""
from __future__ import annotations

import getpass
import hashlib
import json
from pathlib import Path

from pydantic import ValidationError

from . import builder, gate, planner, realism, review
from .bus import MessageBus
from .models import (ROLES, TOOL_ARGS, AgentOutput, Contribution, FinalState, Proposal,
                     RunReport, SendMessageArgs)
from .policy import Denied, PolicyEnforcer
from .llm import simulated_tokens


class BudgetExceeded(Exception):
    pass


class Abstained(Exception):
    pass


def _rotate(seq, n):
    n %= len(seq)
    return list(seq[n:]) + list(seq[:n])


class Run:
    def __init__(self, cfg, scenario: dict, store, llm, out_dir: Path, clock, trace_cls,
                 built_by: str | None = None, parent_pattern_id: str | None = None,
                 reviewer_codes: list[str] | None = None, rerun_index: int = 0):
        self.cfg, self.scenario, self.store, self.llm, self.clock = cfg, scenario, store, llm, clock
        self.out = Path(out_dir)
        self.limits = cfg.limits
        self.rerun_index = rerun_index
        self.run_id = f"{scenario['id']}-seed{scenario.get('rng_seed', 0)}" + (f"-r{rerun_index}" if rerun_index else "")
        self.trace = trace_cls(self.out, self.run_id, clock)
        self.built_by = built_by or getpass.getuser()
        self.parent_pattern_id = parent_pattern_id
        self.reviewer_codes = reviewer_codes or []
        self.policy = PolicyEnforcer(cfg)
        self.visible = set(scenario["sources"]) if scenario["mode"] == "recall" else \
            {s for s in scenario["sources"] if not store.is_mo(s)}
        self.bus = MessageBus(self.trace, self.visible)
        self.proposals: dict[str, Proposal] = {}
        self.offers: list[dict] = []
        self.declined_all: set[str] = set()
        self.revision_codes: dict[str, list[str]] = {}
        self.counts = {k: 0 for k in ("agent_calls", "messages_sent", "messages_delivered", "tool_calls",
                                      "denied_tool_calls", "refused_messages", "retries", "gate_blocks",
                                      "simulated_tokens")}
        self.reasons: list[str] = []
        self.rounds_used = 0
        self.start = clock.now()

    # ------------------------------------------------------------------ context
    def _context(self, role: str, round_name: str) -> dict:
        prof = self.cfg.profiles["roles"][role]
        mode = self.scenario["mode"]
        sources = [s for s in self.scenario["sources"] if mode == "recall" or not self.store.is_mo(s)]
        return {
            "role": role, "round": round_name,
            "goals": prof["goals"], "propensity": self.scenario.get("propensity", "motivated"),
            "own_actions": sorted(a for a, s in self.cfg.actions.items() if s["actor"] == role),
            "knows": prof["knows"], "does_not_know": prof["does_not_know"],
            "objectives": {k: v["text"] for k, v in self.cfg.world["objectives"].items()},
            "scenario": {"id": self.scenario["id"], "params": self.scenario.get("params", {}),
                         "sources_as_data": self.store.data_view(sources)},
            "revealed_offers": self.offers if round_name != "R1" else [],
            "inbox": self.bus.inbox[role],
            "visible_proposals": [{"id": p.id, "objective_id": p.objective_id,
                                   "steps": [(c.id, c.actor, c.action) for c in p.contributions if not c.superseded]}
                                  for p in self.proposals.values() if role in p.audience],
            "revision_codes": {pid: c for pid, c in self.revision_codes.items()
                               if role in self.proposals[pid].audience},
        }

    # ------------------------------------------------------------------ limits
    def _charge(self, raw: str, latency: float):
        self.clock.advance(latency)
        self.counts["simulated_tokens"] += simulated_tokens(raw)
        if latency > self.limits["turn_timeout_s"]:
            raise BudgetExceeded("TURN_TIMEOUT")
        if (self.clock.now() - self.start).total_seconds() > self.limits["run_timeout_s"]:
            raise BudgetExceeded("RUN_TIMEOUT")
        if self.counts["simulated_tokens"] > self.limits["token_budget"]:
            raise BudgetExceeded("TOKEN_BUDGET")

    # ------------------------------------------------------------------ one turn
    def _turn(self, role: str, round_name: str):
        ctx = self._context(role, round_name)
        ctx_hash = hashlib.sha256(json.dumps(ctx, sort_keys=True, default=str).encode()).hexdigest()
        attempts = 0
        while True:
            raw, latency = self.llm.complete(role, round_name, ctx)
            self.counts["agent_calls"] += 1
            self.trace.emit("agent_call", actor=role, round=round_name, context_sha256=ctx_hash,
                            response=raw, simulated_tokens=simulated_tokens(raw), latency_s=latency)
            self._charge(raw, latency)
            try:
                out = AgentOutput.model_validate_json(raw)
                parsed = []
                for call in out.tool_calls:
                    model = TOOL_ARGS.get(call.tool)
                    parsed.append((call.tool, model.model_validate(call.args) if model else call.args))
                break
            except ValidationError as e:
                attempts += 1
                self.counts["retries"] += 1
                self.trace.emit("validation_error", actor=role, round=round_name, attempt=attempts,
                                errors=[err["msg"] for err in e.errors()][:5])
                if attempts > self.limits["max_retries"]:
                    self.trace.emit("abstained", actor=role, round=round_name)
                    raise Abstained(role)
        if out.private_reasoning:
            self.trace.emit("private_reasoning", actor=role, round=round_name, text=out.private_reasoning)
        msgs = 0
        for tool, args in parsed:
            self.counts["tool_calls"] += 1
            try:
                if tool == "send_message":
                    msgs += 1
                    if msgs > self.limits["max_msgs_per_turn"]:
                        self.counts["refused_messages"] += 1
                        raise Denied("MESSAGE_CAP")
                self.policy.check(role, round_name, tool, args, self.proposals)
                self._apply(role, round_name, tool, args)
                self.trace.emit("tool_call", actor=role, round=round_name, tool=tool,
                                args=args.model_dump(by_alias=True) if hasattr(args, "model_dump") else args,
                                allowed=True)
            except Denied as d:
                self.counts["denied_tool_calls"] += 1
                self.trace.emit("tool_call", actor=role, round=round_name, tool=tool,
                                args=args.model_dump(by_alias=True) if hasattr(args, "model_dump") else args,
                                allowed=False, code=d.code)

    def _apply(self, role, round_name, tool, a):
        if tool == "propose_contribution":
            if round_name == "R1":
                ref = self.trace.seq + 1
                self.offers.append({"actor": role, "action": a.action, "text": a.text, "trace_ref": ref})
                return
            p = self.proposals.get(a.proposal_id)
            if p is None:
                p = Proposal(id=a.proposal_id, proposer=role, objective_id=a.objective_id, audience={role})
                self.proposals[a.proposal_id] = p
            actor = a.actor or role
            n = len(p.contributions) + 1
            c = Contribution(id=f"{p.id}.{n}", proposal_id=p.id, added_by=role, actor=actor, action=a.action,
                             text=a.text, benefit=a.benefit, flows=a.flows, cites=a.cites,
                             unwitting=actor != role, trace_ref=self.trace.seq + 1)
            if a.revises:
                for old in p.contributions:
                    if old.id == a.revises:
                        old.superseded = True
            p.contributions.append(c)
            p.audience.add(role)
        elif tool == "send_message":
            self.counts["messages_sent"] += 1
            delivered = self.bus.send(role, a, self.proposals, round_name)
            self.counts["messages_delivered"] += len(delivered)
            if a.proposal_ref and a.flows:
                self.proposals[a.proposal_ref].side_agreements.append(
                    {"from_actor": role, "flows": a.flows, "trace_ref": self.trace.seq})
        elif tool == "accept":
            self.proposals[a.proposal_id].accepts.add(role)
        elif tool == "decline":
            p = self.proposals[a.proposal_id]
            p.declines.add(role)
            if role == p.proposer:
                p.withdrawn = True
        elif tool == "decline_all":
            self.declined_all.add(role)
            for p in self.proposals.values():
                if role in p.audience:
                    p.declines.add(role)
                    if role == p.proposer:
                        p.withdrawn = True

    # ------------------------------------------------------------------ rounds
    def _round(self, name: str, idx: int, roles=ROLES):
        self.rounds_used += 1
        self.trace.emit("round_start", round=name, order=_rotate(list(roles), self.scenario.get("rng_seed", 0) + idx))
        for role in _rotate(list(roles), self.scenario.get("rng_seed", 0) + idx):
            self._turn(role, name)
        if name == "R1":
            self.trace.emit("offers_revealed", offers=self.offers)

    def _valid_commits(self, only: set[str] | None = None) -> list[Proposal]:
        valid = []
        for p in self.proposals.values():
            if only is not None and p.id not in only:
                continue
            active = [c for c in p.contributions if not c.superseded]
            if p.withdrawn or not active:
                continue
            accepted = (p.accepts | {p.proposer}) - p.declines
            knowing = {c.actor for c in active if not c.unwitting}
            unwitting_ok = all(c.actor not in p.audience for c in active if c.unwitting)
            ok = knowing <= accepted and unwitting_ok
            self.trace.emit("commit_evaluated", proposal_id=p.id, knowing_step_owners=sorted(knowing),
                            accepted=sorted(accepted), unwitting_ok=unwitting_ok, valid=ok,
                            solo=knowing == {p.proposer})
            if ok:
                valid.append(p)
        return valid

    # ------------------------------------------------------------------ pipeline
    def _pipeline(self, p: Proposal) -> tuple[str, object]:
        accepted = (p.accepts | {p.proposer}) - p.declines
        rz = realism.check(p, accepted, self.cfg)
        self.trace.emit("realism", proposal_id=p.id, **{k: rz[k] for k in ("passed", "codes")})
        if not rz["passed"]:
            return "reject", rz["codes"]
        plan = planner.analyse(p.objective_id, self.cfg, self.scenario.get("params", {}).get("related_party"))
        self.trace.emit("planner", proposal_id=p.id, minimal=plan["minimal"],
                        effective_minimal=plan["effective_minimal"], card_type=plan["card_type"])
        card = builder.build(self, p, rz, plan)
        pre = gate.precheck(card)
        self.trace.emit("gate", proposal_id=p.id, **pre)
        if pre["blocked"]:
            self.counts["gate_blocks"] += 1
            return "revise", pre["codes"]
        rv = review.review(card, self.cfg, self.store, self.llm, p.id)
        self.trace.emit("review", proposal_id=p.id, revision_codes=rv["revision_codes"],
                        novelty=card.novelty, mo_matches=[m.model_dump() for m in card.mo_matches],
                        source_support=card.source_support)
        if rv["revision_codes"]:
            return "revise", rv["revision_codes"]
        full = gate.full(card)
        self.trace.emit("gate", proposal_id=p.id, stage="full", blocked=full["blocked"], codes=full["codes"],
                        classifier=full["classifier"])
        if full["blocked"]:
            self.counts["gate_blocks"] += 1
            return "revise", full["codes"]
        return "card", card

    # ------------------------------------------------------------------ run
    def run(self) -> dict:
        self.trace.emit("run_start", scenario=self.scenario, limits=self.limits, versions=self.cfg.versions(),
                        visible_sources=sorted(self.visible), parent_pattern_id=self.parent_pattern_id,
                        reviewer_codes=self.reviewer_codes, sanitiser_flags=self.store.flagged())
        cards = []
        try:
            for i, name in enumerate(("R1", "R2", "R3")):
                self._round(name, i)
            commits = self._valid_commits()
            pending = {}
            rejected_any = False
            for p in commits:
                kind, val = self._pipeline(p)
                if kind == "card":
                    cards.append(val)
                elif kind == "revise":
                    pending[p.id] = val
                else:
                    rejected_any = True
                    self.reasons += val
            if pending:
                if self.rounds_used >= self.limits["max_rounds"]:
                    raise BudgetExceeded("NO_ROUND_LEFT_FOR_REVISION")
                for pid, codes in pending.items():
                    self.revision_codes[pid] = review.strip_mo(codes)
                    p = self.proposals[pid]
                    p.accepts = set()
                    p.declines = set()
                    self.trace.emit("revision_requested", proposal_id=pid, codes=self.revision_codes[pid])
                audience = sorted(set().union(*(self.proposals[pid].audience for pid in pending)))
                self._round("R4", 3, roles=[r for r in ROLES if r in audience])
                for p in self._valid_commits(only=set(pending)):
                    kind, val = self._pipeline(p)
                    if kind == "card":
                        cards.append(val)
                    else:
                        rejected_any = True
                        self.reasons += val
                for pid in set(pending) - {p.id for p in self._valid_commits(only=set(pending))}:
                    rejected_any = True
                    self.reasons.append(f"REVISION_NOT_RECOMMITTED:{pid}")
            if cards:
                state = FinalState.CARD_ISSUED
            elif rejected_any or pending:
                state = FinalState.REJECTED_IN_PIPELINE
            else:
                state = FinalState.NO_SCHEME
        except Abstained as e:
            state = FinalState.FAILED
            self.reasons.append(f"ABSTAINED:{e.args[0]}")
        except BudgetExceeded as e:
            state = FinalState.BUDGET_EXCEEDED
            self.reasons.append(e.args[0])
        return self._handoff(state, cards)

    def _handoff(self, state: FinalState, cards: list) -> dict:
        """Orchestrator → human handoff: cards and a metadata-only run report, gate-checked."""
        report = RunReport(run_id=self.run_id, scenario_id=self.scenario["id"], final_state=state,
                           rounds_used=self.rounds_used, counts=self.counts,
                           reason_codes=self.reasons, card_ids=[c.pattern_id for c in cards],
                           trace_ref=f"traces/{self.run_id}.jsonl")
        report_gate = gate.check_report(report)
        if report_gate:
            report.reason_codes = ["REPORT_REDACTED"]
        q = self.out / "queue"
        r = self.out / "reports"
        q.mkdir(parents=True, exist_ok=True)
        r.mkdir(parents=True, exist_ok=True)
        for c in cards:
            (q / f"{c.pattern_id}.json").write_text(c.model_dump_json(indent=2, by_alias=True))
        (r / f"{self.run_id}.json").write_text(report.model_dump_json(indent=2))
        self.trace.emit("handoff", final_state=state.value, cards=[c.pattern_id for c in cards],
                        report=report.model_dump(mode="json"))
        head = self.trace.close(state.value)
        return {"final_state": state, "cards": cards, "report": report, "head_hash": head}
