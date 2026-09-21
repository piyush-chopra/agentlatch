# CrewAI and LLM providers

Install the optional CrewAI dependencies:

```sh
uv sync --extra crew --group dev
cp .env.example .env
```

Use `uv run --extra crew ...` for CrewAI commands so uv retains the optional dependencies. The Python CLI loads `.env`. Each attempt creates a two-agent CrewAI team: a task specialist drafts a structured Plan and a state reviewer checks/corrects it against the original snapshot; AgentLatch coordinates these independently executing crews. A single shared Crew does not control transaction order.

## Selected runtime: Gemma 4 31B through Ollama Cloud

The current workspace uses the existing `gemma4:31b-cloud` Ollama tag:

```dotenv
AGENTLATCH_MODEL=ollama_chat/gemma4:31b-cloud
AGENTLATCH_BASE_URL=http://localhost:11434
AGENTLATCH_ENABLE_CREW=true
AGENTLATCH_LOCAL_ONLY=false
```

The client endpoint is local, but **inference runs in Ollama's cloud**. Ollama handles its own account/session authentication. Start/sign in to Ollama if needed and use `ollama pull gemma4:31b-cloud` to register the tag. AgentLatch does not need an OpenAI API key for this route.

`GET /api/runtime` reports model, provider, inference location, readiness, and team roles without exposing credentials. The console selects CrewAI by default when enabled and labels cloud inference explicitly. Readiness checks installation and the Ollama model inventory; successful inference still depends on provider availability and account limits.

Each workflow task has a **specialist → reviewer** crew; independent workflow tasks run concurrently. The reviewer receives the original snapshot and the specialist's proposal to avoid applying the change twice. Both produce structured Plans. The coordinator remains the sole authority for versions, schemas, leases, and invariants. Model review is additional reasoning, not a replacement for deterministic checks.

The adapter explicitly selects CrewAI's LiteLLM route for Ollama chat. This avoids native OpenAI-compatible routing of Ollama-specific options in CrewAI 1.15.22.

## Fully local Ollama alternative

Start Ollama and pull a model separately:

```sh
ollama pull gemma4:12b
```

```dotenv
AGENTLATCH_MODEL=ollama_chat/gemma4:12b
AGENTLATCH_LOCAL_ONLY=true
AGENTLATCH_BASE_URL=http://localhost:11434
AGENTLATCH_ENABLE_CREW=true
```

Then run `uv run --extra crew agentlatch demo --mode crew`, or `uv run --extra crew agentlatch serve` and select CrewAI in the console. Ollama uses CrewAI's LiteLLM integration. Local model quality and hardware determine output quality and latency; invalid structured output fails safely without committing.

## Cloud providers

Set the model ID available in your provider account. These are configuration examples, not a promise of account access or model longevity. A ChatGPT subscription is not an OpenAI API key.

| Provider | AGENTLATCH_MODEL example | Key variable |
| --- | --- | --- |
| OpenAI | `openai/gpt-4o-mini` | `OPENAI_API_KEY` |
| Anthropic | `anthropic/claude-sonnet-4-20250514` | `ANTHROPIC_API_KEY` |
| Gemini | `gemini/gemini-2.5-flash` | `GEMINI_API_KEY` |

For direct cloud-provider APIs, set `AGENTLATCH_LOCAL_ONLY=false` and leave `AGENTLATCH_BASE_URL` empty for cloud defaults. Enable console model runs with `AGENTLATCH_ENABLE_CREW=true`; otherwise only scripted runs are accepted over HTTP. An explicit CLI `--mode crew` is its own opt-in.

## Other local / compatible servers

For an OpenAI-compatible endpoint such as a locally hosted inference server:

```dotenv
AGENTLATCH_MODEL=openai/your-served-model-name
AGENTLATCH_BASE_URL=http://127.0.0.1:1234/v1
AGENTLATCH_API_KEY_ENV=LOCAL_LLM_KEY
LOCAL_LLM_KEY=local-placeholder-if-your-server-requires-one
```

`AGENTLATCH_API_KEY_ENV` names the environment variable; it is not the secret itself. The worker maps known provider prefixes to their standard key variables when this override is absent. All agents currently share one server-level model configuration; per-task model routing is future work.

## Bounds and privacy

- CrewAI: delegation disabled, memory disabled, cache disabled, no mutation tools, max iterations 3, retry limit 1.
- LLM request timeout: 180 seconds; token output cap: 2,048. Ollama reasoning is disabled for compact structured proposals. Downloaded local models use an 8,192-token context option; cloud tags retain provider context defaults.
- Agent execution limit: 200 seconds per agent; the outer workflow deadline independently limits the whole worker process. CrewAI demonstration runs get 900 seconds for model loading, review, and conflict retries. Custom workflows keep their explicitly configured deadline.
- Telemetry disabled for CrewAI workers. Model calls still use the selected provider's network endpoint.
- Worker cancellation terminates the subprocess; this does not cancel billing for a remote request already received.
- Generic provider exception messages are kept out of persistent events to avoid accidentally recording credentials.
- API keys are read only from server environment/.env, which is gitignored.

Sources: [CrewAI LLM connections](https://docs.crewai.com/en/learn/llm-connections), [task output models](https://docs.crewai.com/en/concepts/tasks), [agent configuration](https://docs.crewai.com/en/concepts/agents).

Local-only mode rejects remote provider prefixes, cloud-backed Ollama tags, and non-loopback endpoints before model execution. It also rejects embedding-only tags. Source for Ollama chat options: [Ollama chat API](https://docs.ollama.com/api/chat).
