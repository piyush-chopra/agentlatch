from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Key = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9_.:/-]+$")]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Stamp(Model):
    version: int = Field(ge=1)
    schema_version: int = Field(ge=1)


class Resource(Model):
    key: Key
    value: dict[str, Any]
    json_schema: dict[str, Any]
    version: int
    schema_version: int

    @property
    def stamp(self) -> Stamp:
        return Stamp(version=self.version, schema_version=self.schema_version)


class ResourceCreate(Model):
    key: Key
    value: dict[str, Any]
    json_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})


class Write(Model):
    key: Key
    value: dict[str, Any]
    json_schema: dict[str, Any] | None = None


class Envelope(Model):
    id: Key
    kind: Literal["message", "effect"] = "message"
    destination: Key
    schema_version: int = Field(default=1, ge=1)
    payload: dict[str, Any]


class DeliveryAck(Model):
    id: Key
    owner: Key
    token: int = Field(ge=1)


class Plan(Model):
    envelopes: list[Envelope] = Field(default_factory=list, max_length=32)
    writes: list[Write] = Field(max_length=64)
    rationale: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def unique_writes(self):
        if len({w.key for w in self.writes}) != len(self.writes):
            raise ValueError("Each resource may be written only once in a plan")
        return self


class LeaseRequest(Model):
    owner: Key
    resources: list[Key] = Field(min_length=1, max_length=64)
    ttl_seconds: float = Field(default=15, ge=0.05, le=300)


class Lease(Model):
    owner: str
    token: int
    resources: list[str]
    expires_at: float


class Commit(Model):
    acknowledgements: list[DeliveryAck] = Field(default_factory=list, max_length=32)
    execution_token: int | None = Field(default=None, ge=1)
    workflow_id: Key
    operation_id: Key
    owner: Key
    lease_token: int = Field(ge=1)
    reads: dict[Key, Stamp] = Field(min_length=1, max_length=64)
    plan: Plan
    attempt_id: int = Field(ge=1)


class ConservationRule(Model):
    id: Key
    kind: Literal["conserve_total"] = "conserve_total"
    resources: list[Key] = Field(min_length=2, max_length=64)
    field: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def unique_resources(self):
        if len(set(self.resources)) != len(self.resources):
            raise ValueError("Invariant resources must be unique")
        return self


class NumericRule(Model):
    id: Key
    kind: Literal["numeric_bounds"] = "numeric_bounds"
    resources: list[Key] = Field(min_length=1, max_length=64)
    field: str = Field(min_length=1, max_length=160)
    minimum: int | None = None
    maximum: int | None = None
    max_delta: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def bounds(self):
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum exceeds maximum")
        return self


class SchemaRule(Model):
    id: Key
    kind: Literal["schema_allowlist"] = "schema_allowlist"
    resources: list[Key] = Field(min_length=1, max_length=64)
    allowed_hashes: list[str] = Field(min_length=1, max_length=32)


Invariant = Annotated[ConservationRule | NumericRule | SchemaRule, Field(discriminator="kind")]


def parse_rule(rule):
    from pydantic import TypeAdapter

    return TypeAdapter(Invariant).validate_python({"kind": "conserve_total", **rule})


class WorkflowCreate(Model):
    id: Key
    max_steps: int = Field(default=30, ge=1, le=10000)
    timeout_seconds: float = Field(default=300, gt=0, le=86400)
    repeat_limit: int = Field(default=3, ge=1, le=100)
    specification: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_invariants(self):
        rules = self.specification.get("invariants", [])
        if not isinstance(rules, list) or len(rules) > 32:
            raise ValueError("invariants must be a list of at most 32 rules")
        for rule in rules:
            parse_rule(rule)
        return self


class TaskSpec(Model):
    emits: list[Envelope] = Field(default_factory=list, max_length=32)
    inbox: Key | None = None
    destinations: list[Key] = Field(default_factory=list, max_length=32)
    id: Key
    role: str = Field(min_length=1, max_length=500)
    goal: str = Field(min_length=1, max_length=8000)
    reads: list[Key] = Field(min_length=1, max_length=64)
    writes: list[Key] = Field(default_factory=list, max_length=64)
    depends_on: list[Key] = Field(default_factory=list)
    max_attempts: int = Field(default=4, ge=1, le=20)
    # Deterministic actions power offline examples; CrewAI uses the natural-language goal.
    action: Literal["increment", "migrate", "copy", "transfer", "noop"] = "noop"
    field: str = "count"
    amount: int = 1

    @model_validator(mode="after")
    def scope(self):
        if any(e.destination not in self.destinations for e in self.emits):
            raise ValueError("Every emitted envelope needs a declared destination")
        if self.action == "transfer" and len(self.writes) != 2:
            raise ValueError("Transfer requires exactly two ordered write targets")
        if not set(self.writes) <= set(self.reads):
            raise ValueError("All writes must also appear in reads")
        if len(set(self.reads)) != len(self.reads):
            raise ValueError("Duplicate reads are not allowed")
        return self


class WorkflowSpec(Model):
    invariants: list[Invariant] = Field(default_factory=list, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    resources: list[ResourceCreate] = Field(default_factory=list)
    tasks: list[TaskSpec] = Field(min_length=1, max_length=100)
    max_steps: int = Field(default=30, ge=1, le=10000)
    timeout_seconds: float = Field(default=300, gt=0, le=86400)
    repeat_limit: int = Field(default=3, ge=1, le=100)

    @model_validator(mode="after")
    def acyclic(self):
        if len({r.id for r in self.invariants}) != len(self.invariants):
            raise ValueError("Invariant IDs must be unique")
        for task in self.tasks:
            for rule in self.invariants:
                if set(task.writes).intersection(rule.resources) and not set(rule.resources) <= set(
                    task.reads
                ):
                    raise ValueError(
                        f"Task {task.id} must read all resources of invariant {rule.id}"
                    )
        ids = {t.id for t in self.tasks}
        if len(ids) != len(self.tasks):
            raise ValueError("Task IDs must be unique")
        if len({r.key for r in self.resources}) != len(self.resources):
            raise ValueError("Resource keys must be unique")
        completed: set[str] = set()
        pending = list(self.tasks)
        while pending:
            ready = [t for t in pending if set(t.depends_on) <= completed]
            if not ready:
                raise ValueError("Workflow contains a dependency cycle or an unknown dependency")
            completed.update(t.id for t in ready)
            pending = [t for t in pending if t.id not in completed]
        return self
