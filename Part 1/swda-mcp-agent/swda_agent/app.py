"""Composition root: wires adapters into the gateway. The only place that knows about files."""

from __future__ import annotations

from .adapters import CsvProgrammeCatalogue, JsonlTraceSink, YamlRegistryStore
from .config import Settings
from .gateway import Gateway
from .ports import Clock, IdFactory, SystemClock, TraceSink, uuid_ids
from .tools import ClassifierRules, ToolBox


def build_toolbox(settings: Settings) -> ToolBox:
    return ToolBox(ClassifierRules.load(settings.rules_path), CsvProgrammeCatalogue(settings.catalogue_path))


def build_gateway(
    settings: Settings,
    *,
    clock: Clock | None = None,
    ids: IdFactory | None = None,
    trace: TraceSink | None = None,
    audit: TraceSink | None = None,
) -> Gateway:
    return Gateway(
        registry_store=YamlRegistryStore(settings.registry_path),
        toolbox=build_toolbox(settings),
        trace=trace or JsonlTraceSink(settings.trace_path),
        audit=audit or JsonlTraceSink(settings.audit_path),
        clock=clock or SystemClock(),
        ids=ids or uuid_ids,
        max_payload_bytes=settings.max_payload_bytes,
        tool_timeout_s=settings.tool_timeout_s,
    )
