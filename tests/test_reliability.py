import asyncio
import os
import time
import uuid

import pytest

from agentlatch.coordinator import Coordinator
from agentlatch.demo import demo_spec
from agentlatch.engine import Engine
from agentlatch.errors import CoordinationError
from agentlatch.models import (
    Commit,
    DeliveryAck,
    Envelope,
    LeaseRequest,
    Plan,
    WorkflowCreate,
    Write,
)
from agentlatch.planners import ScriptedPlanner
from agentlatch.scheduler import Scheduler


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path):
    if request.param == "sqlite":
        return Coordinator(tmp_path / "reliable.db")
    url = os.getenv("AGENTLATCH_TEST_POSTGRES")
    if not url:
        pytest.skip("Set AGENTLATCH_TEST_POSTGRES to an isolated test database")
    import psycopg
    from psycopg import sql

    schema = "test_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as db:
        db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    separator = "&" if "?" in url else "?"
    c = Coordinator(url + separator + "options=-csearch_path%3D" + schema)
    request.addfinalizer(lambda: drop_schema(url, schema))
    return c


def drop_schema(url, schema):
    import psycopg
    from psycopg import sql

    with psycopg.connect(url, autocommit=True) as db:
        db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def setup_run(c, mode="scripted", destinations=None, rules=None):
    spec = demo_spec("race")
    spec.tasks = spec.tasks[:1]
    spec.tasks[0].destinations = destinations or []
    if rules:
        spec.invariants = rules
    for resource in spec.resources:
        c.create_resource(resource)
    c.create_workflow(
        WorkflowCreate(id="run", max_steps=spec.max_steps, specification=spec.model_dump())
    )
    c.enqueue("run", mode)
    return spec


def proposal(c, spec, generation, envelopes=None):
    task = spec.tasks[0]
    snapshot = c.snapshot(task.reads)
    lease = c.acquire(LeaseRequest(owner="worker", resources=task.reads))
    attempt = c.begin_attempt("run", task.id, "worker", generation)
    return Commit(
        workflow_id="run",
        operation_id=task.id,
        owner="worker",
        lease_token=lease.token,
        execution_token=generation,
        attempt_id=attempt,
        reads={k: r.stamp for k, r in snapshot.items()},
        plan=Plan(
            writes=[Write(key=task.writes[0], value={"count": 11})], envelopes=envelopes or []
        ),
    )


def test_expired_scheduler_cannot_commit_or_finish(store):
    spec = setup_run(store)
    old = store.claim_run("run", "old", 0.08)
    req = proposal(store, spec, old)
    time.sleep(0.1)
    new = store.claim_run("run", "new")
    assert new > old
    for action in [
        lambda: store.commit(req),
        lambda: store.finish("run", "completed", old),
        lambda: store.begin_attempt("run", "late", "old", old),
    ]:
        with pytest.raises(CoordinationError) as exc:
            action()
        assert exc.value.code == "execution_fenced"
    assert store.snapshot()[spec.tasks[0].writes[0]].value == {"count": 10}
    store.release_run("run", "old", old)
    store.renew_run("run", "new", new)


async def test_two_schedulers_complete_once(store):
    spec = setup_run(store)
    first, second = Scheduler(store), Scheduler(Coordinator(store.path))
    first.start()
    second.start()
    try:
        for _ in range(100):
            if store.get_workflow("run")["status"] != "running":
                break
            await asyncio.sleep(0.05)
        assert store.get_workflow("run")["status"] == "completed"
        assert store.snapshot()[spec.tasks[0].writes[0]].value == {"count": 11}
        assert store.get_workflow("run")["steps"] == 1
        assert store.task_outcomes("run")[spec.tasks[0].id]["status"] == "completed"
    finally:
        await first.close()
        await second.close()


