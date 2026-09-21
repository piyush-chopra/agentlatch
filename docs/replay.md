# Visualizing the problem and recovery

Open **Replay** in the console, or choose **Why coordination matters** from another page. The two lanes follow the same starting state and illustrative schedule: unchecked writes on the left, coordinator-managed commits on the right.

Choose a scenario, then play or advance one step at a time. Playback pauses at the intervention; choose Continue replay to see the outcome. Restart and the keyboard-accessible timeline allow revisiting any step. Nothing plays automatically on entry, and playback makes no model calls or database writes.

| Scenario | Failure illustrated | AgentLatch intervention |
| --- | --- | --- |
| Lost update | Two agents read 10 and both write 11 | Reject the stale proposal; refresh context and replan to reach 12 |
| Stale schema | One agent renames a field while another proposes the old shape | Check data/schema stamps atomically; retry using the current contract |
| Deadlock | Each agent holds one resource while waiting for the other | Acquire the complete declared resource set atomically or hold none |
| No-progress loop | Distinct operations repeatedly propose the same transition | Bound repeated transitions; stop with an explicit failure |

## Explanation versus evidence

The replay frames are explanatory data in `frontend/src/replay.ts`. They are not captured model traces, and the unsafe lane never bypasses the coordinator. Real scheduling and model output may differ.

Below the illustration, **Now, the evidence** uses actual events already loaded by the console. It selects the latest recorded `version_conflict`, then correlates a later retry and accepted commit by workflow and operation/task identifier. Sequence numbers expose the underlying records. Inspect this run opens its workflow in Overview. Missing records are labeled as unobserved; the bounded session event buffer is not a complete historical audit query.

The recorded evidence is independent of the selected explanatory scenario. It proves the displayed rejection/recovery events occurred, not that every illustrative scenario was exercised by that run.

## Boundaries

Protection applies to declared dependencies and writes routed through AgentLatch. It cannot detect every semantic mistake in a valid model proposal or coordinate arbitrary external side effects. Atomic lease acquisition avoids partial-resource hold-and-wait; it does not promise fairness or eliminate all database contention. Transition repetition detection bounds a specific class of loops; attempts and deadlines bound additional execution. A bounded failure is an honest outcome, not a successful workflow.

## Interface and implementation

`RaceReplay.tsx` renders agent actions, a commit checkpoint, versioned JSON state, narrated steps, playback controls, and the event evidence chain. It reuses the existing event polling feed without adding backend endpoints. The two lanes stack on narrow screens, colors are paired with text/icons, controls support keyboard input, and step narration uses a polite live region. See [architecture](architecture.md), [flow diagrams](flows.md), and [interface design](design.md).
