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
