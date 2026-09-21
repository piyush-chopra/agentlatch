"""A real mailbox/outbox handoff; effects remain queued unless a dispatcher is started."""

import argparse
import asyncio
import json

from dotenv import load_dotenv

from agentlatch.coordinator import Coordinator
from agentlatch.demo import handoff_spec
from agentlatch.engine import Engine
from agentlatch.planners import CrewPlanner, ScriptedPlanner


async def main():
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=".agentlatch/state.db")
    parser.add_argument("--mode", choices=["scripted", "crew"], default="scripted")
    args = parser.parse_args()
    c = Coordinator(args.db)
    spec = handoff_spec().model_copy(
        update={"timeout_seconds": 900 if args.mode == "crew" else 300}
    )
    for task in spec.tasks:
        for envelope in task.emits:
            c.register_channel(
                envelope.destination, envelope.kind, envelope.schema_version, {"type": "object"}
            )
    planner = CrewPlanner() if args.mode == "crew" else ScriptedPlanner()
    result = await Engine(c, planner).run(spec, mode=args.mode)
    print(
        json.dumps(
            {
                "run": result,
                "deliveries": [
                    {k: d[k] for k in ["id", "kind", "status", "attempts"]}
                    for d in c.deliveries()
                    if d["workflow_id"] == result["id"]
                ],
            },
            indent=2,
        )
    )
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
