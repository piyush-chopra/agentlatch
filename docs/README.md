# AgentLatch documentation

Canonical repository: [piyush-chopra/agentlatch](https://github.com/piyush-chopra/agentlatch).

AgentLatch coordinates shared JSON state used by asynchronous agents. Python owns the backend and AI execution; React + TypeScript owns the console. The documents below describe the implemented MVP and explicitly distinguish future work.

## Reading paths

- **Run it:** [root quick start](../README.md) → [providers](providers.md) → [operations](operations.md).
- **See the problem:** [interactive replay guide](replay.md).
- **Understand it:** [architecture / HLD](architecture.md) → [flow diagrams / FLDs](flows.md) → [low-level design / LLD](low-level-design.md).
- **Integrate it:** [workflow guide](workflows.md) → [API reference](api.md) → [external worker example](../examples/external_worker.py).
- **Contribute:** [contribution guide](../CONTRIBUTING.md) → [architecture decisions](decisions.md) → [roadmap](roadmap.md).

## Terms

| Term | Meaning |
| --- | --- |
| Resource | A named JSON object, its JSON Schema, and two version counters |
| Snapshot | A consistent read of all resources declared by a task |
| Proposal / plan | Typed replacement values returned by an agent; not yet committed |
| Read set | All resources whose current versions the proposal depends on |
| Write set | Resources the task is allowed and required to replace |
| Lease | Short-lived, exclusive permission to commit against a set of resources |
| Fencing token | A monotonically increasing lease generation checked by the coordinator |
| Attempt | One durable unit of execution budget reserved before inference |
| Operation | A logical task result identified by `(workflow_id, operation_id)` |
| Receipt | The durable result of a successful operation, returned on duplicate delivery |
| Semantic staleness | An agent's assumptions conflict with a newer data/schema version |
| Deterministic coordinator | Code-controlled validation and commit rules; model output and concurrent arrival order are not deterministic |

## Source references

The implementation uses CrewAI's documented [LLM configuration](https://docs.crewai.com/en/learn/llm-connections) and [structured task output](https://docs.crewai.com/en/concepts/tasks). Frontend tooling follows [React](https://react.dev/learn/creating-a-react-app) and [Vite](https://vite.dev/guide/). These references describe upstream APIs; this repository's lockfiles determine the exact installed versions.

Implementation checks and untested boundaries are recorded in [verification](verification.md).

The [interface design guide](design.md) covers the iPad-inspired React redesign, typography, color tokens, accessibility decisions, and HIG references.
