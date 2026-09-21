import pytest
from pydantic import ValidationError

from agentlatch.coordinator import Coordinator
from agentlatch.errors import CoordinationError
from agentlatch.models import (
    Commit,
    LeaseRequest,
    Plan,
    ResourceCreate,
    WorkflowCreate,
    WorkflowSpec,
    Write,
)


def transfer(c, operation, values):
    reads = c.snapshot(list(values))
    attempt = c.begin_attempt("transfer", operation, "agent")
    lease = c.acquire(LeaseRequest(owner="agent", resources=list(values)))
    try:
        return c.commit(
            Commit(
                workflow_id="transfer",
                operation_id=operation,
                owner="agent",
                attempt_id=attempt,
                lease_token=lease.token,
                reads={k: v.stamp for k, v in reads.items()},
                plan=Plan(writes=[Write(key=k, value={"count": v}) for k, v in values.items()]),
            )
        )
    finally:
        c.release("agent", lease.token)


@pytest.fixture
def c(tmp_path):
    c = Coordinator(tmp_path / "invariants.db")
    c.create_resource(ResourceCreate(key="source", value={"count": 10}))
    c.create_resource(ResourceCreate(key="destination", value={"count": 0}))
    c.create_workflow(
        WorkflowCreate(
            id="transfer",
            specification={
                "invariants": [
                    {
                        "id": "inventory-total",
                        "resources": ["source", "destination"],
                        "field": "count",
                    }
                ]
            },
        )
    )
    return c


def test_valid_transfer_is_atomic(c):
    transfer(c, "valid", {"source": 7, "destination": 3})
    assert [r.value["count"] for r in c.snapshot().values()] == [3, 7]


def test_semantically_invalid_but_schema_valid_transfer_rejected(c):
    with pytest.raises(CoordinationError) as exc:
        transfer(c, "invalid", {"source": 7, "destination": 8})
    assert exc.value.code == "invariant_violation"
    assert c.snapshot(["source"])["source"].value == {"count": 10}
    assert c.snapshot(["destination"])["destination"].value == {"count": 0}
    assert c.operation("transfer", "invalid") is None


def test_cannot_omit_policy_dependency(c):
    with pytest.raises(CoordinationError) as exc:
        transfer(c, "omit", {"source": 7})
    assert exc.value.code == "incomplete_policy_reads"


def test_noninteger_amount_rejected(c):
    with pytest.raises(CoordinationError) as exc:
        transfer(c, "float", {"source": 7.0, "destination": 3.0})
    assert exc.value.code == "invariant_violation"


def test_invalid_policy_is_rejected_before_execution():
    with pytest.raises(ValidationError):
        WorkflowCreate(
            id="bad",
            specification={
                "invariants": [{"id": "rule", "resources": ["a", "a"], "field": "count"}]
            },
        )
    with pytest.raises(ValidationError):
        WorkflowSpec(
            name="bad",
            invariants=[{"id": "rule", "resources": ["a", "b"], "field": "count"}],
            tasks=[{"id": "agent", "role": "r", "goal": "g", "reads": ["a"], "writes": ["a"]}],
        )
