"""Input guard and personal-data masking.

Request text is *data*. It is never used to choose tools, arguments or control flow; the
agent's tool sequence is fixed in code. The guard only flags instruction-like text (so an
officer can see it) and masks personal data before anything is logged.
"""

from __future__ import annotations

import hashlib
import re

INJECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "IGNORE_INSTRUCTIONS": re.compile(r"\b(ignore|disregard|forget)\b.{0,30}\b(instruction|rule|prompt)s?\b", re.I),
    "ROLE_OVERRIDE": re.compile(r"\b(you are now|act as|pretend to be|system prompt)\b", re.I),
    "TOOL_REQUEST": re.compile(r"\b(call|invoke|run|use)\b.{0,40}\b(tool|function|api|command)\b", re.I),
    "FORCE_DECISION": re.compile(
        r"\b(approve|mark (me )?(as )?eligible|grant)\b.{0,30}\b(all|every|anyway|regardless)\b", re.I
    ),
    "MARKUP": re.compile(r"<\s*/?\s*(script|system|tool|instructions?)\b", re.I),
}

PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("[NRIC]", re.compile(r"\b[STFGM]\d{7}[A-Z]\b", re.I)),
    ("[EMAIL]", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("[PHONE]", re.compile(r"(?<!\d)(\+65[ -]?)?[689]\d{3}[ -]?\d{4}(?!\d)")),
    ("[POSTCODE]", re.compile(r"\b(?:singapore|s\(?)\s*\d{6}\)?\b", re.I)),
]


def scan(text: str) -> list[str]:
    """Return sorted flag codes for instruction-like content."""
    return sorted(code for code, pattern in INJECTION_PATTERNS.items() if pattern.search(text))


def mask_pii(text: str) -> str:
    for token, pattern in PII_PATTERNS:
        text = pattern.sub(token, text)
    return text


def text_fingerprint(text: str) -> str:
    """Hash for correlating a request across logs without storing it."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_excerpt(text: str, limit: int = 80) -> str:
    """Masked, truncated excerpt for traces. Masking happens before truncation."""
    masked = mask_pii(text)
    return masked if len(masked) <= limit else masked[:limit] + "..."
