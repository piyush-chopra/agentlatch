"""Replica-safe polling scheduler: the database claim, not this process, owns each run."""

import asyncio
import json
import os

from .engine import Engine
from .models import WorkflowSpec
from .planners import CrewPlanner, ScriptedPlanner


class Scheduler:
    def __init__(self, coordinator, max_runs=4, poll_seconds=0.25, claim_ttl=30):
        if max_runs < 1 or poll_seconds <= 0 or not 0.05 <= claim_ttl <= 300:
            raise ValueError("Invalid scheduler limits")
        self.coordinator = coordinator
        self.max_runs = max_runs
        self.poll_seconds = poll_seconds
        self.claim_ttl = claim_ttl
        self.jobs = {}
        self.task = None
        self.errors = 0

    def start(self):
        self.task = asyncio.create_task(self.loop())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        tasks = list(self.jobs.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def work(self, row):
        planner = CrewPlanner() if row["mode"] == "crew" else ScriptedPlanner()
        try:
            await Engine(self.coordinator, planner).run(
                WorkflowSpec.model_validate(json.loads(row["specification"])),
                row["id"],
                mode=row["mode"],
                claim_ttl=self.claim_ttl,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # No provider text, SQL, or credentials in logs. A surviving replica can retry.
            self.errors += 1

    async def loop(self):
        while True:
            try:
                await asyncio.to_thread(self.coordinator.expire_runs)
                rows = await asyncio.to_thread(self.coordinator.pending_runs)
                for row in rows:
                    if len(self.jobs) >= self.max_runs:
                        break
                    if row["id"] in self.jobs:
                        continue
                    if (
                        row["mode"] == "crew"
                        and os.getenv("AGENTLATCH_ENABLE_CREW", "false").lower() != "true"
                    ):
                        continue
                    task = asyncio.create_task(self.work(row))
                    self.jobs[row["id"]] = task
                    task.add_done_callback(lambda done, key=row["id"]: self.jobs.pop(key, None))
            except asyncio.CancelledError:
                raise
            except Exception:
                self.errors += 1
            await asyncio.sleep(self.poll_seconds)
