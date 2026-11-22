"""Cross-platform one-command runner (works where `make` is not installed, e.g. Windows).

python run.py all        # setup + lint + typecheck + test + evaluate + demo
python run.py setup | lint | test | evaluate | demo
"""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
ENV = {**os.environ, "PYTHONUTF8": "1"}


def sh(*args: str) -> None:
    print("$", " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, env=ENV, check=True)  # noqa: S603 - fixed argument lists only


def setup() -> None:
    if not PY.exists():
        venv.create(VENV, with_pip=True)
    sh(str(PY), "-m", "pip", "install", "-q", "-e", ".[dev]")


def lint() -> None:
    sh(str(PY), "-m", "ruff", "check", ".")
    sh(str(PY), "-m", "mypy", "swda_agent")


def test() -> None:
    sh(str(PY), "-m", "pytest")


def evaluate() -> None:
    sh(str(PY), "-m", "swda_agent", "evaluate")


def demo() -> None:
    sh(str(PY), "-m", "swda_agent", "demo")


STEPS = {"setup": [setup], "lint": [lint], "test": [test], "evaluate": [evaluate], "demo": [demo],
         "all": [setup, lint, test, evaluate, demo]}  # fmt: skip

if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "all"
    if target not in STEPS:
        sys.exit(f"usage: python run.py [{'|'.join(STEPS)}]")
    if target not in ("setup", "all") and not PY.exists():
        setup()
    try:
        for step in STEPS[target]:
            step()
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)
