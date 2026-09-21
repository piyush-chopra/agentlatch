"""Run with `uv run python examples/external_worker.py` against a running server."""

import os
import uuid

import httpx

run = f"external-{uuid.uuid4().hex[:8]}"
key = f"{run}/counter"
owner = "example-worker"
headers = (
    {"Authorization": f"Bearer {os.environ['AGENTLATCH_API_TOKEN']}"}
    if os.getenv("AGENTLATCH_API_TOKEN")
    else {}
)


def call(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return response.json()


with httpx.Client(base_url="http://127.0.0.1:8000", headers=headers) as client:
    call(client, "POST", "/api/resources", json={"key": key, "value": {"count": 0}})
    call(client, "POST", "/api/workflows", json={"id": run})
    attempt = call(
        client,
        "POST",
        f"/api/workflows/{run}/attempts",
        json={"operation_id": "add", "owner": owner},
    )
    snapshot = call(client, "GET", "/api/snapshot", params={"key": key})[key]
    # A real agent would produce this value from the snapshot before acquiring the lease.
    plan = {"writes": [{"key": key, "value": {"count": snapshot["value"]["count"] + 1}}]}
    lease = call(client, "POST", "/api/leases", json={"owner": owner, "resources": [key]})
    try:
        payload = {
            "workflow_id": run,
            "operation_id": "add",
            "owner": owner,
            "attempt_id": attempt["attempt_id"],
            "lease_token": lease["token"],
            "reads": {
                key: {"version": snapshot["version"], "schema_version": snapshot["schema_version"]}
            },
            "plan": plan,
        }
        print(call(client, "POST", "/api/commits", json=payload))
        print("Identical retry:", call(client, "POST", "/api/commits", json=payload))
    finally:
        call(client, "DELETE", f"/api/leases/{lease['token']}", params={"owner": owner})
