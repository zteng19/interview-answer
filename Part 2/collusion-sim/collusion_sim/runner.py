"""Builds and runs one scenario end to end."""
from __future__ import annotations

from pathlib import Path

from .config import load_config
from .llm import ScriptedMockLLM
from .orchestrator import Run
from .sanitiser import SourceStore
from .trace import FakeClock, TraceWriter


def run_scenario(scenario_id: str, out_dir: str | Path = "out", cfg=None, script: dict | None = None,
                 clock=None, built_by: str | None = None, parent_pattern_id: str | None = None,
                 reviewer_codes: list[str] | None = None, rerun_index: int = 0, scenario: dict | None = None):
    cfg = cfg or load_config()
    scenario = scenario or cfg.scenario(scenario_id)
    store = SourceStore(annex_b_path=scenario.get("annex_b_override", "data/annex_b.yaml"))
    llm = ScriptedMockLLM(script) if script is not None else ScriptedMockLLM.from_file(scenario["script"])
    run = Run(cfg, scenario, store, llm, Path(out_dir), clock or FakeClock(), TraceWriter,
              built_by=built_by, parent_pattern_id=parent_pattern_id,
              reviewer_codes=reviewer_codes, rerun_index=rerun_index)
    return run.run()
