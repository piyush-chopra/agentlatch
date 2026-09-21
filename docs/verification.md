# Verification record

Verified during initial implementation on 2026-09-21 using Python 3.12 and Node 25 on macOS arm64.

| Check | Result |
| --- | --- |
| Python regression suite with CrewAI installed | 29 passed |
| Ruff static checks | Passed |
| React TypeScript checking and Vite production build | Passed |
| CrewAI Agent/Task/Crew with deterministic fake LLM | Typed proposal returned; no provider calls |
| FastAPI HTTP external worker example | Commit accepted; identical retry returned same receipt |
| Local `/health` and built React HTML | HTTP 200 |
| Initial GitHub CI | Passed Python 3.12/3.13 and React build |

The suite covers cross-process leases, contention, schema races, write skew, atomic rollback, fencing, receipts, recovery, budgets, cancellation, invalid schemas, workflow conservation policies, and API behavior. Upstream FastAPI/Starlette and CrewAI emit deprecation warnings; they did not cause test failures.

Not validated at the initial implementation checkpoint (superseded below): live Ollama/cloud generation (no provider run configured), Docker execution, load/failover benchmarks, or browser visual/interactivity inspection (no browser automation surface was available). The frontend was type-checked and built, and its assets were served successfully; this is not a claim of visual QA.

For current CI results, consult [GitHub Actions](https://github.com/piyush-chopra/agentlatch/actions). Run commands are listed in [operations](operations.md).

## Live multi-agent Ollama verification

On 2026-09-21, a real CrewAI schema-race workflow completed using `ollama_chat/gemma4:31b-cloud` through `http://localhost:11434`. This is Ollama **cloud** inference. Run `run-b0989d924d50` used two concurrent task crews (specialist + reviewer per crew), committed both logical operations in three budgeted attempts, rejected one stale proposal, and finished with `available: 11` at data version 3 / schema version 2. Elapsed workflow time was approximately 20.4 seconds. This supersedes the initial record's untested-live-provider limitation; Docker and load/failover limits remain.

A downloaded local `gemma4:12b` model was discovered but a successful fully on-device workflow is not claimed. The user selected the cloud model before completing that validation. The test suite additionally covers multi-agent CrewAI output, explicit LiteLLM routing, model discovery, local-only cloud rejection, and cloud opt-in.

## Reliability release verification (2026-09-21)

- SQLite and a real isolated local PostgreSQL instance: 66 tests passed with CrewAI installed. Tests include competing schedulers, claim expiry/fencing, restart recovery, kill-before-commit rollback, kill-after-commit receipt recovery, immutable channels, atomic message handoff, retry/dead-letter handling, lost receiver response with stable idempotency ID, and numeric/schema policy rejection.
- Live `gemma4:31b-cloud` CrewAI schema race on the new durable engine: `run-d5fc3d478ede` completed, ending at `available: 11`, data v3/schema v2. This is cloud inference through local Ollama.
- Live CrewAI message handoff: `run-c7c887e7fe32` completed. Producer and reviewer exchanged one durable message; its ACK committed with the reviewer update. One effect remained pending with zero delivery attempts; no external dispatcher was started.
- The HTTP adapter was tested against a local receiver: stable idempotency header and envelope body verified, redirects rejected.
- React TypeScript/Vite build passed. Playwright navigation verified a completed scripted handoff, an acknowledged message, a queued effect, and no page errors. Tablet (820px) and phone (390px) viewports showed no horizontal page overflow.
- SQLite and PostgreSQL contention smoke workloads each completed 8 concurrent workflows / 16 increments on one resource with final value exactly 16. See [raw measurements and scope](benchmarks/README.md).
- PostgreSQL reliability CI job and optional Compose topology added. Local native PostgreSQL was executed; Docker container execution, remote network partitions, replicated-database failover, and production-scale load are not claimed as tested.

The original screenshots and initial 29-test record are historical checkpoints. The reliability tests, benchmark artifacts, and screenshot 13 describe the newer implementation. Provider and framework deprecation warnings remain nonfatal.

CI exposed a slow-start deadline race on Python 3.12: expiry before the initial claim could escape as an exception. The engine now returns the persisted terminal failure without invoking a planner. A deterministic regression test forces this boundary without depending on machine speed.
