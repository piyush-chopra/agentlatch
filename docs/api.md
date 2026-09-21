# API reference

Base URL: `http://127.0.0.1:8000`. Interactive contract: `/docs`; machine-readable schema: `/openapi.json`. Strict Pydantic models reject unknown fields. API paths below require `Authorization: Bearer <AGENTLATCH_API_TOKEN>` when that server variable is set. `/health` and UI assets are public.

## Routes

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Process health and version |
| GET | `/api/state` | Resources, latest 100 workflows, active leases, event counters |
| GET | `/api/events?after=0&limit=200&workflow_id=…` | Ascending event cursor; max limit 1,000 |
| POST | `/api/resources` | Create a resource, 201; existing key returns 409 |
| GET | `/api/snapshot?key=a&key=b` | One consistent read of all requested resources |
| POST | `/api/workflows` | Create a low-level coordinator workflow, 201 |
| POST | `/api/workflows/{id}/attempts` | Reserve budget for owner and operation |
| POST | `/api/leases` | Acquire all resources or none |
| DELETE | `/api/leases/{token}?owner=…` | Release only matching owner/token rows |
| POST | `/api/commits` | Atomic validated state transition |
| POST | `/api/runs` | Schedule a high-level workflow, 202 |
| GET | `/api/runs/{id}` | Persisted status, limits, and specification |
| POST | `/api/runs/{id}/cancel` | Close run; cancel local active worker |
| POST | `/api/demos` | Start a fresh namespaced race or schema example, 202 |

## Low-level request contracts

Create resource:

```json
{"key":"inventory","value":{"count":10},"json_schema":{"type":"object","properties":{"count":{"type":"integer","minimum":0}},"required":["count"],"additionalProperties":false}}
```

Create workflow:

```json
{"id":"run-001","max_steps":30,"timeout_seconds":300,"repeat_limit":3,"specification":{}}
```

Reserve attempt (`POST /api/workflows/run-001/attempts`):

```json
{"operation_id":"increment","owner":"inventory-worker"}
```

Response: `{"attempt_id":1}`. Do this before planning, including retries.

Acquire lease:

```json
{"owner":"inventory-worker","resources":["inventory"],"ttl_seconds":15}
```

Response includes `owner`, monotonically increasing `token`, sorted unique `resources`, and `expires_at`. Default TTL is 15 seconds; allowed range is 0.05–300. Do not acquire before an LLM call.

Commit:

```json
{
  "workflow_id":"run-001",
  "operation_id":"increment",
  "owner":"inventory-worker",
  "attempt_id":1,
  "lease_token":1,
  "reads":{"inventory":{"version":1,"schema_version":1}},
  "plan":{"writes":[{"key":"inventory","value":{"count":11},"json_schema":null}],"rationale":"One item received"}
}
```

A successful response contains `operation_id` and updated `resources` indexed by key. The numeric token and attempt ID above are illustrative; use the actual returned values. Versions come from the actual snapshot, never from a guess.

## External worker protocol

1. Register the workflow and resources once.
2. Reserve an attempt before each model call.
3. Read **every** dependency in one snapshot call.
4. Produce and validate a plan using those values and schemas.
5. Acquire the entire read set in one lease request.
6. Send the plan, original read stamps, lease token, and attempt ID to commit.
7. Release in `finally`. On conflict, use fresh context and a newly charged attempt.
8. On uncertain network delivery, retry the exact same commit payload to retrieve its receipt.

See [the runnable httpx example](../examples/external_worker.py). The low-level API trusts clients to declare complete read dependencies. A shared bearer token does not enforce per-agent write permissions. The built-in engine additionally enforces workflow task scope.

## High-level runs

```json
{"spec":{"name":"Example","resources":[],"tasks":[{"id":"agent-a","role":"Inventory specialist","goal":"Increment count","reads":["inventory"],"writes":["inventory"],"action":"increment"}]},"mode":"scripted","run_id":"optional-stable-id"}
```

`mode` is `scripted` or `crew`. Crew mode from the API requires `AGENTLATCH_ENABLE_CREW=true`. `POST /api/demos` accepts `{"scenario":"schema","mode":"scripted"}` or scenario `race`.

## Errors

Domain errors return `{"code":"…","message":"…"}`. Invalid request shapes use FastAPI's standard 422 `detail` response.

| Code | HTTP | Handling |
| --- | --- | --- |
| version_conflict | 409 | Fresh snapshot, new attempt, replan |
| lease_busy / stale_lease | 409 | Release when applicable; bounded retry with new context |
| idempotency_mismatch | 409 | Do not reuse a committed operation ID with different content |
| blind_write / invalid_attempt | 409 | Correct worker protocol |
| loop_detected / step_budget_exceeded | 409 | Stop; inspect workflow behavior |
| deadline_exceeded / workflow_closed | 409 | Stop; run cannot accept new changes |
| workflow_mismatch / run_active | 409 | Correct run ID / specification or wait |
| resource_exists | 409 | Read existing state; do not overwrite initialization |
| resource_missing / workflow_missing | 404 | Initialize missing identifiers |
| schema_violation / invalid_schema / scope_violation | 422 | Correct plan or schema |
| crew_disabled | 422 | Configure server opt-in |
| unauthorized | 401 | Supply configured bearer token |

Rejected commit events are persisted after transaction rollback. Errors earlier in planning or lease acquisition are logged by the built-in engine as task failures/retries, not as rejected commits.
