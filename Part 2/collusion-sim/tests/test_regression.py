"""Regression checks: normalised snapshots of every scripted scenario.
Update deliberately with `make snapshot` (UPDATE_SNAPSHOTS=1) and review the diff."""
import json
import os
from pathlib import Path

import pytest

from collusion_sim.config import load_config
from collusion_sim.evaluation import evaluate
from collusion_sim.runner import run_scenario

SNAP = Path(__file__).parent / "snapshots"
SCENARIOS = [s["id"] for s in load_config().scenarios["scenarios"]]
VOLATILE = {"ts", "hash", "prev_hash", "context_sha256", "run_id", "built_by"}


def normalise(obj):
    if isinstance(obj, dict):
        return {k: normalise(v) for k, v in sorted(obj.items()) if k not in VOLATILE}
    if isinstance(obj, list):
        return [normalise(v) for v in obj]
    return obj


def snapshot(sid, out):
    res = run_scenario(sid, out, built_by="pipeline")
    events = [json.loads(l) for l in (out / "traces" / f"{res['report'].run_id}.jsonl").read_text().splitlines()]
    return normalise({
        "final_state": res["final_state"].value,
        "report": res["report"].model_dump(mode="json"),
        "cards": [json.loads(c.model_dump_json(by_alias=True)) for c in res["cards"]],
        "event_kinds": [e["kind"] for e in events],
    })


@pytest.mark.parametrize("sid", SCENARIOS)
def test_snapshot(sid, tmp_path):
    got = snapshot(sid, tmp_path)
    path = SNAP / f"{sid}.json"
    if os.environ.get("UPDATE_SNAPSHOTS") == "1" or not path.exists():
        path.write_text(json.dumps(got, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert got == json.loads(path.read_text(encoding="utf-8")), f"regression in {sid}: run `make snapshot` only if intended"


def test_eval_harness_passes_on_mocks(tmp_path):
    report = evaluate(tmp_path / "eval_out")
    failed = [(r["scenario"], c) for r in report["scenarios"] for c in r["checks"] if not c["passed"]]
    assert report["passed"], failed
