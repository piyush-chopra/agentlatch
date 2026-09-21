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

Not validated in this session: live Ollama/cloud generation (no provider run configured), Docker execution, load/failover benchmarks, or browser visual/interactivity inspection (no browser automation surface was available). The frontend was type-checked and built, and its assets were served successfully; this is not a claim of visual QA.

For current CI results, consult [GitHub Actions](https://github.com/piyush-chopra/agentlatch/actions). Run commands are listed in [operations](operations.md).
