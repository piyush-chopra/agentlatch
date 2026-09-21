import time

from fastapi.testclient import TestClient

from agentlatch.api import create_app


def test_api_demo_and_events(tmp_path):
    with TestClient(create_app(str(tmp_path / "api.db"))) as client:
        assert client.get("/health").json()["status"] == "ok"
        response = client.post("/api/demos", json={"scenario": "schema"})
        assert response.status_code == 202
        run_id = response.json()["id"]
        for _ in range(50):
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] != "running":
                break
            time.sleep(0.05)
        assert run["status"] == "completed"
        assert client.get("/api/state").json()["resources"][0]["value"] == {"available": 11}
        events = client.get("/api/events").json()
        assert any(e["kind"] == "commit_rejected" for e in events)
        assert client.get(f"/api/events?after={events[-1]['sequence']}").json() == []
        assert client.post("/api/demos", json={"mode": "crew"}).status_code == 422
        assert client.get("/api/events?limit=100000").status_code == 422
        assert client.get("/api/snapshot?key=missing").status_code == 404


def test_bearer_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTLATCH_API_TOKEN", "test-token")
    with TestClient(create_app(str(tmp_path / "auth.db"))) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/state").status_code == 401
        assert (
            client.get("/api/state", headers={"Authorization": "Bearer test-token"}).status_code
            == 200
        )


def test_handoff_api_and_observability(tmp_path):
    with TestClient(create_app(str(tmp_path / "handoff.db"))) as client:
        response = client.post("/api/demos/handoff")
        assert response.status_code == 202
        run_id = response.json()["id"]
        for _ in range(100):
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] != "running":
                break
            time.sleep(0.03)
        assert run["status"] == "completed"
        assert set(run["task_outcomes"]) == {"producer", "reviewer"}
        deliveries = client.get("/api/deliveries").json()
        assert {d["kind"]: d["status"] for d in deliveries} == {
            "message": "delivered",
            "effect": "pending",
        }
        assert client.get("/api/executions").json()["backend"] == "sqlite"
        claim = client.post(
            "/api/deliveries/claim",
            json={"kind": "effect", "destination": "inventory-notifications", "owner": "test"},
        ).json()
        ack = {k: claim[k] for k in ["id", "owner", "token"]}
        assert client.post("/api/deliveries/ack", json=ack).status_code == 200
        assert client.post("/api/deliveries/ack", json={**ack, "token": 999}).status_code == 409
        assert (
            client.post(
                "/api/channels",
                json={"destination": "broken", "kind": "message", "json_schema": {"type": "wrong"}},
            ).status_code
            == 422
        )