async def test_cancelled_process_work_resumes_without_repeating_commit(store):
    spec = setup_run(store)
    entered = asyncio.Event()

    class Hung:
        async def plan(self, task, snapshot):
            entered.set()
            await asyncio.sleep(100)

    job = asyncio.create_task(Engine(store, Hung()).run(spec, "run"))
    await entered.wait()
    job.cancel()
    await asyncio.gather(job, return_exceptions=True)
    assert store.get_workflow("run")["status"] == "running"
    result = await Engine(Coordinator(store.path), ScriptedPlanner()).run(spec, "run")
    assert result["status"] == "completed"
    assert store.get_workflow("run")["steps"] == 2
    assert store.snapshot()[spec.tasks[0].writes[0]].value == {"count": 11}


def test_outbox_atomic_and_duplicate_delivery_fencing(store):
    spec = setup_run(store, destinations=["inventory.updated"])
    generation = store.claim_run("run", "scheduler")
    envelope = Envelope(
        id="notice", kind="effect", destination="inventory.updated", payload={"count": 11}
    )
    req = proposal(store, spec, generation, [envelope])
    with pytest.raises(CoordinationError) as exc:
        store.commit(req)
    assert exc.value.code == "channel_missing"
    assert store.snapshot()[spec.tasks[0].writes[0]].value == {"count": 10}
    assert store.deliveries() == []
    store.register_channel(
        "inventory.updated", "effect", 1, {"type": "object", "required": ["count"]}
    )
    result = store.commit(req)
    assert store.commit(req) == result
    assert len(store.deliveries()) == 1
    old = store.claim_delivery("effect", "inventory.updated", "first", 0.05)
    time.sleep(0.07)
    new = store.claim_delivery("effect", "inventory.updated", "second")
    assert old["id"] == new["id"]
    with pytest.raises(CoordinationError):
        store.acknowledge(DeliveryAck(id=old["id"], owner="first", token=old["token"]))
    ack = DeliveryAck(id=new["id"], owner="second", token=new["token"])
    store.acknowledge(ack)
    store.acknowledge(ack)
    assert store.claim_delivery("effect", "inventory.updated", "third") is None


def test_numeric_policy_rejects_large_valid_json_change(store):
    from agentlatch.models import NumericRule

    spec = setup_run(store)
    # Separate fresh run to persist a policy without mutating its identity.
    rule = NumericRule(
        id="safe-delta", resources=spec.tasks[0].reads, field="count", minimum=0, max_delta=1
    )
    spec.invariants = [rule]
    store.create_workflow(WorkflowCreate(id="policy", specification=spec.model_dump()))
    lease = store.acquire(LeaseRequest(owner="p", resources=spec.tasks[0].reads))
    req = Commit(
        workflow_id="policy",
        operation_id=spec.tasks[0].id,
        owner="p",
        lease_token=lease.token,
        attempt_id=store.begin_attempt("policy", spec.tasks[0].id, "p"),
        reads={k: r.stamp for k, r in store.snapshot().items()},
        plan=Plan(writes=[Write(key=spec.tasks[0].writes[0], value={"count": 1000})]),
    )
    with pytest.raises(CoordinationError) as exc:
        store.commit(req)
    assert exc.value.code == "invariant_violation"
    assert store.snapshot()[spec.tasks[0].writes[0]].value == {"count": 10}


async def test_message_consumption_and_followup_effect_are_atomic(store):
    from agentlatch.models import ResourceCreate, TaskSpec, WorkflowSpec

    store.register_channel("review", "message", 1, {"type": "object", "required": ["instruction"]})
    store.register_channel("notify", "effect", 1, {"type": "object"})
    spec = WorkflowSpec(
        name="handoff",
        resources=[ResourceCreate(key="stock", value={"count": 0})],
        tasks=[
            TaskSpec(
                id="producer",
                role="r",
                goal="increment",
                reads=["stock"],
                writes=["stock"],
                action="increment",
                destinations=["review"],
                emits=[
                    Envelope(
                        id="review-request",
                        destination="review",
                        payload={"instruction": "Review stock"},
                    )
                ],
            ),
            TaskSpec(
                id="consumer",
                role="r",
                goal="increment after review",
                reads=["stock"],
                writes=["stock"],
                depends_on=["producer"],
                action="increment",
                inbox="review",
                destinations=["notify"],
                emits=[
                    Envelope(
                        id="notification",
                        kind="effect",
                        destination="notify",
                        payload={"done": True},
                    )
                ],
            ),
        ],
    )

    class Observer(ScriptedPlanner):
        async def plan(self, task, snapshot, messages=None):
            if task.id == "consumer":
                assert messages[0]["payload"]["instruction"] == "Review stock"
                assert snapshot["stock"].value["count"] == 1
            return await super().plan(task, snapshot, messages)

    result = await Engine(store, Observer()).run(spec)
    assert result["status"] == "completed"
    assert store.snapshot()["stock"].value == {"count": 2}
    states = {r["kind"]: r["status"] for r in store.deliveries()}
    assert states == {"message": "delivered", "effect": "pending"}


