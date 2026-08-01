# Adversarial Agent Runtime

A self-contained implementation of Part A of the adversarial agent runtime
assessment. Part B is intentionally not started, following the required
submission sequence.

Following clarification from the assessment team, this repository also builds
the local challenge environment from scratch:

- a deterministic tokenizer;
- a Messages-compatible hostile mock model with scenarios S1–S12;
- chaos and red-team harnesses;
- the handwritten Part A runtime;
- tests, evals, traces, and documentation.

## Current status

The deterministic tokenizer, hostile mock server, seeded process-kill primitive,
public/generated red-team corpus, and Part A SQLite durability core are
implemented. All five tools and the trusted-task capability gate are also
implemented. The durable Messages client, event-derived loop, and working
`run`/`resume` CLI now pass S1–S12. Context compaction, JSONL traces, and offline
replay are also implemented. The current 93-test suite covers their contracts.

R2 now passes end to end. In the recorded batch, 100 distinct logical email runs
were each hard-killed in a separate process, resumed in a fresh process, and
checked for a valid event chain, completed state, and exactly one email row:
`100/100` passed with `100` observed kills. The machine-readable result is
`evidence/chaos-100.json`.

S8 passes 40 turns: every exact serialized request remains at or below 8,000
tokens, and the fact introduced at turn 3 is correctly available at turn 40.
The mock derives S8 progress from its own emitted turn markers, not runtime-owned
step metadata.

See `TIMELOG.md` for actual work time and `DECISIONS.md` for architecture choices.
Generated runtime state will be confined to `workspace/`.

## Commands

The public interface is:

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
`make setup` performs an editable install with no runtime dependencies so the
`agent` and `mockllm` console commands are available. On Windows, Python's user
Scripts directory may not be on `PATH`; use `python -m agent` and
`python -m mockllm` as the supported equivalents.

`make eval` runs 21 cases, prints the pass rate, writes
`evals/reports/latest.json`, and diffs statuses against
`evals/baseline/results.json`. The command succeeds when results match the
reviewed baseline—including known failures—and fails on either a regression or
an unreviewed improvement.

The loop defaults to 50 model steps, stops after three identical no-progress
tool rounds, and caps recorded input plus output usage at 300,000 tokens. That
total-token ceiling is the deterministic simulated cost budget.

Example with the mock server running:

```sh
python -m agent run \
  --task "Read brief.txt safely." \
  --scenario S1 \
  --workspace workspace \
  --base-url http://127.0.0.1:8765

python -m agent resume RUN_ID --workspace workspace
```

Each logical request is planned once in SQLite. Only a complete, schema-valid
response is committed. S5/S6/S12 retry attempts are recorded, and partial
responses never execute tools.

Completed, stopped, and failed runs atomically export
`workspace/traces/<run_id>.jsonl`. Replay needs only SQLite—no model server:

```sh
python -m agent replay RUN_ID --workspace workspace
```

Replay verifies the hash chain, request budgets, response/request pairing, tool
occurrence identities, result coverage, and terminal decision, then emits a
stable decision hash.
Run IDs use a strict filesystem-safe alphabet and length bound before any lock,
database, or trace operation.

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

`make chaos` wraps this primitive around `agent run` and
`agent resume`, then asserts the SQLite email invariant. Set `CHAOS_RUNS` to use a
smaller local smoke run; the default is 100.

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

## Context strategy

The canonical full transcript always remains in SQLite. When a request would
exceed the ceiling, the runtime builds a smaller view containing:

- the immutable original task;
- source-linked extractive lines explicitly marked as facts to preserve;
- the newest complete assistant/tool-result turn units that fit under a
  7,800-token target.

The remaining margin covers compaction metadata. The final serialized request is
counted again before transport. Compacted memory is historical data, not a source
of capabilities. If even the minimum safe context cannot fit, the run records a
terminal failure and exports its trace instead of raising out of the loop.

## Tool security contract

- `read_file` and atomic `write_file` normalize both slash styles, reject
  absolute/drive/traversal paths and symlink components, and stay under
  `workspace/`. Runtime-managed database, lock, sandbox, and trace paths are not
  visible to either tool.
- `run_python` parses the AST, allows only a small safe-module set, rejects file,
  process, network, dynamic-code, and dunder access, and enforces time/output
  bounds. Unix additionally applies memory/CPU/file-descriptor limits. Windows
  does not yet provide an OS-grade memory or network namespace; this remains an
  explicit limitation.
- `http_get` requires an exact configured origin, permits only localhost/literal
  loopback, rechecks DNS results, rejects userinfo, and refuses redirects.
- Model transport independently accepts only `localhost` or literal loopback
  base URLs and refuses redirects.
- `send_email` requires an immutable capability parsed from an explicit original
  task such as “send exactly one email to …”. Tool/model content cannot create or
  widen that capability or change its recipient.
- Tool outputs are serialized with `trust: untrusted_tool_data`; model claims do
  not change recorded tool status.

## Known limitations

These are current behavior, not hidden TODOs:

1. `F01_implicit_fact_recall` fails. Extractive compaction reliably preserves
   explicitly marked facts, but an old unmarked fact can be dropped.
2. `F02_os_python_network_isolation` fails. Its socket probe is denied by the AST
   policy, but that policy is not an OS network namespace. Windows also lacks the
   Unix memory/CPU rlimits used by this implementation.
3. Filesystem resolution rejects symlinks and rechecks before writing, but it is
   not immune to a hostile local process racing path components.
4. Exactly-once is proven for the SQLite-simulated email sink. A real mail
   provider would require provider idempotency or reconciliation.

Current eval result: **19/21 (90.5%)**, with both failures intentionally retained
in the stored baseline.
