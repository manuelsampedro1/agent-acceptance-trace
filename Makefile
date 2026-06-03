.PHONY: test lint build smoke

test:
	PYTHONPATH=src python3 -m unittest discover -s tests

lint:
	python3 -m py_compile src/agent_acceptance_trace/*.py tests/*.py

build:
	python3 -m compileall -q src tests

smoke:
	PYTHONPATH=src python3 -m agent_acceptance_trace examples/task-contract.md --diff examples/sample.diff --evidence examples/closeout.md --min-covered 80
	PYTHONPATH=src python3 -m agent_acceptance_trace examples/task-contract.md --diff examples/sample.diff --evidence examples/closeout.md --format json --min-covered 80 >/tmp/agent-acceptance-trace-smoke.json
	PYTHONPATH=src python3 -m agent_acceptance_trace examples/task-contract.md --diff examples/sample.diff --evidence examples/closeout.md --proof-packet examples/proof-packet.json --min-covered 80
