import multiprocessing
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from agentlatch.coordinator import Coordinator
from agentlatch.errors import CoordinationError
from agentlatch.models import (
    Commit,
    LeaseRequest,
    Plan,
    ResourceCreate,
    Stamp,
    TaskSpec,
    WorkflowCreate,
    WorkflowSpec,
    Write,
)

SCHEMA = {
    "type": "object",
    "properties": {"count": {"type": "integer", "minimum": 0}},
    "required": ["count"],
    "additionalProperties": False,
}


@pytest.fixture
def c(tmp_path):
    coordinator = Coordinator(tmp_path / "test.db")
    for key in ["a", "b"]:
        coordinator.create_resource(ResourceCreate(key=key, value={"count": 0}, json_schema=SCHEMA))
    coordinator.create_workflow(WorkflowCreate(id="w", max_steps=100))
    return coordinator


def request(c, operation="op", keys=None, values=None, owner="worker", reads=None):
    keys = keys or ["a"]
    snapshot = c.snapshot(keys)
    lease = c.acquire(LeaseRequest(owner=owner, resources=keys))
    attempt = c.begin_attempt("w", operation, owner)
    return Commit(
        workflow_id="w",
        operation_id=operation,
        owner=owner,
        lease_token=lease.token,
        attempt_id=attempt,
        reads=reads or {k: r.stamp for k, r in snapshot.items()},
        plan=Plan(writes=[Write(key=k, value=(values or {}).get(k, {"count": 1})) for k in keys]),
    )


def commit_release(c, req):
    try:
        return c.commit(req)
    finally:
        c.release(req.owner, req.lease_token)


def test_atomic_rollback(c):
    req = request(c, keys=["a", "b"], values={"b": {"count": -1}})
    with pytest.raises(CoordinationError, match="-1"):
        commit_release(c, req)
    assert [r.value["count"] for r in c.snapshot().values()] == [0, 0]
    assert c.operation("w", "op") is None


def test_stale_reads_and_schema_migration(c):
    stale = c.snapshot(["a"])["a"].stamp
    req = request(c)
    req.plan = Plan(
        writes=[
            Write(
                key="a",
                value={"available": 5},
                json_schema={"type": "object", "required": ["available"]},
            )
        ]
    )
    commit_release(c, req)
    migrated = c.snapshot(["a"])["a"]
    assert (migrated.version, migrated.schema_version) == (2, 2)
    req = request(c, "stale", reads={"a": stale})
    with pytest.raises(CoordinationError) as exc:
        commit_release(c, req)
    assert exc.value.code == "version_conflict"


def test_read_dependency_prevents_write_skew(c):
    snapshots = c.snapshot(["a", "b"])
    commit_release(c, request(c, "other", keys=["b"]))
    req = request(c, "consumer", keys=["a", "b"], reads={k: v.stamp for k, v in snapshots.items()})
    req.plan = Plan(writes=[Write(key="a", value={"count": 4})])
    with pytest.raises(CoordinationError) as exc:
        commit_release(c, req)
    assert exc.value.code == "version_conflict"
    assert c.snapshot(["a"])["a"].value == {"count": 0}


def test_idempotent_receipt_survives_restart_and_closed_workflow(c):
    req = request(c)
    result = commit_release(c, req)
    c.finish("w", "completed")
    reopened = Coordinator(c.path)
    assert reopened.commit(req) == result
    assert reopened.snapshot(["a"])["a"].version == 2
    req.plan.writes[0].value = {"count": 2}
    with pytest.raises(CoordinationError) as exc:
        reopened.commit(req)
    assert exc.value.code == "idempotency_mismatch"


