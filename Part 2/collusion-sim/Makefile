PY ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: all setup test demo eval review verify snapshot clean

## One command: set up, test, run every scenario, run the evaluation harness
all: setup test demo eval

setup:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -e ".[dev]"

test:
	$(BIN)/python -m pytest

demo:
	$(BIN)/python -m collusion_sim run --all

eval:
	$(BIN)/python -m collusion_sim --out out/eval eval

review:
	$(BIN)/python -m collusion_sim review interactive --as-reviewer alice

verify:
	$(BIN)/python -m collusion_sim verify

snapshot:
	UPDATE_SNAPSHOTS=1 $(BIN)/python -m pytest tests/test_regression.py

clean:
	rm -rf out .pytest_cache
