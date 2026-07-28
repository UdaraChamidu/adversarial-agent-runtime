# Time Log

The written brief budgets six hours for Part A and two hours for Part B. It
describes the mock server, tokenizer, and harness as supplied; the publisher later
clarified that this project must build them too. Infrastructure time is therefore
shown separately rather than hidden inside the Part A runtime budget.

Times use Asia/Colombo (UTC+05:30). Durations are active working time, rounded to
the nearest minute.

| Date | Area | Active time | Work and result |
|---|---|---:|---|
| 2026-07-27 | Planning | 0:18 | Read the brief, inventoried the empty repository, and produced the requirement/architecture plan. |
| 2026-07-28 | Infrastructure | 0:04 | Confirmed expanded scope, audited Python/SQLite/Git tooling, and created the tested M0 scaffold. |
| 2026-07-28 | Infrastructure | 0:08 | Implemented deterministic token counting, strict Messages request validation, the S1–S12 data catalog/engine, transport faults, and 19 additional tests. |
| 2026-07-28 | Infrastructure | 0:05 | Added seeded process-group termination, public and generated red-team corpora, the authorized S1 email fixture, and six harness tests. |
| 2026-07-28 | Part A | 0:04 | Implemented the append-only hash-chained SQLite store, pure run reducer, OS run lock, and atomic email effect with crash/concurrency tests. |

## Totals

| Budget area | Used | Cap |
|---|---:|---:|
| Challenge infrastructure and planning | 0:35 | Not specified in written brief |
| Part A runtime | 0:04 | 6:00 |
| Part B framework runtime | 0:00 | 2:00 |
