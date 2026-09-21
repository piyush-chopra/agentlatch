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


def enforce_rules(rules, current, proposed):
    from .coordinator import digest

    enforce_conservation([r for r in rules if r.kind == "conserve_total"], current, proposed)
    for rule in rules:
        if rule.kind == "conserve_total" or not set(rule.resources).intersection(proposed):
            continue
        if not set(rule.resources) <= current.keys():
            raise CoordinationError(
                "incomplete_policy_reads", f"Rule {rule.id} needs all dependencies", 422
            )
        for key in rule.resources:
            old = current[key]
            new = proposed.get(key, old)
            if rule.kind == "schema_allowlist":
                valid = digest(new.json_schema) in rule.allowed_hashes
            else:
                before, after = old.value.get(rule.field), new.value.get(rule.field)
                valid = type(before) is int and type(after) is int
                if valid:
                    valid = (
                        (rule.minimum is None or after >= rule.minimum)
                        and (rule.maximum is None or after <= rule.maximum)
                        and (rule.max_delta is None or abs(after - before) <= rule.max_delta)
                    )
            if not valid:
                raise CoordinationError(
                    "invariant_violation", f"Rule {rule.id} rejected resource {key}", 422
                )
