"""LLM adapter interface and the scripted mock (one script per scenario, per agent, per round).

The mock returns exactly what is scripted, including adversarial outputs, so it tests
our CODE (controls, pipeline, states) – not model behaviour. See README "What the mock
cannot prove".
"""
from __future__ import annotations

import json
from typing import Protocol

from .config import load_yaml


class LLMClient(Protocol):
    def complete(self, agent: str, round_name: str, context: dict) -> tuple[str, float]:
        """Return (raw_text_output, simulated_latency_seconds)."""


class ScriptedMockLLM:
    def __init__(self, script: dict):
        self.script = script
        self.cursor: dict[tuple[str, str], int] = {}

    @classmethod
    def from_file(cls, path: str) -> "ScriptedMockLLM":
        return cls(load_yaml(path))

    def complete(self, agent: str, round_name: str, context: dict) -> tuple[str, float]:
        items = self.script.get("agents", {}).get(agent, {}).get(round_name, [])
        i = self.cursor.get((agent, round_name), 0)
        self.cursor[(agent, round_name)] = i + 1
        if i >= len(items):
            return json.dumps({"tool_calls": []}), 1.0
        item = items[i]
        latency = 1.0
        if isinstance(item, dict) and "_latency" in item:
            item = dict(item)
            latency = float(item.pop("_latency"))
        if isinstance(item, dict) and "_raw" in item:      # deliberately malformed output
            return item["_raw"], latency
        return json.dumps(item), latency

    def review(self, pattern_key: str) -> dict:
        r = self.script.get("review", {})
        return r.get(pattern_key, r.get("default", {}))


def simulated_tokens(text: str) -> int:
    return max(1, len(text) // 4)
