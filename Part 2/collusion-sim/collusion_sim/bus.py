"""Mediated message bus with public and private channels (point 2).

- Relay checks on every message: injection patterns, and no leak of a proposal that
  contains an unwitting step to that unwitting actor (or to the public channel).
- Recipient filter: strips source IDs the recipient may not see (e.g. MO IDs in
  discovery runs). Partial protection – a paraphrase passes. Both the sent and the
  delivered version are traced.
"""
from __future__ import annotations

import re

from .models import ROLES, SendMessageArgs
from .policy import Denied
from .sanitiser import injection_flags

ID_RE = re.compile(r"\b(MO-\d{2}|S\d)\b")


class MessageBus:
    def __init__(self, trace, visible_ids: set[str]):
        self.trace = trace
        self.visible = visible_ids
        self.inbox: dict[str, list[dict]] = {r: [] for r in ROLES}

    def _filter(self, text: str) -> str:
        return ID_RE.sub(lambda m: m.group(0) if m.group(0) in self.visible else "[id-removed]", text)

    def send(self, sender: str, a: SendMessageArgs, proposals: dict, round_name: str) -> list[str]:
        recipients = [r for r in ROLES if r != sender] if a.channel == "public" else list(a.to)
        sent_id = self.trace.emit("message_sent", sender=sender, round=round_name, channel=a.channel,
                                  to=recipients, body=a.body, cites=a.cites, proposal_ref=a.proposal_ref)
        # relay checks
        if injection_flags(a.body):
            self.trace.emit("message_blocked", ref=sent_id, code="RELAY_INJECTION_PATTERN")
            raise Denied("RELAY_INJECTION_PATTERN")
        if a.proposal_ref:
            p = proposals[a.proposal_ref]
            unwitting = {c.actor for c in p.contributions if c.unwitting and not c.superseded}
            if a.channel == "public" or unwitting & set(recipients):
                self.trace.emit("message_blocked", ref=sent_id, code="RELAY_UNWITTING_LEAK")
                raise Denied("RELAY_UNWITTING_LEAK")
        delivered_body = self._filter(a.body)
        delivered_cites = [c for c in a.cites if c in self.visible]
        for r in recipients:
            self.inbox[r].append({"from": sender, "channel": a.channel, "body": delivered_body,
                                  "cites": delivered_cites, "proposal_ref": a.proposal_ref})
            if a.proposal_ref:
                proposals[a.proposal_ref].audience.add(r)
        self.trace.emit("message_delivered", ref=sent_id, to=recipients, body=delivered_body,
                        cites=delivered_cites, filtered=delivered_body != a.body or delivered_cites != a.cites)
        return recipients
