"""Synthetic programme catalogue from CSV. Production: read-only SQL Warehouse view (10g)."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from ..models import Programme


def _list(value: str) -> list[str]:
    return [v.strip() for v in value.split("|") if v.strip()]


class CsvProgrammeCatalogue:
    def __init__(self, path: Path) -> None:
        raw = Path(path).read_bytes()
        # Version = content hash, so every recommendation records exactly which data it used.
        self.version = "catalogue-" + hashlib.sha256(raw).hexdigest()[:12]
        rows = csv.DictReader(raw.decode("utf-8").splitlines())
        self._programmes: dict[str, Programme] = {}
        for row in rows:
            p = Programme.model_validate(
                {
                    "programme_id": row["programme_id"],
                    "name": row["name"],
                    "category": row["category"],
                    "priority": int(row["priority"]),
                    "min_age": int(row["min_age"]),
                    "max_age": int(row["max_age"]),
                    "residency_allowed": _list(row["residency_allowed"]),
                    "employment_allowed": _list(row["employment_allowed"]),
                    "max_per_capita_income": float(row["max_per_capita_income"])
                    if row["max_per_capita_income"]
                    else None,
                    "active": row["active"].strip().lower() == "true",
                }
            )
            self._programmes[p.programme_id] = p

    def by_category(self, category: str) -> list[Programme]:
        found = [p for p in self._programmes.values() if p.category == category and p.active]
        return sorted(found, key=lambda p: (p.priority, p.programme_id))

    def get(self, programme_id: str) -> Programme | None:
        return self._programmes.get(programme_id)
