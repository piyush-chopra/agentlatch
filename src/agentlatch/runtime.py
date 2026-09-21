"""Non-secret runtime discovery and explicit local-inference validation."""

import importlib.util
import json
import os
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from .errors import CoordinationError


def runtime_status() -> dict:
    model = os.getenv("AGENTLATCH_MODEL", "ollama_chat/gemma4:31b-cloud")
    enabled = os.getenv("AGENTLATCH_ENABLE_CREW", "false").lower() == "true"
    local_only = os.getenv("AGENTLATCH_LOCAL_ONLY", "false").lower() == "true"
    ollama = model.startswith(("ollama/", "ollama_chat/"))
    status = {
        "enabled": enabled,
        "model": model,
        "provider": "ollama" if ollama else model.split("/")[0],
        "local_only": local_only,
        "ready": False,
        "inference_location": "cloud",
        "default_mode": "crew" if enabled else "scripted",
        "team": ["Task specialist", "State reviewer"],
        "message": "",
    }
    if importlib.util.find_spec("crewai") is None:
        status["message"] = "Install CrewAI with uv sync --extra crew --group dev"
        return status
    if local_only and not ollama:
        status["message"] = "Local-only mode requires an Ollama model"
        return status
    if ollama:
        base_url = os.getenv("AGENTLATCH_BASE_URL") or "http://localhost:11434"
        parsed = urlparse(base_url)
        if local_only and (
            parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.scheme != "http"
        ):
            status["message"] = "Local-only mode requires an HTTP Ollama endpoint on localhost"
            return status
        try:
            with urlopen(base_url.rstrip("/") + "/api/tags", timeout=3) as response:
                models = json.load(response)["models"]
            tag = model.split("/", 1)[1]
            tag = tag if ":" in tag else tag + ":latest"
            found = next((m for m in models if m.get("name") == tag or m.get("model") == tag), None)
            if not found:
                status["message"] = f"Model is not installed. Run ollama pull {tag}"
                return status
            if local_only and (
                found.get("remote_host") or found.get("remote_model") or tag.endswith("-cloud")
            ):
                status["message"] = (
                    "This Ollama tag uses cloud inference; select a downloaded local model"
                )
                return status
            cloud_backed = bool(
                found.get("remote_host") or found.get("remote_model") or tag.endswith("-cloud")
            )
            status["inference_location"] = (
                "cloud"
                if cloud_backed
                else "local"
                if parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                else "server"
            )
            capabilities = found.get("capabilities", [])
            if capabilities and "completion" not in capabilities:
                status["message"] = "Select a text-generation model, not an embedding-only model"
                return status
        except (URLError, TimeoutError, ValueError, KeyError, OSError):
            status["message"] = (
                "Cannot reach Ollama. Start ollama serve and check the configured endpoint"
            )
            return status
    status["ready"] = True
    status["message"] = (
        f"Ollama {status['inference_location']} model available"
        if ollama
        else "Provider configured; credentials checked at execution"
    )
    return status


def require_runtime():
    status = runtime_status()
    if not status["ready"]:
        raise CoordinationError("runtime_unavailable", status["message"], 422)
    return status
