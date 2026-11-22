"""The Service Recommendation Agent.

Its control flow is fixed: classify -> look up programmes -> check eligibility -> rank.
Nothing in the request text can add, remove or reorder tool calls.
"""

from __future__ import annotations

from typing import cast

from . import guard
from .models import (
    ClassifyOutput,
    EligibilityOutput,
    LookupOutput,
    ProgrammeRecommendation,
    Recommendation,
    ServiceRequest,
)
from .tools import ToolInvoker

LOW_CONFIDENCE = 0.5
MAX_RESULTS = 5


class ServiceRecommendationAgent:
    AGENT_ID = "service-recommendation-agent"
    VERSION = "1.0.0"
    # In-code allowlist: the second, independent check behind the registry policy.
    ALLOWED_TOOLS: frozenset[str] = frozenset({"classify_request", "lookup_programmes", "check_eligibility"})
    DESCRIPTION = (
        "Classifies a service request and recommends relevant SWDA programmes with rule-based "
        "eligibility. Decision support only; an officer makes the final decision. Synthetic data."
    )

    def run(self, req: ServiceRequest, tools: ToolInvoker, correlation_id: str) -> Recommendation:
        flags = guard.scan(req.text)

        cls = cast(ClassifyOutput, tools.invoke("classify_request", {"text": req.text}))
        lookup = cast(LookupOutput, tools.invoke("lookup_programmes", {"category": cls.category}))

        checked: list[tuple[int, str, str, EligibilityOutput]] = []
        for p in lookup.programmes:
            result = cast(
                EligibilityOutput,
                tools.invoke(
                    "check_eligibility",
                    {"programme_id": p.programme_id, "applicant": req.applicant.model_dump()},
                ),
            )
            checked.append((p.priority, p.programme_id, p.name, result))

        # Eligible first, then catalogue priority, then ID: a total, deterministic order.
        checked.sort(key=lambda t: (not t[3].eligible, t[0], t[1]))
        recs = [
            ProgrammeRecommendation(rank=i, programme_id=pid, name=name, eligible=r.eligible, reasons=r.reasons)
            for i, (_, pid, name, r) in enumerate(checked[:MAX_RESULTS], start=1)
        ]

        if cls.confidence < LOW_CONFIDENCE:
            flags.append("LOW_CONFIDENCE")
        if not any(r.eligible for r in recs):
            flags.append("NO_ELIGIBLE_PROGRAMME")
        refer = bool({"LOW_CONFIDENCE", "NO_ELIGIBLE_PROGRAMME"} & set(flags)) or any(
            f in guard.INJECTION_PATTERNS for f in flags
        )

        return Recommendation(
            request_id=req.request_id,
            correlation_id=correlation_id,
            agent_id=self.AGENT_ID,
            agent_version=self.VERSION,
            classifier_version=cls.rules_version,
            catalogue_version=lookup.catalogue_version,
            category=cls.category,
            confidence=cls.confidence,
            matched_keywords=cls.matched_keywords,
            recommendations=recs,
            next_step="refer_to_officer" if refer else "recommend",
            flags=sorted(set(flags)),
        )


# Agents this server can run. The registry decides which of them may be exposed, and to whom.
AGENT_IMPLEMENTATIONS = {ServiceRecommendationAgent.AGENT_ID: ServiceRecommendationAgent}
