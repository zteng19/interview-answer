PY := .venv/bin/python
export PYTHONUTF8 := 1

.PHONY: all setup lint test evaluate demo serve docker clean

all: setup lint test evaluate demo

setup:
	test -x $(PY) || python3 -m venv .venv
	$(PY) -m pip install -q -e ".[dev]"

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
	$(PY) -m mypy swda_agent

test:
	$(PY) -m pytest

evaluate:
	$(PY) -m swda_agent evaluate

demo:
	$(PY) -m swda_agent demo

serve:
	$(PY) -m swda_agent serve

docker:
	docker build -t swda-mcp-agent:1.0.0 .

clean:
	rm -rf out .pytest_cache .mypy_cache .ruff_cache
