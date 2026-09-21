import uuid

from .models import ResourceCreate, TaskSpec, WorkflowSpec


def demo_spec(scenario: str = "schema") -> WorkflowSpec:
    namespace = f"demo-{uuid.uuid4().hex[:8]}"
    key = f"{namespace}/inventory"
    resource = ResourceCreate(
        key=key,
        value={"count": 10},
        json_schema={
            "type": "object",
            "properties": {"count": {"type": "integer", "minimum": 0}},
            "required": ["count"],
            "additionalProperties": False,
        },
    )
    if scenario == "race":
        tasks = [
            TaskSpec(
                id=f"agent-{i}",
                role="Inventory specialist",
                goal="Increase inventory count by one using the current schema.",
                reads=[key],
                writes=[key],
                action="increment",
                max_attempts=8,
            )
            for i in range(4)
        ]
    elif scenario == "schema":
        tasks = [
            TaskSpec(
                id="schema-architect",
                role="Schema architect",
                goal="Rename count to available. Preserve the quantity. Migrate the JSON schema: available must be a nonnegative integer, required, with no extra properties.",
                reads=[key],
                writes=[key],
                action="migrate",
            ),
            TaskSpec(
                id="inventory-agent",
                role="Inventory specialist",
                goal="Add one item to inventory. Use the quantity field defined by the current schema (count or available). Preserve the schema.",
                reads=[key],
                writes=[key],
                action="increment",
            ),
        ]
    else:
        raise ValueError("Unknown demo scenario")
    return WorkflowSpec(
        name=f"{scenario.title()} conflict demonstration",
        resources=[resource],
        tasks=tasks,
        timeout_seconds=300,
    )


def handoff_spec():
    from .models import Envelope, NumericRule

    suffix = uuid.uuid4().hex[:8]
    key = f"handoff-{suffix}/inventory"
    review = f"handoff-{suffix}/review"
    notify = "inventory-notifications"
    return WorkflowSpec(
        name="Durable agent handoff",
        resources=[ResourceCreate(key=key, value={"count": 10})],
        invariants=[
            NumericRule(id="bounded-update", resources=[key], field="count", minimum=0, max_delta=1)
        ],
        tasks=[
            TaskSpec(
                id="producer",
                role="Inventory specialist",
                goal="Increase count by one and emit the requested review message.",
                reads=[key],
                writes=[key],
                action="increment",
                destinations=[review],
                emits=[
                    Envelope(
                        id="review",
                        destination=review,
                        payload={"request": "Verify the latest authoritative inventory snapshot"},
                    )
                ],
            ),
            TaskSpec(
                id="reviewer",
                role="State reviewer",
                goal="Read the review message and current snapshot; leave count unchanged and emit the notification template.",
                reads=[key],
                writes=[key],
                action="noop",
                depends_on=["producer"],
                inbox=review,
                destinations=[notify],
                emits=[
                    Envelope(
                        id="notification",
                        kind="effect",
                        destination=notify,
                        payload={"inventory": key, "reviewed": True},
                    )
                ],
            ),
        ],
        max_steps=10,
    )