def test_delivery_eventually_deadletters_after_worker_crashes(store):
    spec = setup_run(store, destinations=["mail"])
    store.register_channel("mail", "message", 1, {"type": "object"})
    req = proposal(
        store,
        spec,
        store.claim_run("run", "scheduler"),
        [Envelope(id="mail", destination="mail", payload={})],
    )
    store.commit(req)
    for _ in range(5):
        assert store.claim_delivery("message", "mail", "crashing", 0.05)
        time.sleep(0.06)
    assert store.claim_delivery("message", "mail", "later") is None
    assert store.deliveries()[0]["status"] == "dead"


async def test_outbox_ambiguous_ack_reuses_idempotency_key(store):
    from agentlatch.dispatcher import Dispatcher

    spec = setup_run(store, destinations=["sink"])
    store.register_channel("sink", "effect", 1, {"type": "object"})
    store.commit(
        proposal(
            store,
            spec,
            store.claim_run("run", "scheduler"),
            [Envelope(id="effect", kind="effect", destination="sink", payload={})],
        )
    )
    processed = set()
    calls = []

    async def receiver(item):
        calls.append(item["id"])
        processed.add(item["id"])
        if len(calls) == 1:
            raise TimeoutError("Simulate successful receiver with a lost response")

    dispatcher = Dispatcher(store, {"sink": receiver})
    await dispatcher.once()
    with store.transaction() as db:
        db.execute("UPDATE deliveries SET available_at=0")
    await dispatcher.once()
    assert len(calls) == 2 and len(processed) == 1
    assert store.deliveries()[0]["status"] == "delivered"


def killed_scheduler(path, serialized, signal):
    import json

    from agentlatch.models import WorkflowSpec

    c = Coordinator(path)
    spec = WorkflowSpec.model_validate(json.loads(serialized))
    token = c.claim_run("run", "doomed", 0.15)
    req = proposal(c, spec, token)
    c.commit(req)
    c.release(req.owner, req.lease_token)
    signal.put(True)
    # Die without final task outcome or scheduler release.
    time.sleep(60)


async def test_process_kill_after_commit_recovers_receipt(store):
    import multiprocessing

    spec = setup_run(store)
    ctx = multiprocessing.get_context("spawn")
    signal = ctx.Queue()
    child = ctx.Process(target=killed_scheduler, args=(store.path, spec.model_dump_json(), signal))
    child.start()
    try:
        assert await asyncio.to_thread(signal.get, True, 15)
        child.kill()
        await asyncio.to_thread(child.join, 5)
        await asyncio.sleep(0.2)
        result = await Engine(Coordinator(store.path), ScriptedPlanner()).run(spec, "run")
        assert result["status"] == "completed"
        assert result["tasks"][spec.tasks[0].id]["cached"]
        assert store.snapshot()[spec.tasks[0].writes[0]].value == {"count": 11}
        assert store.get_workflow("run")["steps"] == 1
    finally:
        if child.is_alive():
            child.kill()
        child.join(5)


