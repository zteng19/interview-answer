"""Typed contracts for every boundary: MCP input, tool inputs/outputs, agent output, registry."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_TEXT_CHARS = 2000
MAX_RECOMMENDATIONS = 5

Residency = Literal["citizen", "pr", "other"]
Employment = Literal["employed", "unemployed", "self_employed", "student", "retired"]
Classification = Literal["public", "internal", "sensitive", "restricted"]
CLASSIFICATION_RANK: dict[str, int] = {"public": 0, "internal": 1, "sensitive": 2, "restricted": 3}


class Strict(BaseModel):
    """Base model: unknown fields are rejected, so callers cannot smuggle extra instructions."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- MCP input
class Applicant(Strict):
    age: int = Field(ge=0, le=120)
    residency: Residency
    employment_status: Employment
    monthly_household_income: float = Field(ge=0, le=1_000_000)
    household_size: int = Field(ge=1, le=20)


class ServiceRequest(Strict):
    request_id: str = Field(pattern=r"^[A-Za-z0-9\-]{1,64}$")
    channel: Literal["web", "counter", "phone", "email"]
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    applicant: Applicant


# --------------------------------------------------------------------------- tools
class ClassifyInput(Strict):
    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)


class ClassifyOutput(Strict):
    category: str
    confidence: float = Field(ge=0, le=1)
    matched_keywords: list[str]
    rules_version: str


class LookupInput(Strict):
    category: str = Field(pattern=r"^[a-z_]{1,40}$")


class Programme(Strict):
    programme_id: str
    name: str
    category: str
    priority: int
    min_age: int
    max_age: int
    residency_allowed: list[Residency]
    employment_allowed: list[Employment]
    max_per_capita_income: float | None
    active: bool


class LookupOutput(Strict):
    programmes: list[Programme]
    catalogue_version: str


class EligibilityInput(Strict):
    programme_id: str = Field(pattern=r"^[A-Z0-9\-]{1,20}$")
    applicant: Applicant


class EligibilityOutput(Strict):
    programme_id: str
    eligible: bool
    reasons: list[str]  # rule codes, never free text


# --------------------------------------------------------------------------- agent output
class ProgrammeRecommendation(Strict):
    rank: int
    programme_id: str
    name: str
    eligible: bool
    reasons: list[str]


class Recommendation(Strict):
    request_id: str
    correlation_id: str
    agent_id: str
    agent_version: str
    classifier_version: str
    catalogue_version: str
    category: str
    confidence: float = Field(ge=0, le=1)
    matched_keywords: list[str]
    recommendations: list[ProgrammeRecommendation] = Field(max_length=MAX_RECOMMENDATIONS)
    next_step: Literal["recommend", "refer_to_officer"]
    flags: list[str]
    disclaimer: str = "Decision support only. An officer makes the final decision."


# --------------------------------------------------------------------------- registry
AgentStatus = Literal["draft", "submitted", "approved", "suspended", "revoked", "retired"]
ApproverRole = Literal["product_owner", "security", "data_owner"]
REQUIRED_APPROVER_ROLES: frozenset[str] = frozenset({"product_owner", "security", "data_owner"})


class Approval(Strict):
    approver: str
    role: ApproverRole
    approved_on: date


class AgentRecord(Strict):
    agent_id: str = Field(pattern=r"^[a-z0-9\-]{3,64}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    status: AgentStatus
    owner: str
    purpose: str
    intended_users: list[str]
    data_classification: Classification
    classifier_version: str | None = None
    prompt_version: str | None = None
    image_digest: str
    allowed_tools: list[str]
    allowed_clients: list[str]
    expires_on: date
    approvals: list[Approval] = []
    evidence: list[str] = []
    automated_checks: dict[str, bool] = {}


class ClientRecord(Strict):
    client_id: str
    token_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["active", "disabled"]
    rate_limit_per_minute: int = Field(ge=1, le=10_000)


class Registry(Strict):
    registry_version: int
    cache_ttl_seconds: int = Field(ge=0, le=300)
    clients: list[ClientRecord]
    agents: list[AgentRecord]


class AgentManifest(Strict):
    """What a team submits for approval (Part 1B, section 3.10)."""

    agent_id: str = Field(pattern=r"^[a-z0-9\-]{3,64}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    owner: str
    purpose: str
    intended_users: list[str]
    data_classification: Classification
    classifier_version: str | None = None
    prompt_version: str | None = None
    image_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    requested_tools: list[str]
    allowed_clients: list[str]
    expires_on: date
    evidence: list[str]
