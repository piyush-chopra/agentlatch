"""Reproducible local contention smoke benchmark; no model calls or external effects."""

import argparse
import asyncio
import json
import math
import platform
import statistics
import tempfile
import time
import uuid
from datetime import UTC, datetime

from agentlatch.coordinator import Coordinator
from agentlatch.engine import Engine
from agentlatch.models import ResourceCreate, TaskSpec, WorkflowSpec
from agentlatch.planners import ScriptedPlanner


async def measure(path, runs, agents):
    c = Coordinator(path)
    key = "benchmark-" + uuid.uuid4().hex[:10]
    c.create_resource(ResourceCreate(key=key, value={"count": 0}))
    latencies = []

    async def run(index):
        spec = WorkflowSpec(
            name=f"Contention {index}",
            tasks=[
                TaskSpec(
                    id=f"agent-{n}",
                    role="Incrementer",
                    goal="Add one",
                    reads=[key],
                    writes=[key],
                    action="increment",
                    max_attempts=20,
                )
                for n in range(agents)
            ],
            max_steps=1000,
        )
        started = time.perf_counter()
        result = await Engine(c, ScriptedPlanner()).run(spec)
        latencies.append(time.perf_counter() - started)
        return result

    start = time.perf_counter()
    results = await asyncio.gather(*(run(i) for i in range(runs)))
    elapsed = time.perf_counter() - start
    completed = sum(r["status"] == "completed" for r in results)
    value = c.snapshot([key])[key].value["count"]
    report = {
        "captured_at": datetime.now(UTC).isoformat(),
        "backend": "postgresql" if c.storage.postgres else "sqlite",
        "python": platform.python_version(),
        "platform": platform.system() + " " + platform.machine(),
        "workload": "Concurrent workflows incrementing one shared resource; ScriptedPlanner includes 120ms delay",
        "runs": runs,
        "agents_per_run": agents,
        "completed_runs": completed,
        "expected_value": runs * agents,
        "actual_value": value,
        "elapsed_seconds": round(elapsed, 3),
        "commits_per_second": round(value / elapsed, 3),
        "workflow_p50_seconds": round(statistics.median(latencies), 3),
        "workflow_p95_seconds": round(
            sorted(latencies)[max(0, math.ceil(len(latencies) * 0.95) - 1)], 3
        ),
        "scope": "Single machine, one client process, small smoke workload; not production capacity or LLM throughput",
    }
    print(json.dumps(report, indent=2))
    if completed != runs or value != runs * agents:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db")
    parser.add_argument("--runs", type=int, default=8)
    parser.add_argument("--agents", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.runs <= 100 or not 1 <= args.agents <= 20:
        parser.error("Use runs 1–100 and agents 1–20")
    with tempfile.TemporaryDirectory(prefix="agentlatch-bench-") as directory:
        asyncio.run(measure(args.db or directory + "/state.db", args.runs, args.agents))
