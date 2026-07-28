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

The repository scaffold and command contracts are covered by five passing smoke
tests. Runtime behavior is not yet implemented, and no correctness or security
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
