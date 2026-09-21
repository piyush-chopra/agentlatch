"""Real CrewAI classes with a fake LLM: no credentials or network calls."""

import pytest


def test_crew_structured_proposal(monkeypatch):
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    monkeypatch.setenv("CREWAI_TELEMETRY_DISABLED", "true")
    crewai = pytest.importorskip("crewai")
    from agentlatch import crew_worker

    class FakeLLM(crewai.BaseLLM):
        def __init__(self):
            super().__init__(model="fake", temperature=0)

        def call(self, messages, tools=None, callbacks=None, available_functions=None, **kwargs):
            return '{"writes":[{"key":"counter","value":{"count":1},"json_schema":null}],"rationale":"Increment by one"}'

        def supports_function_calling(self):
            return False

    monkeypatch.setattr(crew_worker, "build_llm", lambda: FakeLLM())
    result = crew_worker.produce(
        {
            "task": {
                "role": "Inventory specialist",
                "goal": "Increment count by one",
                "writes": ["counter"],
            },
            "snapshot": {
                "counter": {
                    "value": {"count": 0},
                    "version": 1,
                    "schema_version": 1,
                    "json_schema": {"type": "object"},
                }
            },
        }
    )
    assert result.writes[0].value == {"count": 1}
