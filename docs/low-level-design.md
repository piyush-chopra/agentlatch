# Low-level design

## Source layout

```text
src/agentlatch/
  models.py          Strict Pydantic transport and workflow models
  coordinator.py     SQLite transactions, leases, versions, receipts, audit log
  policies.py        Deterministic workflow-scoped conservation invariants
  engine.py          DAG scheduling and bounded attempt/retry execution
  planners.py        Offline simulator and cancellable subprocess adapter
  crew_worker.py     CrewAI Agent / Task / Crew construction and LLM configuration
  api.py             FastAPI routes and background run lifecycle
  cli.py             serve / demo / run / state commands
  demo.py            Reproducible, namespaced conflict scenarios
  static/            Generated React production build
frontend/src/
  App.tsx            Console, polling, run creation, state and event inspection
  styles.css         Responsive visual system and reduced-motion handling
```

## Data model / ER diagram

```mermaid
erDiagram
    WORKFLOWS ||--o{ ATTEMPTS : budgets
    WORKFLOWS ||--o{ OPERATIONS : records
    WORKFLOWS ||--o{ TRANSITIONS : limits
    WORKFLOWS ||--o{ EVENTS : audits
    RESOURCES ||--o| LEASES : fenced_by
    LEASE_SEQUENCE ||--o{ LEASES : issues
    WORKFLOWS { string id PK string status int steps int max_steps float deadline int repeat_limit string specification float created_at }
    RESOURCES { string key PK string value string json_schema int version int schema_version }
    ATTEMPTS { int id PK string workflow_id string operation_id string owner string status float created_at }
    OPERATIONS { string workflow_id PK string operation_id PK string fingerprint string result }
    TRANSITIONS { string workflow_id PK string fingerprint PK int count }
    LEASES { string resource PK string owner int token float expires_at }
    LEASE_SEQUENCE { int token PK }
    EVENTS { int sequence PK string workflow_id string kind string payload float created_at }
```

Relationships are enforced by coordinator code; the MVP DDL does not declare foreign keys. JSON values are stored as canonical JSON text. Timestamps are UTC Unix seconds. `lease_sequence.token` and `events.sequence` use SQLite AUTOINCREMENT. Never reset the sequence while clients can hold old tokens.

| Table | Important behavior |
| --- | --- |
| resources | Initialized at data/schema version 1; every accepted write increments data version, even a no-op |
| workflows | Immutable identity/specification check, persisted attempt count, absolute deadline |
| leases | One row per resource, token shared by an atomic acquisition; expired rows are overwritten |
| lease_sequence | Monotonic generations across releases and process restarts |
| attempts | Pending, committed, or abandoned; reserved before inference |
| operations | Unique logical operation receipt and payload fingerprint |
| transitions | Version-independent transition fingerprint count per workflow |
| events | Ordered append-only application audit history; not a full event-sourced state reconstruction log |

## Commit algorithm

Inside one `BEGIN IMMEDIATE` transaction:

1. Hash canonical read stamps and plan. If a receipt exists, return it when the hash matches; otherwise reject ID reuse.
2. Check workflow existence, running status, and absolute deadline.
3. Check that the supplied attempt is pending and matches workflow, operation, and owner.
4. Require all write keys to appear in the read set.
5. For every read key, check lease owner, token, expiry, and both resource versions.
6. Validate all proposed values against current or proposed JSON Schema before changing anything.
7. Enforce configured conservation invariants over current and fully proposed state. Hash input state and proposed output independently of version counters. Reject excessive repeats.
8. Update resources and version counters. Insert receipt and transition count, mark attempt committed, append event.
9. Commit the transaction. On error, roll back; record a rejection in a separate transaction.

A crash after commit but before delivery is safe: the next identical delivery returns the durable receipt. A crash before commit leaves no partial data changes. A crash before the rejection event can omit that event; it cannot partially apply the rejected write.

## State machines

```mermaid
stateDiagram-v2
    [*] --> running: create workflow
    running --> completed: all tasks committed
    running --> failed: error / budget / deadline / failed dependency
    running --> cancelled: cancellation / graceful shutdown
    completed --> [*]
    failed --> [*]
    cancelled --> [*]
```

```mermaid
stateDiagram-v2
    [*] --> pending: reserve attempt and charge budget
    pending --> committed: accepted atomic commit
    pending --> abandoned: failed attempt / cancellation
    committed --> [*]
    abandoned --> [*]
```

A process killed abruptly can leave a workflow `running` and an attempt `pending`. Reusing the same run ID and specification skips committed operations and charges new attempts for unfinished tasks. Remaining task and workflow budgets still apply. Gracefully cancelled/failed runs are terminal; use a new ID to intentionally start a new execution.

## Loop and deadlock controls

- DAG validation rejects unknown dependencies and cycles before execution.
- No agent-held resource lock survives inference.
- Lease acquisition is all-or-nothing, so resources cannot form a hold-and-wait cycle in this protocol.
- Retries have per-task maximum attempts and a shared durable workflow step budget.
- Workflow deadlines are checked before planning and inside commit.
- Repeated transitions are rejected after `repeat_limit`; alternating repeated states are eventually stopped. Fresh but meaningless transitions are bounded by steps/deadline.
- CrewAI delegation is disabled; iteration/retry limits cap internal reasoning. The worker subprocess is terminated on runtime cancellation/timeout.

## Frontend behavior

The React console polls state and new events roughly every 1.2 seconds, using an event sequence cursor. It retains the latest 3,000 events in memory and displays up to 80 for a filter. The API retains the full event table until an operator archives it. Model credentials never reach React. UI assets are served by FastAPI after a Vite production build. Server state is authoritative; the UI does not optimistically mutate resource values.
