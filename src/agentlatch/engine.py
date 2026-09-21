from __future__ import annotations

import asyncio
import random
import uuid
from typing import Protocol

from .coordinator import Coordinator
from .errors import CoordinationError
from .models import Commit, DeliveryAck, LeaseRequest, Plan, Resource, WorkflowCreate, WorkflowSpec


class Planner(Protocol):
    async def plan(self, task, snapshot: dict[str, Resource]) -> Plan: ...


class Engine:
    def __init__(self, coordinator: Coordinator, planner: Planner, concurrency: int = 4):
        self.coordinator = coordinator
        self.planner = planner
        self.execution_tokens = {}
        self.slots = asyncio.Semaphore(concurrency)

    async def run(
        self,
        spec: WorkflowSpec,
        run_id: str | None = None,
        mode: str = "scripted",
        claim_ttl: float = 30,
    ) -> dict:
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
        c.enqueue(run_id, mode)
        owner = f"scheduler-{uuid.uuid4().hex}"
        try:
            generation = c.claim_run(run_id, owner, claim_ttl)
        except CoordinationError as exc:
            if exc.code == "deadline_exceeded":
                c.expire_runs()
                return {"id": run_id, "status": c.get_workflow(run_id)["status"], "tasks": {}}
            if exc.code == "workflow_closed":
                return {"id": run_id, "status": c.get_workflow(run_id)["status"], "tasks": {}}
            raise
        self.execution_tokens[run_id] = generation
        parent = asyncio.current_task()

        async def heartbeat():
            while True:
                await asyncio.sleep(claim_ttl / 3)
                try:
                    await asyncio.to_thread(c.renew_run, run_id, owner, generation, claim_ttl)
                except Exception:
                    parent.cancel()
                    return

        keeper = asyncio.create_task(heartbeat())
        outcomes = c.task_outcomes(run_id)
        pending = [task for task in spec.tasks if task.id not in outcomes]
        try:
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
                        outcome = {"status": "skipped", "code": "dependency_failed"}
                        c.save_outcome(run_id, task.id, outcome, generation)
                        outcomes[task.id] = outcome
                    break
                results = await asyncio.gather(*(self.execute(run_id, task) for task in ready))
                for task, outcome in zip(ready, results, strict=True):
                    c.save_outcome(run_id, task.id, outcome, generation)
                    outcomes[task.id] = outcome
                ready_ids = {t.id for t in ready}
                pending = [t for t in pending if t.id not in ready_ids]
            status = (
                "completed"
                if all(r["status"] == "completed" for r in outcomes.values())
                else "failed"
            )
            c.finish(run_id, status, generation)
        except asyncio.CancelledError:
            # Shutdown/ownership loss is resumable. Explicit API cancellation closes the run.
            raise
        except CoordinationError as exc:
            if exc.code not in {"execution_fenced", "workflow_closed"}:
                c.finish(run_id, "failed", generation)
            raise
        except Exception:
            c.finish(run_id, "failed", generation)
            raise
        finally:
            keeper.cancel()
            await asyncio.gather(keeper, return_exceptions=True)
            c.release_run(run_id, owner, generation)
            self.execution_tokens.pop(run_id, None)
        return {"id": run_id, "status": c.get_workflow(run_id)["status"], "tasks": outcomes}

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
            message = None
            committed = False
            try:
                if task.inbox:
                    while message is None:
                        workflow = c.get_workflow(run_id)
                        if workflow["status"] != "running":
                            raise CoordinationError("workflow_closed", "Workflow no longer running")
                        if c.remaining_seconds(run_id) <= 0:
                            raise CoordinationError(
                                "deadline_exceeded", "No message arrived before deadline"
                            )
                        message = c.claim_delivery(
                            "message", task.inbox, owner, ttl=300, workflow_id=run_id
                        )
                        if message is None:
                            await asyncio.sleep(0.1)
                attempt_id = c.begin_attempt(
                    run_id, task.id, owner, self.execution_tokens.get(run_id)
                )
                snapshot = c.snapshot(task.reads)
                remaining = c.remaining_seconds(run_id)
                if remaining <= 0:
                    raise CoordinationError("deadline_exceeded", "Workflow deadline has passed")
                async with asyncio.timeout(min(remaining, 240) if message else remaining):
                    if message:
                        plan = await self.planner.plan(task, snapshot, messages=[message])
                    else:
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
                        acknowledgements=[
                            DeliveryAck(id=message["id"], owner=owner, token=message["token"])
                        ]
                        if message
                        else [],
                        execution_token=self.execution_tokens.get(run_id),
                        workflow_id=run_id,
                        operation_id=task.id,
                        owner=owner,
                        lease_token=lease.token,
                        reads={k: r.stamp for k, r in snapshot.items()},
                        plan=plan,
                        attempt_id=attempt_id,
                    )
                )
                committed = True
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
                if message and not committed:
                    try:
                        c.reject_delivery(
                            DeliveryAck(id=message["id"], owner=owner, token=message["token"]),
                            retry_after=0,
                        )
                    except CoordinationError:
                        pass
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
