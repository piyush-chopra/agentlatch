# Operations and verification

## Local setup

Use Python 3.12/3.13 and Node 22.12+. The repository pins Python 3.12; `uv.lock` and `frontend/package-lock.json` fix dependencies.

```sh
uv sync --group dev
cd frontend
npm ci
npm run build
cd ..
uv run agentlatch serve
```

The server binds to `127.0.0.1:8000` by default. Persistent data lives in `.agentlatch/state.db`. Change it with `--db /path/to/state.db` before the CLI subcommand, or `AGENTLATCH_DB`. For frontend development, run `npm run dev` in `frontend/`; its API proxy points to port 8000.

## Docker

```sh
docker compose up --build
```

The default container includes the offline runtime and serves the built React console. Data persists in a named volume. To include CrewAI, build with `docker compose build --build-arg INSTALL_CREW=true`, then configure provider environment variables. A container's localhost is not the host machine; use your platform's host gateway address for host Ollama. Docker packaging is provided but requires local Docker to validate on your platform.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| AGENTLATCH_DB | .agentlatch/state.db | SQLite path or PostgreSQL URI |
| AGENTLATCH_API_TOKEN | empty | Optional workspace-wide API bearer token |
| AGENTLATCH_ENABLE_CREW | false | Allow model-backed runs through HTTP |
| AGENTLATCH_MODEL | ollama_chat/gemma4:31b-cloud | CrewAI model identifier |
| AGENTLATCH_LOCAL_ONLY | false | Reject cloud tags and non-loopback Ollama endpoints when true |
| AGENTLATCH_BASE_URL | provider default | Model endpoint override |
| AGENTLATCH_API_KEY_ENV | prefix-based mapping | Name of key environment variable |

## Before exposing the server

This is a trusted-workspace MVP. Configure a long bearer token, TLS via a reverse proxy, request-size limits and rate limits, and a process/resource budget before network exposure. Do not expose an unauthenticated instance. There is no per-agent RBAC, multi-tenant isolation, global model concurrency quota, or administrative audit protection against direct filesystem changes. Low-level protocol clients share administrative trust.

Shared state may contain sensitive business data and is visible to clients with the workspace token. Crew mode sends its snapshot to the chosen model provider. Do not put secrets in resource values. Keep `.env`, SQLite files, and model credentials out of Git.

## Tests and builds

```sh
uv run pytest -q
uv run ruff check .
npm ci --prefix frontend
npm run build --prefix frontend
```

Tests exercise lost-update prevention, schema version staleness, read-set write-skew rejection, atomic rollback, duplicate delivery, expiry/fencing, nonpartial leases, competing processes, concurrent budget reservation, cancellation/deadlines, cycle rejection, transition loops, engine retry/resume, API authentication, and event cursors. CrewAI adapter tests use deterministic fake model output when available; live cloud calls require configured credentials and are separate from offline tests.

CI runs Python 3.12 and 3.13 tests/lint and the React TypeScript/production build. No external model credentials are required. Build verification is not a load test or a formal correctness proof.

## Recovery runbook

| Symptom | Action |
| --- | --- |
| Abrupt crash during execution | Restart a compatible scheduler against the same store; enrolled runs recover after claim expiry, skipping committed tasks |
| Workflow is cancelled/failed/completed | It is terminal; inspect events before intentionally starting a new ID |
| Resource lease remains after worker death | Wait for expiry; a new token fences the old holder |
| Repeated version conflicts | Reduce shared write contention, add real dependencies, or split resources |
| sqlite database locked | Check long transactions and local storage; do not run on network filesystems |
| planner_error | Confirm optional dependencies, API key, model ID, endpoint, and provider quota |
| schema_violation | Compare plan values with the current JSON Schema; do not bypass validation |
| loop_detected | Inspect repetitive goals/transitions; change the workflow rather than blindly raising limits |
| React console absent | Build `frontend`; then restart the server so the assets mount is registered |
| 401 in console | Enter the server bearer token in Connection settings |
| CrewAI run rejected | Enable AGENTLATCH_ENABLE_CREW and restart the server |

SQLite deadlines and expiry use host wall clock; keep it stable. PostgreSQL uses the database clock for coordination checks. A late worker still needs current versions and the current fencing token; token checks are independent of its local clock.

## Backups and retention

Use SQLite's online backup API or stop the process before copying the database. Do not copy only the main `.db` while active WAL transactions may exist. A backup contains resources, receipts, budgets, leases, and events; protect it like production data. Restoring old backups while old workers remain alive can regress token history: stop all workers before restore. No automatic retention or pruning is implemented. Event and receipt tables grow over time; design archival policies before long-running production use.

## Observability

`/health` checks process readiness only. `/api/state` gives event totals, active leases, and recent runs. `/api/events` supports cursor-based export. Events are an operational audit log, not a tamper-evident ledger or complete replay source. `/ready` checks database reachability. Authenticated `/api/metrics` reports workflow/delivery counts and local scheduler counters; `/api/executions` and `/api/deliveries` expose operational state. Additive schema generation and small contention benchmarks are implemented; distributed tracing, retention, general migration tooling, and production scale/failover testing remain further work.

## PostgreSQL and scheduler replicas

```sh
uv sync --extra postgres --extra crew --group dev
export AGENTLATCH_DB='postgresql://USER:PASSWORD@HOST:5432/agentlatch'
uv run --no-sync agentlatch serve
# Additional process/host, same authoritative database and model configuration:
uv run --no-sync agentlatch worker
```

Use a least-privilege database role and TLS where appropriate. The URI is server configuration and is never returned to React. The optional `compose.postgres.yaml` uses PostgreSQL 17; provide URL-safe `POSTGRES_PASSWORD` and `AGENTLATCH_API_TOKEN`, then run `docker compose -f compose.postgres.yaml up --build`. Its database is an independent store, not an automatic migration of SQLite data. The sample omits a dispatcher so queued effects are not sent accidentally.

`AGENTLATCH_MAX_RUNS` limits active runs per scheduler process. `AGENTLATCH_SCHEDULER_ENABLED=false` makes the API enqueue only. Crew scheduling requires `AGENTLATCH_ENABLE_CREW=true`; model selection is still global and must be consistent across replicas. See [reliability](reliability.md) for backup, upgrade, message/effect, and recovery procedures.

## Reliability tests and benchmark

```sh
# Use a dedicated test database; the suite creates and drops isolated schemas.
AGENTLATCH_TEST_POSTGRES='postgresql://USER:PASSWORD@localhost:5432/testdb' uv run --no-sync pytest -q
uv run --no-sync python scripts/benchmark.py
```

Results and scope are recorded in [benchmarks](benchmarks/README.md). The test PostgreSQL role needs schema creation privileges. No LLM calls occur in these tests except separately invoked live verification.