def test_schema_allowlist_blocks_unauthorized_migration(store):
    from agentlatch.coordinator import digest
    from agentlatch.models import SchemaRule

    spec = setup_run(store)
    key = spec.tasks[0].writes[0]
    spec.invariants = [
        SchemaRule(
            id="approved-contract",
            resources=[key],
            allowed_hashes=[digest(store.snapshot()[key].json_schema)],
        )
    ]
    store.create_workflow(WorkflowCreate(id="schema-policy", specification=spec.model_dump()))
    lease = store.acquire(LeaseRequest(owner="p", resources=[key]))
    req = Commit(
        workflow_id="schema-policy",
        operation_id=spec.tasks[0].id,
        owner="p",
        lease_token=lease.token,
        attempt_id=store.begin_attempt("schema-policy", spec.tasks[0].id, "p"),
        reads={key: store.snapshot()[key].stamp},
        plan=Plan(writes=[Write(key=key, value={"renamed": 10}, json_schema={"type": "object"})]),
    )
    with pytest.raises(CoordinationError) as exc:
        store.commit(req)
    assert exc.value.code == "invariant_violation"
    assert store.snapshot()[key].schema_version == 1


def killed_transaction(path, key, signal):
    c = Coordinator(path)
    with c.transaction() as db:
        db.execute("UPDATE resources SET value=? WHERE key=?", ('{"count":999}', key))
        c.event(db, "run", "uncommitted_event", {})
        signal.put(True)
        time.sleep(60)


async def test_process_kill_inside_transaction_rolls_back(store):
    import multiprocessing

    spec = setup_run(store)
    key = spec.tasks[0].writes[0]
    ctx = multiprocessing.get_context("spawn")
    signal = ctx.Queue()
    child = ctx.Process(target=killed_transaction, args=(store.path, key, signal))
    child.start()
    try:
        assert await asyncio.to_thread(signal.get, True, 15)
        child.kill()
        await asyncio.to_thread(child.join, 5)
        reopened = Coordinator(store.path)
        assert reopened.snapshot()[key].value == {"count": 10}
        assert not any(e["kind"] == "uncommitted_event" for e in reopened.events())
    finally:
        if child.is_alive():
            child.kill()
        child.join(5)


def test_mode_and_task_budget_are_persisted(store):
    spec = setup_run(store)
    generation = store.claim_run("run", "scheduler")
    with pytest.raises(CoordinationError) as exc:
        store.enqueue("run", "crew")
    assert exc.value.code == "runtime_mismatch"
    for _ in range(spec.tasks[0].max_attempts):
        store.begin_attempt("run", spec.tasks[0].id, "worker", generation)
    with pytest.raises(CoordinationError) as exc:
        store.begin_attempt("run", spec.tasks[0].id, "worker", generation)
    assert exc.value.code == "attempts_exhausted"


def test_channel_versions_are_immutable_and_schema_checked(store):
    store.register_channel("channel", "message", 1, {"type": "object"})
    with pytest.raises(CoordinationError) as exc:
        store.register_channel("channel", "message", 1, {"type": "object", "required": ["x"]})
    assert exc.value.code == "channel_immutable"
    with pytest.raises(CoordinationError):
        store.register_channel("remote", "message", 1, {"$ref": "https://example.com/schema"})
    with pytest.raises(CoordinationError):
        store.register_channel("bad", "message", 1, {"type": "not-a-type"})


async def test_http_effect_adapter_sends_stable_id_without_following_redirect(store):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.error import HTTPError

    from agentlatch.dispatcher import http_handlers

    received = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, self.headers["Idempotency-Key"], payload))
            self.send_response(302 if self.path == "/redirect" else 204)
            if self.path == "/redirect":
                self.send_header("Location", "/unexpected")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    item = {"id": "stable-id", "workflow_id": "run", "schema_version": 1, "payload": {"count": 11}}
    try:
        await http_handlers({"sink": base + "/events"})["sink"](item)
        with pytest.raises(HTTPError):
            await http_handlers({"sink": base + "/redirect"})["sink"](item)
        assert [r[0] for r in received] == ["/events", "/redirect"]
        assert all(r[1] == "stable-id" and r[2]["payload"] == {"count": 11} for r in received)
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(2)
