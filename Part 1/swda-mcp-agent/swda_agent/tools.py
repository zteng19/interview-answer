"""Deterministic tools and the tool invoker that enforces allowlists, schemas and timeouts."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from .errors import GatewayError
from .models import (
    ClassifyInput,
    ClassifyOutput,
    EligibilityInput,
    EligibilityOutput,
    LookupInput,
    LookupOutput,
)
from .ports import ProgrammeCatalogue, TraceSink


# --------------------------------------------------------------------------- classifier rules
@dataclass(frozen=True)
class ClassifierRules:
    version: str
    default_category: str
    categories: dict[str, dict[str, int]]  # category -> keyword -> weight

    @classmethod
    def load(cls, path: Path) -> ClassifierRules:
        with Path(path).open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(
            version=data["rules_version"],
            default_category=data["default_category"],
            categories={c: dict(v["keywords"]) for c, v in data["categories"].items()},
        )


class ToolBox:
    """The tool implementations. Pure functions of their validated input plus read-only data."""

    def __init__(self, rules: ClassifierRules, catalogue: ProgrammeCatalogue) -> None:
        self.rules = rules
        self.catalogue = catalogue
        self._patterns = {
            cat: [(kw, w, re.compile(r"\b" + re.escape(kw) + r"\b", re.I)) for kw, w in kws.items()]
            for cat, kws in rules.categories.items()
        }

    # Rule-based stand-in for the registered classifier model (Part 1B, 3a @champion).
    def classify_request(self, inp: ClassifyInput) -> ClassifyOutput:
        scores: dict[str, int] = {}
        matched: dict[str, list[str]] = {}
        for cat, patterns in self._patterns.items():
            for kw, weight, pattern in patterns:
                if pattern.search(inp.text):
                    scores[cat] = scores.get(cat, 0) + weight
                    matched.setdefault(cat, []).append(kw)
        if not scores:
            return ClassifyOutput(
                category=self.rules.default_category,
                confidence=0.0,
                matched_keywords=[],
                rules_version=self.rules.version,
            )
        order = list(self.rules.categories)  # ties broken by rule-file order: deterministic
        best = max(scores, key=lambda c: (scores[c], -order.index(c)))
        share = scores[best] / sum(scores.values())
        strength = min(1.0, scores[best] / 4)
        return ClassifyOutput(
            category=best,
            confidence=round(share * strength, 3),
            matched_keywords=sorted(matched[best]),
            rules_version=self.rules.version,
        )

    def lookup_programmes(self, inp: LookupInput) -> LookupOutput:
        return LookupOutput(
            programmes=self.catalogue.by_category(inp.category),
            catalogue_version=self.catalogue.version,
        )

    def check_eligibility(self, inp: EligibilityInput) -> EligibilityOutput:
        p = self.catalogue.get(inp.programme_id)
        if p is None:
            return EligibilityOutput(programme_id=inp.programme_id, eligible=False, reasons=["PROGRAMME_NOT_FOUND"])
        a = inp.applicant
        reasons: list[str] = []
        if not p.active:
            reasons.append("PROGRAMME_INACTIVE")
        if a.age < p.min_age:
            reasons.append("AGE_BELOW_MIN")
        if a.age > p.max_age:
            reasons.append("AGE_ABOVE_MAX")
        if a.residency not in p.residency_allowed:
            reasons.append("RESIDENCY_NOT_ALLOWED")
        if a.employment_status not in p.employment_allowed:
            reasons.append("EMPLOYMENT_STATUS_NOT_ALLOWED")
        if p.max_per_capita_income is not None:
            if a.monthly_household_income / a.household_size > p.max_per_capita_income:
                reasons.append("INCOME_ABOVE_LIMIT")
        return EligibilityOutput(
            programme_id=p.programme_id,
            eligible=not reasons,
            reasons=reasons or ["ALL_RULES_MET"],
        )


# --------------------------------------------------------------------------- tool catalogue
@dataclass(frozen=True)
class ToolSpec:
    name: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    data_classification: str
    read_only: bool
    impl: Callable[[ToolBox, Any], BaseModel] | None


def _not_in_poc(_box: ToolBox, _inp: Any) -> BaseModel:
    raise RuntimeError("Tool exists in the enterprise catalogue but is not implemented in the PoC")


class _AnyInput(BaseModel):
    case_id: str


# Every tool known to the platform. Agents get a subset; approval checks requests against this.
TOOL_CATALOGUE: dict[str, ToolSpec] = {
    "classify_request": ToolSpec(
        "classify_request", ClassifyInput, ClassifyOutput, "internal", True, lambda box, i: box.classify_request(i)
    ),
    "lookup_programmes": ToolSpec(
        "lookup_programmes", LookupInput, LookupOutput, "internal", True, lambda box, i: box.lookup_programmes(i)
    ),
    "check_eligibility": ToolSpec(
        "check_eligibility",
        EligibilityInput,
        EligibilityOutput,
        "sensitive",
        True,
        lambda box, i: box.check_eligibility(i),
    ),
    # Present in the catalogue to prove they are unreachable without approval.
    "get_case_history": ToolSpec("get_case_history", _AnyInput, _AnyInput, "restricted", True, _not_in_poc),
    "update_case_status": ToolSpec("update_case_status", _AnyInput, _AnyInput, "sensitive", False, _not_in_poc),
}

_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool")


class ToolInvoker:
    """Single path from an agent to a tool. Two independent allowlist checks, then schema checks."""

    def __init__(
        self,
        *,
        toolbox: ToolBox,
        agent_allowlist: frozenset[str],
        registry_allowlist: frozenset[str],
        trace: TraceSink,
        correlation_id: str,
        timeout_s: float,
    ) -> None:
        self._box = toolbox
        self._agent_allowlist = agent_allowlist
        self._registry_allowlist = registry_allowlist
        self._trace = trace
        self._corr = correlation_id
        self._timeout = timeout_s
        self.calls: list[str] = []

    def _deny(self, name: str, code: str, detail: str) -> GatewayError:
        self._trace.emit({"event": "tool_denied", "correlation_id": self._corr, "tool": name, "reason": detail})
        return GatewayError(code, f"Tool '{name}' denied: {detail}")

    def invoke(self, name: str, args: dict[str, Any]) -> BaseModel:
        # Check 1: the agent's own allowlist, fixed in code (independent of the registry).
        if name not in self._agent_allowlist:
            raise self._deny(name, "TOOL_DENIED", "not_in_agent_allowlist")
        # Check 2: what the approval actually granted (registry).
        if name not in self._registry_allowlist:
            raise self._deny(name, "TOOL_DENIED", "not_in_registry_allowlist")
        spec = TOOL_CATALOGUE.get(name)
        if spec is None or spec.impl is None:
            raise self._deny(name, "TOOL_DENIED", "unknown_tool")
        try:
            inp = spec.input_model.model_validate(args)
        except ValidationError as e:
            raise self._deny(name, "INVALID_TOOL_INPUT", f"{e.error_count()} validation error(s)") from e

        start = time.perf_counter()
        future = _EXECUTOR.submit(spec.impl, self._box, inp)
        try:
            out = future.result(timeout=self._timeout)
        except FutureTimeout as e:
            raise self._deny(name, "TOOL_TIMEOUT", f"exceeded {self._timeout}s") from e
        if not isinstance(out, spec.output_model):
            raise self._deny(name, "INVALID_TOOL_OUTPUT", "output failed schema")
        self.calls.append(name)
        self._trace.emit(
            {
                "event": "tool_call",
                "correlation_id": self._corr,
                "tool": name,
                "decision": "allow",
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            }
        )
        return out
