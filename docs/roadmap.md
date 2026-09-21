# Roadmap and current limits

## Delivered local MVP

- [x] Python coordinator and async execution engine.
- [x] React + TypeScript console and live event polling.
- [x] Versioned data/schema snapshots and atomic multi-resource commits.
- [x] All-resource leases, expiry, monotonic fencing tokens.
- [x] Durable operation receipts and crash-resume path.
- [x] DAG, step, attempt, deadline, and repeated-transition limits.
- [x] CrewAI structured proposal adapter; local/cloud provider configuration.
- [x] Reproducible offline schema and lost-update demonstrations.
- [x] API, CLI, tests, architecture/flow/LLD docs, and container/CI definitions.
- [x] Deterministic workflow conservation invariants with complete dependency checking.

## Next: stronger domain correctness

The protocol prevents stale writes and now enforces configured integer conservation rules. Extend deterministic domain policies (allowed migrations, richer cross-resource invariants, allowed numeric deltas), integration tests for write skew, and adversarial proposal evaluation. Policies must run within the same commit transaction against the fully proposed state. Model-generated policy exceptions must not override them.

## Next: durable distributed execution

Add PostgreSQL storage with migrations, explicit isolation and retry strategy, durable task claiming, scheduler fencing, per-run execution ownership, heartbeats, and safe multi-replica recovery. Validate through process-kill fault injection and competing scheduler tests. This must precede multi-host deployment.

## Next: external effects

Introduce an atomic transactional outbox. Require effect IDs and downstream idempotency; acknowledge delivery at least once. Design compensations for nontransactional actions. Never market end-to-end exactly-once behavior for arbitrary external APIs.

## Next: platform hardening

Per-agent identity and capabilities; tenant boundaries; request/model concurrency limits; validated configuration; bounded payloads; event archival; migration tooling; metrics/traces; load tests; backup/restore drills; provider evaluation suite; accessible frontend interaction tests.

## Known limitations

- One host and one API scheduler process; no distributed consensus/high availability.
- Task scheduling uses waves and is not throughput-optimized.
- Built-in provider selection is global, not per-agent.
- Schemas protect structure, not semantic truth, contractual compatibility, or business policy.
- Only declared dependencies are checked. External workers can omit reads if trusted protocol rules are violated.
- Entire workflows can partially succeed; there are no compensations or global rollback.
- Abrupt crashes can leave runs marked running until explicitly resumed or cancelled.
- No automatic lease renewal; planning occurs before lease acquisition.
- No true event-sourced replay or cross-database transaction.
- No live LLM success claim without an actual configured provider run.
- UI displays recent events; full history remains queryable through the cursor API.

## Acceptance criteria for production readiness

Demonstrate invariant preservation under competing processes and schedulers, abrupt kills at transaction boundaries, network partitions, duplicate requests, schema migration conflicts, provider timeouts, and database failover. Publish measured throughput, tail latency, recovery time, resource consumption, and documented operational ownership before calling the system production-ready.
