"""Output safety gate (point 9): rules (code, demonstrated) + classifier (mocked).

Abstraction rule: cards describe roles and mechanisms no more concretely than Annex A.
Rules catch banned terms, named entities and over-specific details. The classifier for
"more concrete than Annex A" and evasion detail is MOCKED in the PoC – a stated
limitation: only the rules are demonstrated.
Runs twice: rule pre-check before the Review Agent, full gate after. Also checks run
reports and (in CI) the action library.
"""
from __future__ import annotations

import re

DISCLAIMER = "SIMULATED – NOT A FINDING OF FACT"

BANNED = ["template", "step-by-step", "step by step", "how to avoid", "avoid detection",
          "undetected", "bypass", "evade", "threshold", "script for", "loophole"]
NER = [
    (r"\b(Mr|Mrs|Ms|Mdm|Dr)\.?\s+[A-Z][a-z]+", "NAMED_PERSON"),
    (r"\b[A-Z][A-Za-z]+\s+(Pte\.?\s+)?Ltd\b|\bLLP\b", "NAMED_ORGANISATION"),
    (r"[\w.+-]+@[\w-]+\.[\w.]+", "EMAIL"),
    (r"https?://\S+", "URL"),
    (r"\b[689]\d{7}\b", "PHONE"),
    (r"\b[STFGM]\d{7}[A-Z]\b", "ID_NUMBER"),
]
SPECIFIC = [
    (r"\b\d{1,2}(:\d{2})?\s?(am|pm)\b", "SPECIFIC_TIME"),
    (r"\$\s?\d", "SPECIFIC_AMOUNT"),
    (r"\b\d+\s?(days?|weeks?|hours?)\b", "SPECIFIC_TIMING"),
]


def scan_text(text: str, field: str) -> list[str]:
    low = text.lower()
    codes = [f"TOO_CONCRETE:{field}" for b in BANNED if b in low][:1]
    codes += [f"{c}:{field}" for p, c in NER if re.search(p, text)]
    codes += [f"TOO_CONCRETE:{field}" for p, c in SPECIFIC if re.search(p, text, re.I)][:1]
    return sorted(set(codes))


def _fields(card):
    for i, s in enumerate(card.steps):
        yield f"steps[{i}].text", s.text
    for i, f in enumerate(card.flows):
        yield f"flows[{i}].description", f.description
    for i, b in enumerate(card.benefits):
        yield f"benefits[{i}].description", b.description
    for i, s in enumerate(card.statements):
        if s.origin != "SOURCE":                  # verbatim annex text is the abstraction baseline
            yield f"statements[{i}].text", s.text
    for i, s in enumerate(card.screening_signals):
        yield f"screening_signals[{i}]", s.text
    for i, s in enumerate(card.data_needed_for_testing):
        yield f"data_needed_for_testing[{i}]", s.text


def rules(card) -> list[str]:
    codes = []
    for field, text in _fields(card):
        codes += scan_text(text, field)
    for i, s in enumerate(card.statements):
        if not s.origin:
            codes.append(f"UNLABELLED:statements[{i}]")
    return codes


class MockClassifier:
    """Stand-in for a trained classifier. NOT demonstrated: always passes."""
    demonstrated = False

    def assess(self, card) -> dict:
        return {"result": "pass", "demonstrated": False}


def precheck(card) -> dict:
    codes = rules(card)
    return {"stage": "pre_review", "blocked": bool(codes), "codes": codes}


def full(card, classifier=None) -> dict:
    classifier = classifier or MockClassifier()
    codes = rules(card)
    cls = classifier.assess(card)
    if cls["result"] != "pass":
        codes.append("CLASSIFIER_TOO_CONCRETE")
    if card.disclaimer != DISCLAIMER:
        codes.append("MISSING_DISCLAIMER")
    result = {"stage": "full", "blocked": bool(codes), "codes": codes, "classifier": cls}
    card.safety_gate = result
    return result


def check_report(report) -> list[str]:
    codes = []
    for c in report.reason_codes:
        codes += scan_text(c, "reason_codes")
    return codes


def check_library(cfg) -> list[str]:
    codes = []
    for name in cfg.actions:
        codes += scan_text(name.replace("_", " "), f"actions.{name}")
    for oid, o in cfg.world["objectives"].items():
        codes += scan_text(o["text"], f"objectives.{oid}")
    return codes
