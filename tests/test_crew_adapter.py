"""Real CrewAI classes with a fake LLM: no credentials or network calls."""

import pytest


def test_crew_structured_proposal(monkeypatch):
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    monkeypatch.setenv("CREWAI_TELEMETRY_DISABLED", "true")
    crewai = pytest.importorskip("crewai")
    from agentlatch import crew_worker

    calls = []

    class FakeLLM(crewai.BaseLLM):
        def __init__(self):
            super().__init__(model="fake", temperature=0)

        def call(self, messages, tools=None, callbacks=None, available_functions=None, **kwargs):
            calls.append(str(messages))
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

    assert len(calls) >= 2
    assert any("State consistency reviewer" in call for call in calls)


def test_ollama_uses_litellm_chat_route(monkeypatch):
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    crewai = pytest.importorskip("crewai")
    from agentlatch.crew_worker import build_llm

    monkeypatch.setenv("AGENTLATCH_MODEL", "ollama_chat/gemma4:31b-cloud")
    monkeypatch.setenv("AGENTLATCH_BASE_URL", "http://localhost:11434")
    monkeypatch.delenv("AGENTLATCH_API_KEY_ENV", raising=False)
    llm = build_llm()
    assert type(llm) is crewai.LLM
    assert llm.model == "ollama_chat/gemma4:31b-cloud"
    assert "num_ctx" not in llm.additional_params
