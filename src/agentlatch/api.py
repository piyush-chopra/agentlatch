from __future__ import annotations

import asyncio
import hmac
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from .coordinator import Coordinator
from .demo import demo_spec, handoff_spec
from .errors import CoordinationError
from .models import (
    Commit,
    DeliveryAck,
    Key,
    LeaseRequest,
    Model,
    ResourceCreate,
    WorkflowCreate,
    WorkflowSpec,
)
from .runtime import require_runtime, runtime_status
from .scheduler import Scheduler


class RunRequest(Model):
    spec: WorkflowSpec
    mode: Literal["scripted", "crew"] = "scripted"
    run_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.:/-]{1,160}$")


class DemoRequest(Model):
    scenario: Literal["race", "schema"] = "schema"
    mode: Literal["scripted", "crew"] = "scripted"


class AttemptRequest(Model):
    execution_token: int | None = Field(default=None, ge=1)
    operation_id: str = Field(min_length=1, max_length=160)
    owner: str = Field(min_length=1, max_length=160)


class ChannelRequest(Model):
    destination: Key
    kind: Literal["message", "effect"]
    schema_version: int = Field(default=1, ge=1)
    json_schema: dict


class DeliveryClaim(Model):
    kind: Literal["message", "effect"]
    destination: Key
    owner: Key
    ttl_seconds: float = Field(default=30, ge=0.05, le=300)
    workflow_id: Key | None = None


