from __future__ import annotations

import asyncio
import copy
import json
import os
import sys

from .models import Plan, Write


class ScriptedPlanner:
    """Explicit offline simulation. Uses the exact same commit path as CrewAI."""

    async def plan(self, task, snapshot):
        await asyncio.sleep(0.03 if task.action == "migrate" else 0.12)
        writes = []
        for key in task.writes:
            value = copy.deepcopy(snapshot[key].value)
            schema = None
            if task.action == "increment":
                field = task.field
                if field not in value and field == "count" and "available" in value:
                    field = "available"
                value[field] = value.get(field, 0) + task.amount
            elif task.action == "migrate":
                value["available"] = value.pop("count")
                schema = {
                    "type": "object",
                    "properties": {"available": {"type": "integer", "minimum": 0}},
                    "required": ["available"],
                    "additionalProperties": False,
                }
            elif task.action == "copy":
                source = next(v for k, v in snapshot.items() if k not in task.writes)
                value = {"observed": source.value}
            writes.append(Write(key=key, value=value, json_schema=schema))
        return Plan(
            writes=writes,
            rationale=f"Offline simulation: {task.action} after reading current schema",
        )


class CrewPlanner:
    """One isolated CrewAI worker per attempt; cancellation terminates inference locally."""

    async def plan(self, task, snapshot):
        payload = json.dumps(
            {
                "task": task.model_dump(),
                "snapshot": {k: r.model_dump() for k, r in snapshot.items()},
            }
        )
        env = {**os.environ, "OTEL_SDK_DISABLED": "true", "CREWAI_TELEMETRY_DISABLED": "true"}
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "agentlatch.crew_worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        try:
            stdout, _ = await process.communicate(payload.encode())
            if process.returncode:
                raise RuntimeError(
                    "CrewAI worker failed; verify model availability and credentials"
                )
            marker = b"AGENTLATCH_RESULT="
            results = [
                line[len(marker) :] for line in stdout.splitlines() if line.startswith(marker)
            ]
            if len(results) != 1:
                raise ValueError("CrewAI did not return a structured plan")
            return Plan.model_validate_json(results[0])
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except TimeoutError:
                    process.kill()
                    await process.wait()
