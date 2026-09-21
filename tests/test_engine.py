import asyncio

import pytest

from agentlatch.coordinator import Coordinator
from agentlatch.demo import demo_spec
from agentlatch.engine import Engine
from agentlatch.models import Plan, ResourceCreate, TaskSpec, WorkflowSpec, Write
from agentlatch.planners import ScriptedPlanner


@pytest.mark.parametrize(
    "scenario,field,total", [("schema", "available", 11), ("race", "count", 14)]
)
async def test_demo_replans_after_conflict(tmp_path, scenario, field, total):
    c = Coordinator(tmp_path / "state.db")
    result = await Engine(c, ScriptedPlanner()).run(demo_spec(scenario))
    assert result["status"] == "completed"
    assert next(iter(c.snapshot().values())).value == {field: total}
    assert c.state()["totals"]["commit_rejected"] > 0
    assert c.state()["leases"] == []


async def test_scope_violation_and_failed_dependency(tmp_path):
    class Rogue:
        async def plan(self, task, snapshot):
            return Plan(writes=[Write(key="other", value={})])

    spec = WorkflowSpec(
        name="scope",
        resources=[ResourceCreate(key="a", value={})],
        tasks=[
            TaskSpec(id="first", role="r", goal="g", reads=["a"], writes=["a"]),
            TaskSpec(id="second", role="r", goal="g", reads=["a"], depends_on=["first"]),
        ],
    )
    c = Coordinator(tmp_path / "state.db")
    result = await Engine(c, Rogue()).run(spec)
    assert result["status"] == "failed"
    assert result["tasks"]["first"]["code"] == "scope_violation"
    assert result["tasks"]["second"]["status"] == "skipped"
    assert c.snapshot(["a"])["a"].version == 1


async def test_hung_planner_times_out(tmp_path):
    class Hung:
        async def plan(self, task, snapshot):
            await asyncio.sleep(100)

    spec = demo_spec("race").model_copy(update={"timeout_seconds": 0.05})
    c = Coordinator(tmp_path / "state.db")
    result = await Engine(c, Hung()).run(spec)
    assert result["status"] == "failed"
    assert all(r.value["count"] == 10 for r in c.snapshot().values())


async def test_resume_skips_committed_task(tmp_path):
    from agentlatch.models import WorkflowCreate

    spec = demo_spec("race")
    c = Coordinator(tmp_path / "state.db")
    for resource in spec.resources:
        c.create_resource(resource)
    c.create_workflow(
        WorkflowCreate(id="resume", max_steps=spec.max_steps, specification=spec.model_dump())
    )
    await Engine(c, ScriptedPlanner()).execute("resume", spec.tasks[0])
    result = await Engine(Coordinator(c.path), ScriptedPlanner()).run(spec, "resume")
    assert result["status"] == "completed"
    assert result["tasks"][spec.tasks[0].id]["cached"]
    assert next(iter(c.snapshot().values())).value == {"count": 14}
