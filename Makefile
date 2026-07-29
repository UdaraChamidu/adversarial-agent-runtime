PYTHON ?= python3
TASK ?=
RUN_ID ?=
SCENARIO ?= S1
WORKSPACE ?= workspace
BASE_URL ?= http://127.0.0.1:8765

.PHONY: setup run resume replay mock test eval chaos clean

setup:
	$(PYTHON) scripts/tasks.py setup

run:
	$(PYTHON) -m agent run --task "$(TASK)" --scenario "$(SCENARIO)" --workspace "$(WORKSPACE)" --base-url "$(BASE_URL)"

resume:
	$(PYTHON) -m agent resume "$(RUN_ID)" --workspace "$(WORKSPACE)" --base-url "$(BASE_URL)"

replay:
	$(PYTHON) -m agent replay "$(RUN_ID)"

mock:
	$(PYTHON) -m mockllm

test:
	$(PYTHON) scripts/tasks.py test

eval:
	$(PYTHON) scripts/tasks.py eval

chaos:
	$(PYTHON) scripts/tasks.py chaos

clean:
	$(PYTHON) scripts/tasks.py clean
