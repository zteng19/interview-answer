"""Trace and audit output. Production: OpenTelemetry to CloudWatch, audit to S3 Object Lock (6a, 8f)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonlTraceSink:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, record: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True, default=str) + "\n")


class MemoryTraceSink:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def emit(self, record: dict[str, Any]) -> None:
        # Round-trip through JSON so tests see exactly what a file would contain.
        self.records.append(json.loads(json.dumps(record, default=str)))
