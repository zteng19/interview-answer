from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from swda_agent.adapters import MemoryTraceSink, YamlRegistryStore
from swda_agent.app import build_toolbox
from swda_agent.config import ROOT, Settings
from swda_agent.gateway import Gateway
from swda_agent.ports import FakeClock, SequentialIds

NOW = datetime.fromisoformat("2026-11-23T09:00:00+08:00")
CASEWORKER = "dev-caseworker-app-token"
REPORTING = "dev-reporting-service-token"
DISABLED = "dev-disabled-client-token"
AGENT = "service-recommendation-agent"


@pytest.fixture
def settings() -> Settings:
    return Settings.from_env()


@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    path = tmp_path / "registry.yaml"
    shutil.copy(ROOT / "config" / "registry.yaml", path)
    return path


@pytest.fixture
def store(registry_path: Path) -> YamlRegistryStore:
    return YamlRegistryStore(registry_path)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def trace() -> MemoryTraceSink:
    return MemoryTraceSink()


@pytest.fixture
def audit() -> MemoryTraceSink:
    return MemoryTraceSink()


@pytest.fixture
def toolbox(settings: Settings):  # type: ignore[no-untyped-def]
    return build_toolbox(settings)


@pytest.fixture
def gateway(store, toolbox, trace, audit, clock) -> Gateway:  # type: ignore[no-untyped-def]
    return Gateway(registry_store=store, toolbox=toolbox, trace=trace, audit=audit, clock=clock, ids=SequentialIds())


def _sample(i: int) -> dict[str, Any]:
    lines = (ROOT / "examples" / "sample_requests.jsonl").read_text(encoding="utf-8").splitlines()
    return dict(json.loads(lines[i]))


@pytest.fixture
def request_payload() -> dict[str, Any]:
    return copy.deepcopy(_sample(0))


@pytest.fixture
def sample():  # type: ignore[no-untyped-def]
    return lambda i: copy.deepcopy(_sample(i))
