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
from .demo import demo_spec
from .engine import Engine
from .errors import CoordinationError
from .models import Commit, LeaseRequest, Model, ResourceCreate, WorkflowCreate, WorkflowSpec
from .planners import CrewPlanner, ScriptedPlanner


class RunRequest(Model):
    spec: WorkflowSpec
    mode: Literal["scripted", "crew"] = "scripted"
    run_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.:/-]{1,160}$")


class DemoRequest(Model):
    scenario: Literal["race", "schema"] = "schema"
    mode: Literal["scripted", "crew"] = "scripted"


class AttemptRequest(Model):
    operation_id: str = Field(min_length=1, max_length=160)
    owner: str = Field(min_length=1, max_length=160)


def create_app(db_path: str | None = None) -> FastAPI:
    coordinator = Coordinator(db_path or os.getenv("AGENTLATCH_DB", ".agentlatch/state.db"))
    jobs: dict[str, asyncio.Task] = {}
    token = os.getenv("AGENTLATCH_API_TOKEN", "")

    @asynccontextmanager
    async def lifespan(app):
        yield
        for job in jobs.values():
            job.cancel()
        await asyncio.gather(*jobs.values(), return_exceptions=True)

    app = FastAPI(title="AgentLatch", version="0.1.0", lifespan=lifespan)
    app.state.coordinator = coordinator
    app.state.jobs = jobs

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
        return {"status": "ok", "version": "0.1.0"}

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
        return {"attempt_id": coordinator.begin_attempt(workflow_id, body.operation_id, body.owner)}

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
        planner = CrewPlanner() if body.mode == "crew" else ScriptedPlanner()

        async def work():
            try:
                return await Engine(coordinator, planner).run(body.spec, run_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                coordinator.record(run_id, "run_error", {"type": type(exc).__name__})
                coordinator.finish(run_id, "failed")
                return {"id": run_id, "status": "failed"}

        jobs[run_id] = asyncio.create_task(work())
        # Keep only active jobs; durable state is stored in SQLite.
        jobs[run_id].add_done_callback(lambda done: jobs.pop(run_id, None))
        return {"id": run_id, "status": "running"}

    @app.post("/api/runs", dependencies=auth, status_code=202)
    async def run(body: RunRequest):
        return await launch(body)

    @app.post("/api/demos", dependencies=auth, status_code=202)
    async def demo(body: DemoRequest):
        return await launch(RunRequest(spec=demo_spec(body.scenario), mode=body.mode))

    @app.get("/api/runs/{run_id}", dependencies=auth)
    def run_status(run_id: str):
        result = coordinator.get_workflow(run_id)
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
