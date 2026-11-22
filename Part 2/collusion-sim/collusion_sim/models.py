"""Typed schemas for every handoff (point 8). extra="forbid" everywhere, so an agent
cannot smuggle extra fields such as `status` into its output."""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

ROLES = ("trainee", "provider", "employer")
Role = Literal["trainee", "provider", "employer"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ sources
class SourceRecord(Strict):
    id: str
    title: Optional[str] = None
    raw: str
    normalised: str
    raw_sha256: str
    normalised_sha256: str
    basis: str                      # kept verbatim, e.g. "Observed overseas"
    flags: list[str] = []           # injection heuristics; flagged spans are never quoted


# ------------------------------------------------------------------ agent output
class Cite(Strict):
    source_id: str
    span: Optional[str] = None      # verbatim quote, if any


class FlowIn(Strict):
    from_: str = Field(alias="from")
    to: str
    type: Literal["money", "value", "documents"]
    description: str
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ProposeContributionArgs(Strict):
    action: str
    text: str
    proposal_id: Optional[str] = None     # None in R1 = an offer
    objective_id: Optional[str] = None    # required when creating a proposal
    actor: Optional[Role] = None          # another actor only for can_be_unwitting actions
    benefit: Optional[str] = None
    flows: list[FlowIn] = []
    cites: list[Cite] = []
    revises: Optional[str] = None         # contribution id, R4 only


class SendMessageArgs(Strict):
    channel: Literal["public", "private"]
    to: list[Role]
    body: str
    cites: list[str] = []
    proposal_ref: Optional[str] = None
    flows: list[FlowIn] = []              # side agreements linked to a proposal


class ProposalRefArgs(Strict):
    proposal_id: str


class EmptyArgs(Strict):
    pass


TOOL_ARGS = {
    "propose_contribution": ProposeContributionArgs,
    "send_message": SendMessageArgs,
    "accept": ProposalRefArgs,
    "decline": ProposalRefArgs,
    "decline_all": EmptyArgs,
}


class ToolCall(Strict):
    tool: str
    args: dict = {}


class AgentOutput(Strict):
    """What an actor agent may return. Private reasoning is allowed but never shared."""
    private_reasoning: Optional[str] = None
    tool_calls: list[ToolCall] = []


# ------------------------------------------------------------------ simulation state
class Contribution(Strict):
    id: str
    proposal_id: str
    added_by: Role
    actor: Role
    action: str
    text: str
    benefit: Optional[str] = None
    flows: list[FlowIn] = []
    cites: list[Cite] = []
    unwitting: bool = False
    superseded: bool = False
    trace_ref: int


class Proposal(Strict):
    id: str
    proposer: Role
    objective_id: str
    contributions: list[Contribution] = []
    side_agreements: list[dict] = []      # {from_actor, flows, trace_ref}
    audience: set[str] = set()            # knowing actors who may see it
    accepts: set[str] = set()
    declines: set[str] = set()
    withdrawn: bool = False


class FinalState(str, Enum):
    CARD_ISSUED = "CARD_ISSUED"
    NO_SCHEME = "NO_SCHEME"
    FAILED = "FAILED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    REJECTED_IN_PIPELINE = "REJECTED_IN_PIPELINE"


Origin = Literal["SOURCE", "SIMULATED", "ASSUMPTION"]


# ------------------------------------------------------------------ pattern card
class SupportLink(Strict):
    source_id: str
    span: Optional[str] = None
    verdict: Optional[Literal["supports", "partial", "does_not_support"]] = None
    note: Optional[str] = None


class Statement(Strict):
    text: str
    origin: Origin
    author: Literal["actor", "builder", "review_agent", "us"]
    source_id: Optional[str] = None          # only for SOURCE
    supported_by: list[SupportLink] = []
    depends_on_assumptions: list[str] = []
    trace_ref: Optional[int] = None


class Step(Strict):
    index: int
    actor: Role
    action: str
    text: str
    preconditions: list[str]
    effects: list[str]
    mechanisms: list[str]
    unwitting: bool
    origin: dict[str, Origin]                # per field
    trace_ref: int


class Flow(Strict):
    from_: str = Field(alias="from")
    to: str
    type: str
    description: str
    visibility: Literal["proposal", "private_side_agreement"]
    origin: Origin = "SIMULATED"
    trace_ref: int
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Benefit(Strict):
    actor: Role
    description: str
    origin: Origin = "SIMULATED"


class Participant(Strict):
    actor: Role
    role: Literal["colluder", "single_actor", "unwitting"]
    victim: bool = False


class MOMatch(Strict):
    mo_id: str
    relation: Literal["known", "variation"]
    basis: str


class Suggestion(Strict):
    text: str
    origin: Origin = "ASSUMPTION"
    author: Literal["review_agent"] = "review_agent"


class PatternCard(Strict):
    disclaimer: str = "SIMULATED – NOT A FINDING OF FACT"
    card_type: Literal["collusion", "single-actor"]
    pattern_id: str
    parent_pattern_id: Optional[str] = None
    run_id: str
    scenario_id: str
    scenario_mode: str
    versions: dict[str, str]
    objective_id: str
    objective_text: str
    initial_state: list[str]
    participants: list[Participant]
    steps: list[Step]
    flows: list[Flow]
    benefits: list[Benefit]
    feasible_subsets: list[list[str]]
    feasible_subsets_origin: Origin = "ASSUMPTION"
    related_party: list[str] = []                 # scenario parameter: one owner behind these roles
    effective_subsets: list[list[str]] = []       # feasible subsets after related parties collapse
    statements: list[Statement]
    assumptions: list[str]
    mo_matches: list[MOMatch] = []
    novelty: Optional[Literal["known", "variation", "new"]] = None
    source_support: Optional[int] = None
    realism: dict = {}
    screening_signals: list[Suggestion] = []
    data_needed_for_testing: list[Suggestion] = []
    review_notes: list[str] = []
    safety_gate: dict = {}
    built_by: str
    status: Literal["pending_human_review"] = "pending_human_review"


REASON_CODE = r"^[A-Z_]+(:[A-Za-z0-9_.\[\]\-]+)?$"


class RunReport(Strict):
    """Metadata only – no free text, so it cannot carry raw agent content."""
    run_id: str
    scenario_id: str
    final_state: FinalState
    rounds_used: int
    counts: dict[str, int]
    reason_codes: list[str]
    card_ids: list[str]
    trace_ref: str

    @field_validator("reason_codes")
    @classmethod
    def codes_only(cls, v):
        import re
        for c in v:
            if not re.match(REASON_CODE, c):
                raise ValueError(f"reason code is not metadata: {c!r}")
        return v


class Decision(Strict):
    pattern_id: str
    card_sha256: str
    decision: Literal["approve_for_testing", "reject", "request_changes"]
    reviewer: str
    weakened_control: bool
    citations_checked: list[str]
    rationale: str
    reviewer_codes: list[str] = []
    timestamp: str
