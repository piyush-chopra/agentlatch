# Delivered scope and remaining deployment work

## Implemented

- Python asynchronous engine, CrewAI specialist/reviewer workers, React console.
- Versioned snapshots, atomic multi-resource commits, leases, fencing, receipts, bounded retries and loops.
- Conservation rules, numeric bounds/maximum deltas, approved schema hashes.
- SQLite and optional PostgreSQL storage with serialized mutation transactions and additive schema initialization.
- Durable run mode, scheduler claims, heartbeat, generation fencing, automatic recovery, terminal task outcomes.
- Versioned message contracts, atomic publication/consumption, bounded delivery claims, dead letters.
- Transactional outbox and explicitly configured HTTP effect dispatcher with stable idempotency keys.
- Reliability console, API readiness/metrics, scripted message handoff demonstration.
- Competing scheduler tests, real process-kill tests before/after commit, stale-owner tests, delivery retry tests.
- Local contention smoke measurements and PostgreSQL CI configuration.

## Operational acceptance still required

The code implements the next reliability phase. It is not a production-readiness certification. Before enterprise deployment, validate PostgreSQL failover, network partitions, backup/restore, sustained load and tail latency under the real workload, downstream effect deduplication, and operational ownership.

## Further product scope

- Per-agent identity/capabilities, tenant isolation, request/model global quotas, event archival and tamper-evident audit controls.
- Broader domain validators, contractual migration compatibility, semantic adversarial evaluations.
- Deployment-specific compensations, operator-driven dead-letter redrive, cross-database orchestration.
- Higher-throughput scheduler/locking strategies and true event-sourced replay.
- Model configuration pinned per workflow/agent rather than global server configuration.

## Current limits

PostgreSQL coordinator mutations use one shared advisory transaction lock. SQLite is local storage. Scheduling uses task waves and one owning scheduler per run. Delivery is at least once and unordered; receivers must deduplicate. Whole workflows may partially succeed and cancellation does not undo committed external effects. Protection applies only to declared dependencies and coordinator-managed writes. Schemas and domain rules enforce configured properties, not arbitrary semantic truth. See [reliability design](reliability.md).
