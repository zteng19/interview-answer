"""Input sanitiser and read-only source store (points 6 and 9).

- Hashes raw AND normalised text.
- Keeps the basis tag verbatim (e.g. "Observed overseas").
- Flags injection-like content. Detection is heuristic and secondary: the structural
  control is that source text only ever travels in data fields, and flagged spans are
  never quoted into cards. This reduces injection risk; it cannot prevent it.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from types import MappingProxyType

from .config import load_yaml
from .models import SourceRecord

INJECTION_PATTERNS = [
    r"ignore (all )?(previous|prior) instructions",
    r"disregard (the )?(rules|instructions)",
    r"you are now",
    r"system prompt",
    r"mark (this|the) pattern as approved",
    r"\bapprove (this|the) (card|pattern)\b",
]
MAX_SOURCE_CHARS = 2000


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalise(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)
    t = re.sub(r"<[^>]+>", "", t)               # strip markup
    t = t.replace("’", "'").replace("‘", "'")
    t = re.sub(r"\s+", " ", t).strip()
    return t[:MAX_SOURCE_CHARS]


def injection_flags(text: str) -> list[str]:
    low = text.lower()
    return [f"INJECTION_PATTERN:{i}" for i, p in enumerate(INJECTION_PATTERNS) if re.search(p, low)]


def sanitise(record: dict) -> SourceRecord:
    raw = record["text"]
    norm = normalise(raw)
    return SourceRecord(
        id=record["id"], title=record.get("title"), raw=raw, normalised=norm,
        raw_sha256=sha256(raw), normalised_sha256=sha256(norm),
        basis=record["basis"], flags=injection_flags(norm),
    )


class SourceStore:
    """Read-only after construction. Annex B text goes to the scenario builder;
    Annex A (and our annotations) only to the Review Agent and recall runs."""

    def __init__(self, annex_a_path="data/annex_a.yaml", annex_b_path="data/annex_b.yaml"):
        recs = {}
        for path in (annex_a_path, annex_b_path):
            for r in load_yaml(path)["records"]:
                recs[r["id"]] = sanitise(r)
        self._records = MappingProxyType(recs)

    def get(self, source_id: str) -> SourceRecord | None:
        return self._records.get(source_id)

    def ids(self) -> list[str]:
        return list(self._records)

    def is_mo(self, source_id: str) -> bool:
        return source_id.startswith("MO-")

    def flagged(self) -> dict[str, list[str]]:
        return {k: v.flags for k, v in self._records.items() if v.flags}

    def data_view(self, source_ids: list[str]) -> list[dict]:
        """Source text as DATA fields for an agent brief. In flagged records the
        flagged sentence is replaced; the rest is still passed as data."""
        out = []
        for sid in source_ids:
            r = self._records[sid]
            text = redact_flagged(r.normalised) if r.flags else r.normalised
            out.append({"source_id": sid, "basis": r.basis, "text": text,
                        "flagged": bool(r.flags)})
        return out


def redact_flagged(text: str) -> str:
    sentences = re.split(r"(?<=[.?!])\s+", text)
    kept = [s if not injection_flags(s) else "[flagged content removed]" for s in sentences]
    return " ".join(kept)
