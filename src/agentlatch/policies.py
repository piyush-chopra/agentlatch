"""Deterministic, workflow-scoped business constraints; never evaluated by an LLM."""

from .errors import CoordinationError
from .models import ConservationRule, Resource


def enforce_conservation(
    rules: list[ConservationRule], current: dict[str, Resource], proposed: dict[str, Resource]
):
    for rule in rules:
        if not set(rule.resources).intersection(proposed):
            continue
        if not set(rule.resources) <= current.keys():
            raise CoordinationError(
                "incomplete_policy_reads",
                f"Invariant {rule.id} requires every resource in its read set",
                422,
            )
        before = []
        after = []
        for key in rule.resources:
            old = current[key].value.get(rule.field)
            new = proposed.get(key, current[key]).value.get(rule.field)
            # Integers avoid floating-point accounting ambiguity. bool is not an amount.
            if type(old) is not int or type(new) is not int:
                raise CoordinationError(
                    "invariant_violation",
                    f"Invariant {rule.id} requires integer field {rule.field} on {key}",
                    422,
                )
            before.append(old)
            after.append(new)
        if sum(before) != sum(after):
            raise CoordinationError(
                "invariant_violation",
                f"Invariant {rule.id}: total {rule.field} must remain {sum(before)}, proposed {sum(after)}",
                422,
            )
