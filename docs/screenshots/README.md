# Screenshot provenance

Captured on 2026-09-21 from the running AgentLatch application at localhost:8000. Desktop viewport: 1440 × 1100; tablet: 820 × 1180. Component captures use a taller viewport to keep the sticky toolbar outside the captured component. Browser chrome, bookmarks, credentials, and unrelated tabs are excluded.

The capture used an isolated headless Chrome session through Playwright, explicitly authorized by the user. It navigated the real React UI and consumed the existing local API data without interception, synthetic event injection, or database edits. It did not launch new model calls. There were no browser JavaScript errors during capture.

- `01`: workspace and configured CrewAI/Ollama Cloud runtime.
- `02–05`: explanatory replay frames, not live agent traces. Lost update is captured at its final outcome; schema race, deadlock, and loop at their intervention points.
- `06`: actual buffered rejection/retry/commit events for `run-577833676bd4`.
- `07–10`: actual workflow history, shared resources, resource details, and activity.
- `11`: unsubmitted custom-workflow form.
- `12`: responsive workspace at a tablet viewport.

The selected model `gemma4:31b-cloud` uses cloud inference through the local Ollama server. The images do not establish production scalability, distributed availability, or universal semantic correctness. Follow the README quick start to reproduce the UI; run identifiers and totals naturally differ across installations.

`13-reliability.png` was added after the reliability implementation. It shows an actual completed scripted handoff in the upgraded local workspace, its consumed message, and pending effect. No external dispatcher ran during capture. Desktop, 820px tablet, and 390px phone navigation were checked; no browser JavaScript errors or horizontal viewport overflow were observed.
