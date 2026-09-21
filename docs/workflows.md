# Workflow specification and extension guide

A workflow is a JSON document validated by `WorkflowSpec`. Run it with:

```sh
uv run agentlatch run examples/inventory.json
uv run --extra crew agentlatch run examples/inventory.json --mode crew
```

## Workflow fields

| Field | Default | Meaning |
| --- | --- | --- |
| name | required | Human-readable label |
| resources | `[]` | Initial resource objects; create only missing keys |
| tasks | required | 1–100 uniquely named tasks |
| max_steps | 30 | Workflow attempt budget, 1–10,000 |
| timeout_seconds | 300 | Absolute execution deadline from first creation, up to 86,400 |
| repeat_limit | 3 | Maximum identical state transition repetitions |
| invariants | `[]` | Up to 32 deterministic conservation rules |

Re-running with a new run ID operates on **existing** resources; initialization does not reset them. Demo resource keys are randomized so demonstrations remain isolated. Resource deletion and reset are deliberately absent.

## Task fields

| Field | Default | Meaning |
| --- | --- | --- |
| id | required | Stable logical operation ID within a workflow |
| role | required | CrewAI specialist role |
| goal | required | Natural-language instruction for CrewAI |
| reads | required | 1–64 dependency keys, including every write target |
| writes | `[]` | Exact permitted/required replacement targets |
| depends_on | `[]` | IDs that must commit first |
| max_attempts | 4 | Durable task-level cap, up to 20 |
| action | noop | Offline simulator: increment, migrate, copy, transfer, noop |
| field | count | Offline increment field name |
| amount | 1 | Offline increment amount |

The scripted planner is an explicit deterministic simulation and does not interpret arbitrary natural language. `migrate` is specific to the example: rename `count` to `available` and replace its schema. `copy` writes an `observed` object from the first read-only source. `noop` preserves replacement values. Real CrewAI uses role, goal, scope, and snapshot; scripted action fields are not executable tools.

## Scheduling

Tasks with no dependencies start together, limited to four in-flight tasks by default. The current scheduler uses ready-task waves: it waits for a wave to finish before launching the next wave. A failed task causes its dependents to be skipped; independent tasks may still commit. Workflows are not all-or-nothing distributed transactions.

The engine checks that the proposal writes exactly the declared target set, then adds authoritative snapshot version stamps itself. The LLM cannot pick newer versions to disguise stale reasoning.

## Add a domain

1. Define shared resources and strict JSON Schemas for your domain.
2. Declare every read dependency, including configuration and contracts.
3. Describe agent roles and concrete goals.
4. Put ordering constraints in `depends_on`; independent tasks remain concurrent.
5. Start with the scripted planner or a custom deterministic planner and write failure tests.
6. Switch to CrewAI and evaluate domain correctness independently of concurrency safety.

Custom planners implement `async def plan(task, snapshot) -> Plan`. They must not perform external mutations. The engine owns commit. See `ScriptedPlanner` as a minimal implementation.

## Crash recovery

```sh
uv run agentlatch run examples/inventory.json --run-id stable-run-001
# After an abrupt process crash, use the identical command.
```

Committed task receipts are skipped. Pending attempts remain charged. Existing task attempt counts, shared budget, and original absolute deadline are preserved. A completed/failed/cancelled run ID is terminal. A new run ID intentionally starts new logical operations; it can repeat business actions, so choose IDs carefully.

## Deterministic conservation rules

Run `uv run agentlatch run examples/transfer.json` to move stock atomically between warehouses. Add this to a workflow:

```json
{"invariants":[{"id":"stock-conservation","kind":"conserve_total","resources":["warehouse-a","warehouse-b"],"field":"count"}]}
```

Any task writing a participating resource must read **all** resources in the rule. The coordinator checks the integer total before and after the complete proposed write set inside the same transaction. Missing dependencies, non-integer fields, and changed totals are rejected without partial writes. Unchanged resources contribute their current values. JSON Schema can separately prohibit negative inventory.

The scripted `transfer` action subtracts `amount` from `writes[0]` and adds it to `writes[1]`, using `field`. Real agents follow their natural-language goal. Rules live in the immutable persisted workflow specification; model plans cannot alter them. These are workflow-scoped controls, not a database-wide authorization policy: another trusted workflow without a rule is not bound by it.
