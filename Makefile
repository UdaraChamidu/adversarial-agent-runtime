PYTHON ?= python3
TASK ?=
RUN_ID ?=

.PHONY: setup run resume replay mock test eval chaos clean

setup:
	$(PYTHON) scripts/tasks.py setup

run:
	$(PYTHON) -m agent run --task "$(TASK)"

resume:
	$(PYTHON) -m agent resume "$(RUN_ID)"

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
