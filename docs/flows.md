# Flow-level diagrams (FLDs)

## 1. End-to-end workflow

```mermaid
flowchart TD
    A[CLI / console submits workflow] --> B{Valid acyclic DAG?}
    B -- No --> Reject[Reject request]
    B -- Yes --> C[Persist run and initialize missing resources]
    C --> D[Select dependency-ready tasks]
    D --> E{Committed receipt exists?}
    E -- Yes --> Skip[Reuse result]
    E -- No --> F[Reserve persisted attempt budget]
    F --> G[Read authoritative snapshot]
    G --> H[Agent produces structured plan]
    H --> I[Check declared write scope]
    I --> J[Acquire entire read set lease]
    J --> K{Atomic commit valid?}
    K -- Yes --> L[Persist data + receipt + event]
    K -- Conflict --> M{Retry budget remains?}
    M -- Yes --> G2[Back off and start a new attempt]
    G2 --> F
    M -- No --> Fail[Mark task failed]
    K -- Invalid / expired --> Fail
    L --> Release[Release lease]
    Skip --> Next[Advance DAG]
    Release --> Next
    Fail --> Next
    Next --> Done{All tasks resolved?}
    Done -- No --> D
    Done -- Yes --> Finish[Complete or fail run]
```

On any attempt exit, the engine releases its lease and abandons an uncommitted attempt. Dependencies of failed tasks are skipped. Independent tasks may still finish; there is no whole-workflow rollback.

## 2. Schema migration race

```mermaid
sequenceDiagram
    participant A as Schema architect
    participant B as Inventory agent
    participant C as Coordinator
    participant DB as Shared state
    A->>C: Snapshot inventory
    C-->>A: count=10, data v1 / schema v1
    B->>C: Snapshot inventory
    C-->>B: count=10, data v1 / schema v1
    A->>C: Lease + propose available=10 and new schema
    C->>DB: Atomic migration
    DB-->>C: data v2 / schema v2
    C-->>A: Accepted receipt
    B->>C: Lease + propose count=11 with v1/s1
    C-->>B: version_conflict; no write
    B->>C: Fresh snapshot
    C-->>B: available=10, v2/s2
    B->>C: New budgeted attempt; available=11 with v2/s2
    C->>DB: Commit data v3 / schema v2
    C-->>B: Accepted receipt
```

The offline schema demo deliberately delays the inventory agent long enough to reproduce this conflict. Real model timing varies; a real CrewAI run may complete without colliding.

## 3. Lease expiry and fencing

```mermaid
sequenceDiagram
    participant Old as Old worker
    participant C as Coordinator
    participant New as New worker
    Old->>C: Acquire a,b
    C-->>Old: token=41, expires=t
    Note over Old,C: Old worker pauses beyond t
    New->>C: Acquire a,b
    C-->>New: token=42
    Old->>C: Commit using token=41
    C-->>Old: stale_lease
    Old->>C: Release token=41
    Note over C: token=42 remains intact
    New->>C: Commit using token=42 + current versions
    C-->>New: Accepted
```

## 4. Crash recovery / idempotency

```mermaid
flowchart LR
    Start[Attempt running] --> Tx[Commit transaction]
    Tx --> Crash[Worker crashes before receiving result]
    Crash --> Resume[Resume same run ID and spec]
    Resume --> Receipt{Receipt present?}
    Receipt -- Yes --> Reuse[Skip model call and reuse committed task]
    Receipt -- No --> Budget{Budget and deadline remain?}
    Budget -- Yes --> Retry[Fresh snapshot and new attempt]
    Budget -- No --> Failed[Fail run]
```

## 5. Cancellation

```mermaid
sequenceDiagram
    participant U as Console
    participant API as FastAPI
    participant C as Coordinator
    participant W as CrewAI worker
    U->>API: POST run/cancel
    API->>C: Persist terminal cancelled state
    API->>W: Cancel task / terminate subprocess
    W-->>API: Exits (kill after grace period if necessary)
    Note over C: Future commits fail workflow_closed
    API-->>U: cancelled
```

Already committed operations remain committed. Terminating the local worker cannot guarantee that a remote provider cancels an already submitted generation or stops billing it.
