# Architecture decision records

## ADR-001 — Deterministic commit boundary

**Accepted.** Agents return typed proposals; code owns synchronization. An LLM referee cannot provide transactional guarantees because its outputs are nondeterministic and it can itself read stale state. Models are useful for replanning after a deterministic rejection.

## ADR-002 — Python backend, React frontend

**Accepted by project requirement.** FastAPI/Pydantic expose a typed Python protocol; asyncio coordinates CrewAI workers. React + TypeScript provides a separate operations console, built into Python-served static assets. No Node service is needed in the deployed offline runtime.

## ADR-003 — SQLite first

**Accepted for MVP.** WAL plus short immediate transactions provides a reproducible, low-setup, cross-process local coordinator. The v0.2 extension below adds PostgreSQL, additive schema initialization, and a durable scheduler. Production failover validation is still separate. Adding Redis locks alone would not replace database version checks or atomic receipts.

## ADR-004 — Optimistic planning plus fenced commit leases

**Accepted.** Do not hold locks across slow model calls. Read first; plan; atomically lease the full dependency set; validate versions; commit. Leases expose explicit ownership/recovery semantics, while optimistic version checks remain necessary. In the original SQLite backend, transactions alone already serialize commits; leases provide an explicit protocol boundary for workers.

## ADR-005 — No exactly-once claims for external side effects

**Accepted.** Resource changes and receipts are atomic inside one coordinator database. External payments, emails, migrations, and HTTP actions are not included. The v0.2 outbox supplies delivery IDs and fencing; downstream idempotency and application-specific compensation remain integration responsibilities. Do not add side-effecting CrewAI tools without that design.

## ADR-006 — Durable bounded autonomy

**Accepted.** Persist attempts, deadlines, transition counts, and operation receipts. A restart must not reset an agent's budget. The engine disables recursive delegation and runs model workers in subprocesses so a hung call can be terminated locally.

## ADR-007 — Optional inference dependencies

**Accepted.** Coordination tests and scripted demonstrations must run without a provider account or a large model download. CrewAI and provider extras are optional. Offline simulation is labeled explicitly in UI and docs and uses the real coordinator, not a mocked commit path.

## ADR-008 — Honest commit history

**Accepted.** Canonical source is `piyush-chopra/agentlatch`. Use small, meaningful commits as implementation progresses. Commit dates reflect actual work; future-dated or fabricated backdated history is not part of the workflow.

## ADR: shared PostgreSQL and durable ownership

Accepted for v0.2. SQLite remains the zero-service local option. PostgreSQL coordinator mutations acquire one transaction advisory lock to preserve the original serialized commit model across replicas. This trades write throughput for a small, testable safety boundary. Run ownership is a separate expiring, generation-fenced claim; models never hold the storage transaction.

## ADR: atomic outbox, at-least-once effects

Accepted for v0.2. Publish envelopes and consume messages in the state commit transaction. Use stable delivery IDs and fenced visibility leases. External receivers own durable deduplication because a remote side effect cannot share the local database transaction. URLs are operator-configured destination mappings, never model-chosen network locations.
