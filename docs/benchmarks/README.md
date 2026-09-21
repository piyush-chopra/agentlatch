# Contention smoke benchmark

Measured on 2026-09-21 with Python 3.12.12, macOS arm64. Each run starts eight concurrent workflows with two independent increment tasks each, all targeting one shared resource. ScriptedPlanner deliberately waits 120ms during planning. Twenty attempts per task allow conflict retries. No LLM calls are made.

| Backend | Completed workflows | Final / expected count | Elapsed | Committed operations/s | Workflow p50 | Workflow p95 |
| --- | --- | --- | --- | --- | --- | --- |
| SQLite | 8 / 8 | 16 / 16 | 2.755s | 5.807 | 2.090s | 2.752s |
| PostgreSQL | 8 / 8 | 16 / 16 | 3.479s | 4.600 | 2.447s | 3.413s |

Raw measurements: [SQLite](sqlite-smoke.json), [PostgreSQL](postgres-smoke.json). p95 uses the nearest-rank method; with only eight samples it is the maximum observed workflow latency. This small, deliberately contended, single-machine workload measures the complete simulated execution path. It is **not** a database capacity comparison, production sizing recommendation, LLM throughput measurement, or statistically robust latency study.

```sh
uv run --no-sync python scripts/benchmark.py
uv run --no-sync python scripts/benchmark.py --db 'postgresql://USER:PASSWORD@localhost:5432/benchdb'
```

Use a dedicated database: the script creates namespaced resources and workflow history. SQLite defaults to a temporary database. Publication of real production benchmarks requires realistic workload distributions, long runs, resource telemetry, load saturation curves, failure injection, and replicated deployment measurements.
