"""Ports: the small interfaces the core depends on.

Local adapters (YAML, CSV, JSONL) implement them for the PoC. In production the same
interfaces are implemented by DynamoDB (registry, 10b), the Databricks SQL Warehouse
(programme views, 10g) and OpenTelemetry / CloudWatch (traces, 6a). Agent and gateway
logic do not change when an adapter is swapped.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from .models import Programme, Registry


class RegistryStore(Protocol):
    def load(self) -> Registry: ...
    def save(self, registry: Registry) -> None: ...


class ProgrammeCatalogue(Protocol):
    version: str

    def by_category(self, category: str) -> list[Programme]: ...
    def get(self, programme_id: str) -> Programme | None: ...


class TraceSink(Protocol):
    def emit(self, record: dict[str, Any]) -> None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FakeClock:
    """Deterministic clock for tests and reproducible demo runs."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


class IdFactory(Protocol):
    def __call__(self) -> str: ...


def uuid_ids() -> str:
    return str(uuid.uuid4())


class SequentialIds:
    """Deterministic correlation IDs for tests and samples."""

    def __init__(self, prefix: str = "corr") -> None:
        self._counter = itertools.count(1)
        self._prefix = prefix

    def __call__(self) -> str:
        return f"{self._prefix}-{next(self._counter):06d}"
