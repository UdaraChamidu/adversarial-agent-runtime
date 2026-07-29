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

**Internal occurrence identity never trusts model IDs.** Tool occurrences derive
from run ID, committed response sequence, and block position; repeated external
`tool_use` IDs cannot collide. Events are append-only and hash-linked. A
cross-platform advisory file lock prevents concurrent resume owners and is
released by the OS after `kill -9`.

**Only complete responses become decisions.** The client buffers and validates
the whole HTTP body before the store accepts it. Logical request IDs, responses,
and tool results each have unique SQLite records, so resume retries an unfinished
boundary instead of guessing. Parallel tools execute concurrently but their
results commit in source order; email remains atomic at its earlier effect
boundary.

**Chaos verifies the public process boundary.** Each logical trial uses a new
workspace and run ID, kills the CLI process at a seeded random delay, then starts
a separate resume process. Success requires a completed reducer state, a valid
hash chain, and exactly one email row—not merely a zero exit code.

**Model prose cannot overwrite tool truth.** A final answer following a failed
tool is checked for success claims. If contradictory, the runtime records a
grounding correction and requests a corrected answer within the same limits. It
never changes the underlying failed result.

**Security is enforced outside the model.** Prompt wording is not a security
boundary. Filesystem confinement, URL allow-listing, process limits, tool schema
validation, and email capabilities will be deterministic runtime checks.

**Privileged intent comes only from the original task.** Email is disabled unless
that immutable task explicitly grants one send to a named address. The model and
tool data can never add a grant or change its recipient. File paths reject both
slash styles, drives, traversal, and symlinks. HTTP requires an exact loopback
origin and refuses redirects.

**Python is fail-closed but not a perfect Windows sandbox.** Code is AST-checked
against a small module allow-list and denied filesystem/network/dynamic access;
wall time and output are bounded, with Unix resource limits. Without a Windows
job object or container, OS-grade memory/network isolation remains unsafe and is
kept as a documented failing eval rather than overstated.

**Adversarial fixtures mix reviewability and variation.** Five readable red-team
payloads document the expected trust boundaries, while a seeded generator changes
wrappers, encodings, and privileged requests. The chaos primitive kills an OS
process group at seeded random delays. Deterministic seeds make failures
reproducible without making the runtime aware of the selected attack.

**Part B waits for a measured Part A baseline.** Framework code will not begin
until Part A has recorded scenario, chaos, security, context, and replay results.
That makes the later comparison evidence-based instead of speculative.
