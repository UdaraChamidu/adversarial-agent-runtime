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

The deterministic tokenizer, hostile mock server, seeded process-kill primitive,
public/generated red-team corpus, and Part A SQLite durability core are
implemented. All five tools and the trusted-task capability gate are also
implemented. The current 60-test suite covers their contracts.

The durability unit suite proves one simulated email row after retries, concurrent
callers, and injected failures at every SQLite transaction boundary. This is not
yet the full R2 claim: the end-to-end agent resume path and 100 external process
kills still need to pass.

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

## Harness contracts

The generic crash primitive can repeatedly kill any command at deterministic
pseudo-random times:

```sh
python -m harness.chaos --runs 10 --seed 7 -- python your_target.py
```

The final `make chaos` command will wrap this primitive around `agent run` and
`agent resume`, then assert the SQLite email invariant.

`harness/redteam/payloads/` contains public provenance, filesystem, network,
encoding, and email-injection attacks. The corpus module also creates seeded
variants so runtime defenses cannot pass by matching only the committed strings.

## Durability contract

Canonical run history is stored in an append-only, SHA-256-linked SQLite event
log. Mutable run columns are rebuildable projections. A per-run OS advisory lock
prevents two processes from advancing one run at the same time.

For `send_email`, the email row, tool-effect idempotency record, result, and event
share one `BEGIN IMMEDIATE` transaction. A retry with the same internal tool
occurrence returns its stored result. A second occurrence for the same authorized
logical email slot is deduplicated. Reusing either key with changed content fails
loudly.

## Tool security contract

- `read_file` and atomic `write_file` normalize both slash styles, reject
  absolute/drive/traversal paths and symlink components, and stay under
  `workspace/`.
- `run_python` parses the AST, allows only a small safe-module set, rejects file,
  process, network, dynamic-code, and dunder access, and enforces time/output
  bounds. Unix additionally applies memory/CPU/file-descriptor limits. Windows
  does not yet provide an OS-grade memory or network namespace; this remains an
  explicit limitation.
- `http_get` requires an exact configured origin, permits only localhost/literal
  loopback, rechecks DNS results, rejects userinfo, and refuses redirects.
- `send_email` requires an immutable capability parsed from an explicit original
  task such as “send exactly one email to …”. Tool/model content cannot create or
  widen that capability or change its recipient.
- Tool outputs are serialized with `trust: untrusted_tool_data`; model claims do
  not change recorded tool status.
