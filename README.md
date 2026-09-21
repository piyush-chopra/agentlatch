# AgentLatch

**Let agents reason. Let transactions coordinate.**

AgentLatch is an open-source Python execution engine and deterministic state coordinator for asynchronous AI agents, with a React + TypeScript operations console. CrewAI agents can use Ollama, OpenAI, Anthropic, Gemini, or compatible endpoints. An offline simulator reproduces concurrency failures without an API key.

**Status:** working local MVP, not a distributed production database or a proof of semantic correctness. All protected state changes must go through AgentLatch. Coordination uses established transactional techniques; the project applies them at the agent proposal/commit boundary.

## Quick start

Requirements: Python 3.12 or 3.13, [uv](https://docs.astral.sh/uv/), Node.js 22.12+.

```sh
uv sync --group dev
cd frontend
npm ci
npm run build
cd ..
uv run agentlatch serve
```

Open **http://127.0.0.1:8000** for the console and **http://127.0.0.1:8000/docs** for interactive API documentation. Click **Run scenario** to reproduce a schema migration racing with an inventory update. The event timeline shows the stale commit being rejected and the agent replanning against the new schema.

```sh
# Terminal-only demonstrations; no models or API keys needed
uv run agentlatch demo --scenario schema
uv run agentlatch demo --scenario race
uv run agentlatch run examples/inventory.json
uv run agentlatch state

# Verification
uv run pytest
uv run ruff check .
cd frontend && npm run build
```

## Real agents

```sh
uv sync --extra crew --group dev
cp .env.example .env
# Install/start Ollama separately, then:
ollama pull llama3.2
uv run --extra crew agentlatch demo --mode crew
```

Set `AGENTLATCH_MODEL` and the relevant provider key in `.env`. Set `AGENTLATCH_ENABLE_CREW=true` to enable CrewAI runs from the console. The CLI's explicit `--mode crew` also opts into model calls. See [provider setup](docs/providers.md) for Ollama, OpenAI, Anthropic, Gemini, and compatible local servers. No credentials are stored in the browser or database.

## What is implemented

- Consistent snapshots with data and schema version stamps; atomic multi-resource commits.
- All-or-nothing leases, monotonic fencing tokens, expiry, and stale-writer rejection.
- Durable idempotency receipts, event history, attempt budgets, deadlines, and repeated-transition limits.
- DAG validation, asynchronous independent tasks, bounded conflict retries with fresh snapshots, and recovery of interrupted runs.
- CrewAI agents producing typed proposals in isolated worker processes; no direct database tools.
- React console with demos, run inspection, resource inspection, timeline, custom JSON workflows, and cancellation.
- FastAPI coordinator protocol for external workers; optional bearer authentication.

## Documentation

Start at the [documentation index](docs/README.md).

| Document | Contents |
| --- | --- |
| [Architecture / HLD](docs/architecture.md) | Scope, components, trust boundary, deployment topology, guarantees |
| [Low-level design](docs/low-level-design.md) | Modules, database schema, state machine, transaction algorithm |
| [Flow diagrams](docs/flows.md) | Execution flow, race resolution, fencing, cancellation, recovery |
| [API](docs/api.md) | Endpoints, request fields, errors, external worker protocol |
| [Workflows](docs/workflows.md) | Specification, dependencies, extension guide, examples |
| [Providers](docs/providers.md) | Local models and cloud configuration |
| [Operations](docs/operations.md) | Installation, tests, Docker, security, recovery, troubleshooting |
| [Decisions](docs/decisions.md) | Design choices and alternatives |
| [Roadmap](docs/roadmap.md) | Delivered scope, known limits, production milestones |

## Development

```sh
# Terminal 1
uv run agentlatch serve
# Terminal 2
cd frontend && npm ci && npm run dev
```

Vite proxies `/api` and `/health` to port 8000. The production UI build is placed in the Python package's `static/` directory. See [CONTRIBUTING.md](CONTRIBUTING.md). Apache-2.0 licensed.