def test_fencing_expired_owner_and_release(c):
    old = c.acquire(LeaseRequest(owner="old", resources=["a"], ttl_seconds=0.05))
    attempt = c.begin_attempt("w", "old", "old")
    time.sleep(0.07)
    new = c.acquire(LeaseRequest(owner="new", resources=["a"]))
    assert new.token > old.token
    c.release("old", old.token)
    req = Commit(
        workflow_id="w",
        operation_id="old",
        owner="old",
        lease_token=old.token,
        attempt_id=attempt,
        reads={"a": Stamp(version=1, schema_version=1)},
        plan=Plan(writes=[]),
    )
    with pytest.raises(CoordinationError) as exc:
        c.commit(req)
    assert exc.value.code == "stale_lease"
    assert c.state()["leases"][0]["token"] == new.token


def test_no_partial_lease_acquisition(c):
    held = c.acquire(LeaseRequest(owner="first", resources=["b"]))
    with pytest.raises(CoordinationError):
        c.acquire(LeaseRequest(owner="second", resources=["a", "b"]))
    free = c.acquire(LeaseRequest(owner="third", resources=["a"]))
    assert free.resources == ["a"]
    c.release("first", held.token)


def test_step_budget_is_atomic_under_contention(c):
    c.create_workflow(WorkflowCreate(id="small", max_steps=3))

    def attempt(i):
        try:
            c.begin_attempt("small", str(i), "worker")
            return True
        except CoordinationError:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(attempt, range(20)))
    assert sum(outcomes) == 3
    assert c.get_workflow("small")["steps"] == 3


def test_deadline_and_cancellation(c):
    c.create_workflow(WorkflowCreate(id="short", timeout_seconds=0.01))
    time.sleep(0.02)
    with pytest.raises(CoordinationError) as exc:
        c.begin_attempt("short", "x", "x")
    assert exc.value.code == "deadline_exceeded"
    req = request(c)
    c.finish("w", "cancelled")
    with pytest.raises(CoordinationError) as exc:
        commit_release(c, req)
    assert exc.value.code == "workflow_closed"


def test_repeated_transition_is_bounded(c):
    for i in range(3):
        req = request(c, str(i), values={"a": {"count": 0}})
        commit_release(c, req)
    req = request(c, "fourth", values={"a": {"count": 0}})
    with pytest.raises(CoordinationError) as exc:
        commit_release(c, req)
    assert exc.value.code == "loop_detected"


def test_scope_and_cycles_rejected():
    with pytest.raises(ValidationError):
        TaskSpec(id="x", role="r", goal="g", reads=["a"], writes=["b"])
    with pytest.raises(ValidationError):
        WorkflowSpec(
            name="cycle",
            tasks=[
                TaskSpec(id="x", role="r", goal="g", reads=["a"], depends_on=["y"]),
                TaskSpec(id="y", role="r", goal="g", reads=["a"], depends_on=["x"]),
            ],
        )


def test_no_remote_schema_resolution(c):
    with pytest.raises(CoordinationError) as exc:
        c.create_resource(
            ResourceCreate(
                key="remote", value={}, json_schema={"$ref": "https://example.com/schema"}
            )
        )
    assert exc.value.code == "invalid_schema"


def process_contender(path, owner, queue):
    coordinator = Coordinator(path)
    try:
        lease = coordinator.acquire(LeaseRequest(owner=owner, resources=["a", "b"]))
        queue.put(lease.token)
    except CoordinationError:
        queue.put(None)


def test_leases_are_cross_process(c):
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    processes = [
        ctx.Process(target=process_contender, args=(c.path, f"p{i}", queue)) for i in range(4)
    ]
    for process in processes:
        process.start()
    results = [queue.get(timeout=15) for _ in processes]
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    assert sum(r is not None for r in results) == 1


def test_invalid_attempt_and_blind_write(c):
    req = request(c)
    req.attempt_id = 99999
    with pytest.raises(CoordinationError) as exc:
        c.commit(req)
    assert exc.value.code == "invalid_attempt"
    req.attempt_id = c.begin_attempt("w", "op", "worker")
    req.plan.writes.append(Write(key="b", value={"count": 1}))
    with pytest.raises(CoordinationError) as exc:
        commit_release(c, req)
    assert exc.value.code == "blind_write"
