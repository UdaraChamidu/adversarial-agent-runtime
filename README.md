# Adversarial Agent Runtime

A self-contained implementation of the two-part agent-runtime exercise in
`candidate-brief.md`.

Following clarification from the publisher, this repository builds the entire
local challenge environment from scratch:

- a deterministic tokenizer;
- a Messages-compatible hostile mock model with scenarios S1–S12;
- chaos and red-team harnesses;
- the handwritten Part A runtime;
- the framework-backed Part B runtime;
- tests, evals, traces, and documentation.

## Current status

The deterministic tokenizer and hostile mock server are implemented. The current
24-test suite covers their protocol and all S1–S12 scenario contracts. The Part A
agent runtime is not yet implemented, so no runtime correctness or security
claims are made yet.

See `TIMELOG.md` for actual work time and `DECISIONS.md` for architecture choices.
Generated runtime state will be confined to `workspace/`.

## Commands

The final public interface will be:

```sh
make setup
make run TASK="..."
make test
make eval
make chaos
```

On Windows without GNU Make, the corresponding development interface is:

```powershell
python scripts/tasks.py setup
python scripts/tasks.py test
python scripts/tasks.py eval
python scripts/tasks.py chaos
```

Commands are added only when their implementation and contract tests exist.

## Mock model contract

Start the local server:

```sh
python -m mockllm --host 127.0.0.1 --port 8765
```

It exposes:

- `GET /health`
- `POST /v1/messages`
- `POST /admin/reset` for isolated local tests

Select S1–S12 with the `X-Scenario-ID` header or `metadata.scenario`. Keep
`metadata.request_id` stable across retries. S5 and S12 deliberately close a
response early; S6 returns 429, 529, then 200 for the same request ID.

Scenario files use JSON syntax inside `.yaml` files. JSON is valid YAML 1.2 and
keeps setup dependency-free. The server rejects input above 8,000 tokens using:

```sh
python -m mockllm.tokenizer "text to count"
```
