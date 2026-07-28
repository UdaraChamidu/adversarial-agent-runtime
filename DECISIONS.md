# Architecture Decisions

This file is intentionally concise because the Part A limit is 1,000 words.

## Initial decisions

**Python and standard-library-first.** Python provides SQLite, HTTP, process,
concurrency, and test primitives without hiding the runtime behavior the exercise
is intended to expose. Part A runtime dependencies will be limited to the
standard library plus an allowed test runner.

**Challenge infrastructure is a separate contract.** Although the written brief
calls the mock server and harness supplied, the publisher clarified that they
must also be built here. Their protocol and scenario tests will be frozen before
agent implementation so the tests cannot be weakened to fit the agent.

**The tokenizer favors determinism over vendor imitation.** It counts stable
Unicode spans in four-byte units and canonicalizes JSON before counting. The same
module runs in the server and agent, and the server independently rejects inputs
above 8,000. Scenario `.yaml` files use JSON syntax—valid YAML 1.2—so Part A does
not acquire a YAML parser dependency.

**SQLite is the durability boundary.** The event log and simulated email sink
share one database. A logical email row, idempotency record, tool result, and
completion event can therefore commit atomically. This proves exactly-once for
the simulated sink; a real provider would additionally need a provider-supported
idempotency key or reconciliation.

**Security is enforced outside the model.** Prompt wording is not a security
boundary. Filesystem confinement, URL allow-listing, process limits, tool schema
validation, and email capabilities will be deterministic runtime checks.

**Part B waits for a measured Part A baseline.** Framework code will not begin
until Part A has recorded scenario, chaos, security, context, and replay results.
That makes the later comparison evidence-based instead of speculative.
