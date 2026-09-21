import io
import json

import pytest

from agentlatch import runtime
from agentlatch.errors import CoordinationError


@pytest.fixture(autouse=True)
def local(monkeypatch):
    monkeypatch.setenv("AGENTLATCH_MODEL", "ollama_chat/gemma4:12b")
    monkeypatch.setenv("AGENTLATCH_LOCAL_ONLY", "true")
    monkeypatch.setenv("AGENTLATCH_ENABLE_CREW", "true")
    monkeypatch.setenv("AGENTLATCH_BASE_URL", "http://localhost:11434")
    monkeypatch.setattr(runtime.importlib.util, "find_spec", lambda name: True)


def models(monkeypatch, items):
    monkeypatch.setattr(
        runtime, "urlopen", lambda *a, **k: io.BytesIO(json.dumps({"models": items}).encode())
    )


def test_installed_local_model_ready(monkeypatch):
    models(monkeypatch, [{"name": "gemma4:12b", "capabilities": ["completion"]}])
    result = runtime.require_runtime()
    assert result["ready"] and result["default_mode"] == "crew"
    assert result["team"] == ["Task specialist", "State reviewer"]
    assert "api_key" not in result


def test_cloud_backed_tag_blocked(monkeypatch):
    models(monkeypatch, [{"name": "gemma4:12b", "remote_host": "https://ollama.com"}])
    with pytest.raises(CoordinationError, match="cloud inference"):
        runtime.require_runtime()


def test_embedding_model_blocked(monkeypatch):
    models(monkeypatch, [{"name": "gemma4:12b", "capabilities": ["embedding"]}])
    assert not runtime.runtime_status()["ready"]


def test_missing_model_help(monkeypatch):
    models(monkeypatch, [])
    assert "ollama pull gemma4:12b" in runtime.runtime_status()["message"]


def test_remote_endpoint_blocked_before_network(monkeypatch):
    monkeypatch.setenv("AGENTLATCH_BASE_URL", "https://example.com")
    monkeypatch.setattr(
        runtime, "urlopen", lambda *a, **k: pytest.fail("Must not call remote endpoint")
    )
    assert not runtime.runtime_status()["ready"]


def test_cloud_provider_blocked_in_local_mode(monkeypatch):
    monkeypatch.setenv("AGENTLATCH_MODEL", "openai/model")
    assert not runtime.runtime_status()["ready"]


def test_cloud_tag_allowed_when_explicitly_configured(monkeypatch):
    monkeypatch.setenv("AGENTLATCH_LOCAL_ONLY", "false")
    monkeypatch.setenv("AGENTLATCH_MODEL", "ollama_chat/gemma4:31b-cloud")
    models(monkeypatch, [{"name": "gemma4:31b-cloud", "remote_host": "https://ollama.com"}])
    result = runtime.require_runtime()
    assert result["inference_location"] == "cloud"
    assert "cloud" in result["message"]
