import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from collusion_sim.config import load_config  # noqa: E402
from collusion_sim.runner import run_scenario  # noqa: E402


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def out(tmp_path):
    return tmp_path / "out"


def inline_scenario(sid="inline", sources=("S3",), mode="discovery"):
    return {"id": sid, "mode": mode, "sources": list(sources), "params": {}, "propensity": "motivated",
            "script": None, "rng_seed": 0}


@pytest.fixture
def run_inline(out):
    """Run an inline mock script. Turn order with rng_seed 0:
    R1 trainee→provider→employer · R2 provider→employer→trainee · R3 employer→trainee→provider."""
    def _run(script, cfg=None, sources=("S3",), sid="inline"):
        return run_scenario(sid, out, cfg=cfg, script=script, built_by="pipeline",
                            scenario=inline_scenario(sid, sources))
    return _run


def P(**args):
    return {"tool": "propose_contribution", "args": args}


def turn(*calls):
    return {"tool_calls": list(calls)}
