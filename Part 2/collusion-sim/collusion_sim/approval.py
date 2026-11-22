"""Human review and approval service (point 9). Only this module can set a decision.

- Identity: OS login checked against the reviewer allowlist (SSO in production). On a
  single PoC machine, --as-reviewer NAME is accepted but logged as WEAKENED_CONTROL.
- Builder ≠ reviewer: if the reviewer equals card.built_by, only the override works,
  and it is logged as WEAKENED_CONTROL.
- Checklist: the reviewer must confirm every cited source ID.
- Hash binding: the decision stores the card's SHA-256; any later edit invalidates it.
  There is no key, so this detects edits but is not a signature.
- 'new' patterns need two distinct reviewers before entering the register.
- Approval means "approved for testing" – patterns stay hypotheses.
"""
from __future__ import annotations

import getpass
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .models import Decision, PatternCard


class ApprovalError(Exception):
    pass


def card_sha256(card_json: str) -> str:
    obj = json.loads(card_json)
    obj.pop("status", None)
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def citations(card: PatternCard) -> list[str]:
    return sorted({l.source_id for s in card.statements for l in s.supported_by} |
                  {s.source_id for s in card.statements if s.source_id} |
                  {m.mo_id for m in card.mo_matches})


class ApprovalService:
    def __init__(self, out_dir: Path, allowlist: list[str]):
        self.out = Path(out_dir)
        self.allowlist = set(allowlist)
        self.log = self.out / "decisions.jsonl"
        self.register = self.out / "register.jsonl"

    def queue(self) -> list[Path]:
        return sorted((self.out / "queue").glob("*.json"))

    def load(self, pattern_id: str) -> tuple[PatternCard, str]:
        path = self.out / "queue" / f"{pattern_id}.json"
        if not path.exists():
            raise ApprovalError(f"no such card in queue: {pattern_id}")
        raw = path.read_text()
        return PatternCard.model_validate_json(raw), raw

    def _identity(self, as_reviewer: str | None, card: PatternCard) -> tuple[str, bool]:
        os_user = getpass.getuser()
        if as_reviewer is None:
            if os_user not in self.allowlist:
                raise ApprovalError(f"'{os_user}' is not an allowlisted reviewer (use --as-reviewer in the PoC)")
            if os_user == card.built_by:
                raise ApprovalError("builder cannot review own card (use --as-reviewer in the PoC)")
            return os_user, False
        if as_reviewer not in self.allowlist:
            raise ApprovalError(f"'{as_reviewer}' is not an allowlisted reviewer")
        return as_reviewer, True                     # override on one machine → weakened control

    def decisions(self, pattern_id: str | None = None) -> list[Decision]:
        if not self.log.exists():
            return []
        ds = [Decision.model_validate_json(l) for l in self.log.read_text().splitlines() if l.strip()]
        return [d for d in ds if pattern_id is None or d.pattern_id == pattern_id]

    def decide(self, pattern_id: str, decision: str, rationale: str, citations_checked: list[str],
               as_reviewer: str | None = None, reviewer_codes: list[str] | None = None) -> Decision:
        card, raw = self.load(pattern_id)
        reviewer, weakened = self._identity(as_reviewer, card)
        if not rationale.strip():
            raise ApprovalError("a rationale is mandatory")
        missing = set(citations(card)) - set(citations_checked)
        if missing:
            raise ApprovalError(f"checklist incomplete – open and confirm: {sorted(missing)}")
        prior = [d for d in self.decisions(pattern_id) if d.decision == "approve_for_testing"]
        if any(d.reviewer == reviewer for d in prior):
            raise ApprovalError("this reviewer has already approved this card")
        d = Decision(pattern_id=pattern_id, card_sha256=card_sha256(raw), decision=decision,
                     reviewer=reviewer, weakened_control=weakened, citations_checked=sorted(citations_checked),
                     rationale=rationale, reviewer_codes=reviewer_codes or [],
                     timestamp=datetime.now(timezone.utc).isoformat())
        with open(self.log, "a") as f:
            f.write(d.model_dump_json() + "\n")
        if decision == "approve_for_testing":
            needed = 2 if card.novelty == "new" else 1
            approvals = prior + [d]
            if len({a.reviewer for a in approvals}) >= needed:
                self._register(card, raw, approvals)
        if decision == "request_changes":
            rr = self.out / "rerun_requests"
            rr.mkdir(exist_ok=True)
            (rr / f"{pattern_id}.json").write_text(json.dumps({
                "scenario_id": card.scenario_id, "parent_pattern_id": pattern_id,
                "reviewer_codes": reviewer_codes or []}, indent=2))
        return d

    def _register(self, card: PatternCard, raw: str, approvals: list[Decision]):
        entry = {"pattern_id": card.pattern_id, "status": "approved_for_testing",
                 "card_sha256": card_sha256(raw), "novelty": card.novelty,
                 "reviewers": sorted({a.reviewer for a in approvals}),
                 "weakened_control": any(a.weakened_control for a in approvals),
                 "note": "Hypothesis only: back-test on historical claims and review fairness before any screening rule"}
        with open(self.register, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def verify(self, pattern_id: str) -> bool:
        """True if every decision still matches the current card bytes (hash binding)."""
        _, raw = self.load(pattern_id)
        h = card_sha256(raw)
        return all(d.card_sha256 == h for d in self.decisions(pattern_id))
