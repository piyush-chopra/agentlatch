from __future__ import annotations

import asyncio
import random
import time
import uuid
from typing import Protocol

from .coordinator import Coordinator
from .errors import CoordinationError
from .models import Commit, LeaseRequest, Plan, Resource, WorkflowCreate, WorkflowSpec


class Planner(Protocol):
    async def plan(self, task, snapshot: dict[str, Resource]) -> Plan: ...


class Engine:
    def __init__(self, coordinator: Coordinator, planner: Planner, concurrency: int = 4):
        self.coordinator = coordinator
        self.planner = planner
        self.slots = asyncio.Semaphore(concurrency)

    async def run(self, spec: WorkflowSpec, run_id: str | None = None) -> dict:
        run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        c = self.coordinator
        workflow = c.create_workflow(
            WorkflowCreate(
                id=run_id,
                max_steps=spec.max_steps,
                timeout_seconds=spec.timeout_seconds,
                repeat_limit=spec.repeat_limit,
                specification=spec.model_dump(),
            )
        )
        if workflow["status"] != "running":
            return {"id": run_id, "status": workflow["status"], "tasks": {}}
        outcomes = {}
        pending = list(spec.tasks)
        try:
            # Bootstrap only absent resources; existing state is always authoritative.
            for resource in spec.resources:
                try:
                    c.create_resource(resource)
                except CoordinationError as exc:
                    if exc.code != "resource_exists":
                        raise
            while pending:
                ready = [
                    t
                    for t in pending
                    if all(outcomes.get(d, {}).get("status") == "completed" for d in t.depends_on)
                ]
                if not ready:
                    for task in pending:
                        outcomes[task.id] = {"status": "skipped", "code": "dependency_failed"}
                    break
                results = await asyncio.gather(*(self.execute(run_id, task) for task in ready))
                outcomes.update(zip((t.id for t in ready), results, strict=True))
                ready_ids = {t.id for t in ready}
                pending = [t for t in pending if t.id not in ready_ids]
            status = (
                "completed"
                if all(r["status"] == "completed" for r in outcomes.values())
                else "failed"
            )
            c.finish(run_id, status)
        except asyncio.CancelledError:
            c.finish(run_id, "cancelled")
            raise
        except Exception:
            c.finish(run_id, "failed")
            raise
        return {"id": run_id, "status": status, "tasks": outcomes}

    async def execute(self, run_id, task) -> dict:
        async with self.slots:
            return await self._execute(run_id, task)

    async def _execute(self, run_id, task) -> dict:
        c = self.coordinator
        cached = c.operation(run_id, task.id)
        if cached:
            return {"status": "completed", "cached": True, **cached}
        owner = f"{task.id[:100]}-{uuid.uuid4().hex[:10]}"
        last_error = {
            "code": "attempts_exhausted",
            "message": "Task exhausted its persisted attempt budget",
        }
        while c.attempt_count(run_id, task.id) < task.max_attempts:
            lease = None
            attempt_id = None
            try:
                attempt_id = c.begin_attempt(run_id, task.id, owner)
                snapshot = c.snapshot(task.reads)
                remaining = c.get_workflow(run_id)["deadline"] - time.time()
                if remaining <= 0:
                    raise CoordinationError("deadline_exceeded", "Workflow deadline has passed")
                async with asyncio.timeout(remaining):
                    plan = await self.planner.plan(task, snapshot)
                if {w.key for w in plan.writes} != set(task.writes):
                    raise CoordinationError(
                        "scope_violation",
                        "Plan must write exactly the resources declared by the task",
                        422,
                    )
                # No locks during inference. The entire read set is fenced just for commit.
                lease = c.acquire(LeaseRequest(owner=owner, resources=task.reads))
                result = c.commit(
                    Commit(
                        workflow_id=run_id,
                        operation_id=task.id,
                        owner=owner,
                        lease_token=lease.token,
                        reads={k: r.stamp for k, r in snapshot.items()},
                        plan=plan,
                        attempt_id=attempt_id,
                    )
                )
                return {"status": "completed", **result}
            except CoordinationError as exc:
                last_error = {"code": exc.code, "message": exc.message}
                retryable = exc.code in {"version_conflict", "lease_busy", "stale_lease"}
            except TimeoutError:
                last_error = {
                    "code": "deadline_exceeded",
                    "message": "Planner exceeded the workflow deadline",
                }
                retryable = False
            except Exception as exc:
                # Do not persist provider exception text: it may contain request credentials.
                last_error = {
                    "code": "planner_error",
                    "message": f"Planner failed ({type(exc).__name__}); check provider configuration",
                }
                retryable = False
            finally:
                if lease:
                    c.release(owner, lease.token)
                if attempt_id:
                    c.abandon_attempt(attempt_id)
            c.record(
                run_id,
                "task_retry" if retryable else "task_failed",
                {"task_id": task.id, **last_error},
            )
            if not retryable:
                break
            # Jitter reduces collisions; state validity never depends on retry timing.
            await asyncio.sleep(random.uniform(0.02, 0.12))
        return {"status": "failed", **last_error}
