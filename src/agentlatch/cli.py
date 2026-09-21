import argparse
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from .coordinator import Coordinator
from .demo import demo_spec
from .engine import Engine
from .models import WorkflowSpec
from .planners import CrewPlanner, ScriptedPlanner


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="AgentLatch — deterministic coordination for AI agents"
    )
    parser.add_argument("--db", default=os.getenv("AGENTLATCH_DB", ".agentlatch/state.db"))
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Serve the API and built React console")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    demo = commands.add_parser("demo", help="Run a reproducible concurrency scenario")
    demo.add_argument("--scenario", choices=["race", "schema"], default="schema")
    demo.add_argument("--mode", choices=["scripted", "crew"], default="scripted")
    run = commands.add_parser("run", help="Execute a workflow JSON file")
    run.add_argument("file", type=Path)
    run.add_argument("--run-id", help="Reuse a persisted running workflow after a crash")
    run.add_argument("--mode", choices=["scripted", "crew"], default="scripted")
    commands.add_parser("worker", help="Recover and execute durable queued workflows")
    commands.add_parser(
        "dispatch", help="Deliver transactional outbox effects to configured endpoints"
    )
    commands.add_parser("state", help="Inspect persisted coordinator state")
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(args.db), host=args.host, port=args.port)
        return
    coordinator = Coordinator(args.db)
    if args.command == "worker":
        from .scheduler import Scheduler

        async def work():
            scheduler = Scheduler(coordinator)
            scheduler.start()
            try:
                await scheduler.task
            finally:
                await scheduler.close()

        asyncio.run(work())
        return
    if args.command == "dispatch":
        from .dispatcher import Dispatcher, http_handlers

        handlers = http_handlers(json.loads(os.getenv("AGENTLATCH_EFFECT_ENDPOINTS", "{}")))
        if not handlers:
            parser.error(
                "Set AGENTLATCH_EFFECT_ENDPOINTS to a JSON mapping of destination names to URLs"
            )
        asyncio.run(Dispatcher(coordinator, handlers).run())
        return
    if args.command == "state":
        print(json.dumps(coordinator.state(), indent=2))
        return
    spec = (
        demo_spec(args.scenario)
        if args.command == "demo"
        else WorkflowSpec.model_validate_json(args.file.read_text())
    )
    if args.command == "demo" and args.mode == "crew":
        spec = spec.model_copy(update={"timeout_seconds": 900})
    planner = CrewPlanner() if args.mode == "crew" else ScriptedPlanner()
    result = asyncio.run(
        Engine(coordinator, planner).run(spec, getattr(args, "run_id", None), mode=args.mode)
    )
    print(json.dumps(result, indent=2))
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