def create_app(db_path: str | None = None) -> FastAPI:
    coordinator = Coordinator(db_path or os.getenv("AGENTLATCH_DB", ".agentlatch/state.db"))
    scheduler = Scheduler(coordinator, max_runs=int(os.getenv("AGENTLATCH_MAX_RUNS", "4")))
    jobs = scheduler.jobs
    token = os.getenv("AGENTLATCH_API_TOKEN", "")

    @asynccontextmanager
    async def lifespan(app):
        if os.getenv("AGENTLATCH_SCHEDULER_ENABLED", "true").lower() == "true":
            scheduler.start()
        try:
            yield
        finally:
            await scheduler.close()

    app = FastAPI(title="AgentLatch", version="0.2.0", lifespan=lifespan)
    app.state.coordinator = coordinator
    app.state.jobs = jobs
    app.state.scheduler = scheduler

    @app.exception_handler(CoordinationError)
    async def coordination_error(request: Request, exc: CoordinationError):
        return JSONResponse(
            status_code=exc.status, content={"code": exc.code, "message": exc.message}
        )

    def authorize(authorization: str | None = Header(default=None)):
        if token and not hmac.compare_digest(authorization or "", f"Bearer {token}"):
            raise CoordinationError("unauthorized", "A valid API bearer token is required", 401)

    auth = [Depends(authorize)]

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.2.0"}

    @app.get("/ready")
    def ready():
        try:
            with coordinator.connection() as db:
                db.execute("SELECT count(*) FROM schema_migrations").fetchone()
            return {"status": "ready"}
        except Exception:
            return JSONResponse(status_code=503, content={"status": "unavailable"})

    @app.get("/api/metrics", dependencies=auth)
    def metrics():
        with coordinator.connection() as db:
            runs = {
                r[0]: r[1]
                for r in db.execute("SELECT status,count(*) FROM workflows GROUP BY status")
            }
            deliveries = {
                r[0]: r[1]
                for r in db.execute("SELECT status,count(*) FROM deliveries GROUP BY status")
            }
        return {
            "workflows": runs,
            "deliveries": deliveries,
            "scheduler_errors": scheduler.errors,
            "active_local_jobs": len(jobs),
            "max_local_jobs": scheduler.max_runs,
        }

    @app.get("/api/executions", dependencies=auth)
    def executions():
        return {
            "claims": coordinator.execution_status(),
            "scheduler_errors": scheduler.errors,
            "backend": "postgresql" if coordinator.storage.postgres else "sqlite",
        }

    @app.post("/api/channels", dependencies=auth, status_code=201)
    def channel(body: ChannelRequest):
        coordinator.register_channel(
            body.destination, body.kind, body.schema_version, body.json_schema
        )
        return {"registered": True}

    @app.get("/api/deliveries", dependencies=auth)
    def deliveries(limit: int = Query(100, ge=1, le=1000)):
        return coordinator.deliveries(limit)

    @app.post("/api/deliveries/claim", dependencies=auth)
    def delivery_claim(body: DeliveryClaim):
        return coordinator.claim_delivery(
            body.kind, body.destination, body.owner, body.ttl_seconds, body.workflow_id
        )

    @app.post("/api/deliveries/ack", dependencies=auth)
    def delivery_ack(body: DeliveryAck):
        return coordinator.acknowledge(body)

    @app.post("/api/deliveries/retry", dependencies=auth)
    def delivery_retry(body: DeliveryAck):
        coordinator.reject_delivery(body)
        return {"released": True}

    @app.get("/api/runtime", dependencies=auth)
    def runtime():
        return runtime_status()

    @app.get("/api/state", dependencies=auth)
    def state():
        return coordinator.state()

    @app.get("/api/events", dependencies=auth)
    def events(
        after: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=1000),
        workflow_id: str | None = None,
    ):
        return coordinator.events(after, limit, workflow_id)

    @app.post("/api/resources", dependencies=auth, status_code=201)
    def resources(body: ResourceCreate):
        return coordinator.create_resource(body)

    @app.get("/api/snapshot", dependencies=auth)
    def snapshot(key: Annotated[list[str], Query(min_length=1, max_length=64)]):
        return coordinator.snapshot(key)

    @app.post("/api/workflows", dependencies=auth, status_code=201)
    def workflow(body: WorkflowCreate):
        return coordinator.create_workflow(body)

    @app.post("/api/workflows/{workflow_id}/attempts", dependencies=auth)
    def attempt(workflow_id: str, body: AttemptRequest):
        return {
            "attempt_id": coordinator.begin_attempt(
                workflow_id, body.operation_id, body.owner, body.execution_token
            )
        }

    @app.post("/api/leases", dependencies=auth)
    def acquire(body: LeaseRequest):
        return coordinator.acquire(body)

    @app.delete("/api/leases/{lease_token}", dependencies=auth)
    def release(lease_token: int, owner: str):
        coordinator.release(owner, lease_token)
        return {"released": True}

    @app.post("/api/commits", dependencies=auth)
    def commit(body: Commit):
        return coordinator.commit(body)

    async def launch(body: RunRequest):
        if body.mode == "crew" and os.getenv("AGENTLATCH_ENABLE_CREW", "false").lower() != "true":
            raise CoordinationError(
                "crew_disabled", "Set AGENTLATCH_ENABLE_CREW=true to enable LLM-backed runs", 422
            )
        if body.mode == "crew":
            await asyncio.to_thread(require_runtime)
        run_id = body.run_id or f"run-{uuid.uuid4().hex[:12]}"
        if run_id in jobs and not jobs[run_id].done():
            raise CoordinationError("run_active", "This run is already executing")
        # Validate persisted identity before returning an accepted response.
        existing = coordinator.create_workflow(
            WorkflowCreate(
                id=run_id,
                max_steps=body.spec.max_steps,
                timeout_seconds=body.spec.timeout_seconds,
                repeat_limit=body.spec.repeat_limit,
                specification=body.spec.model_dump(),
            )
        )
        if existing["status"] != "running":
            return {"id": run_id, "status": existing["status"]}
        coordinator.enqueue(run_id, body.mode)
        return {"id": run_id, "status": "running"}

    @app.post("/api/runs", dependencies=auth, status_code=202)
    async def run(body: RunRequest):
        return await launch(body)

    @app.post("/api/demos", dependencies=auth, status_code=202)
    async def demo(body: DemoRequest):
        spec = demo_spec(body.scenario)
        if body.mode == "crew":
            spec = spec.model_copy(update={"timeout_seconds": 900})
        return await launch(RunRequest(spec=spec, mode=body.mode))

    @app.post("/api/demos/handoff", dependencies=auth, status_code=202)
    async def handoff():
        spec = handoff_spec()
        for task in spec.tasks:
            for envelope in task.emits:
                coordinator.register_channel(
                    envelope.destination, envelope.kind, 1, {"type": "object"}
                )
        return await launch(RunRequest(spec=spec, mode="scripted"))

    @app.get("/api/runs/{run_id}", dependencies=auth)
    def run_status(run_id: str):
        result = coordinator.get_workflow(run_id)
        result["task_outcomes"] = coordinator.task_outcomes(run_id)
        result["specification"] = __import__("json").loads(result["specification"])
        return result

    @app.post("/api/runs/{run_id}/cancel", dependencies=auth)
    async def cancel(run_id: str):
        coordinator.get_workflow(run_id)
        coordinator.finish(run_id, "cancelled")
        if run_id in jobs:
            jobs[run_id].cancel()
        return {"id": run_id, "status": coordinator.get_workflow(run_id)["status"]}

    static = Path(__file__).parent / "static"
    if (static / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def frontend():
        if (static / "index.html").exists():
            return FileResponse(static / "index.html")
        return JSONResponse(
            {
                "message": "Build the React UI: cd frontend && npm ci && npm run build",
                "api_docs": "/docs",
            }
        )

    return app
