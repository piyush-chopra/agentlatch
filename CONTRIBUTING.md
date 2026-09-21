# Contributing to AgentLatch

Repository: https://github.com/piyush-chopra/agentlatch

Use Python 3.12/3.13 and Node 22.12+. Install `uv sync --group dev` and `npm ci --prefix frontend`. Optional CrewAI development uses `uv sync --extra crew --group dev`.

Before submitting a change:

1. Explain the concrete failure or behavior being changed.
2. Keep model reasoning outside coordinator transactions.
3. Include regression tests for concurrency, recovery, or protocol changes.
4. Run `uv run pytest -q`, `uv run ruff check .`, and `npm run build --prefix frontend`.
5. Update architecture/API/operations docs when contracts or guarantees change.
6. Submit a focused pull request with actual validation results and limitations.

Use small commits describing completed behavior, such as `feat: reject incompatible schema migrations` or `test: exercise stale fenced writers`. Keep real commit timestamps. Do not commit keys, `.env`, SQLite databases, build caches, or model artifacts. Do not add direct agent mutation tools that bypass the coordinator.

Public issues are appropriate for reproducible bugs and design proposals. Avoid putting credentials or private business state in logs or examples. For sensitive reports, use GitHub's private vulnerability reporting if enabled, or contact the repository owner privately.
