# ReplayForge Design Documentation

Status: implemented end-to-end vertical slice with verified genuine discovery, deterministic replay, exceptional outcomes, tenant reuse, and same-session human handoff evidence.

ReplayForge turns one successful, model-driven interaction with a real user interface into a typed, reviewable capability that can be replayed without a model in the decision loop. The system is designed for stable but heterogeneous bank and credit-union back-office applications where runtime exceptions, safety, auditability, and human intervention matter more than raw browsing breadth.

## Reading order

1. [Product specification](01-product-spec.md) — users, workflows, scope, and acceptance criteria.
2. [Architecture](02-architecture.md) — system boundaries, modules, dependencies, and runtime topology.
3. [Capability artifact](03-capability-artifact.md) — the durable capability contract and versioning model.
4. [Discovery and replay](04-discovery-and-replay.md) — model-driven discovery and deterministic execution semantics.
5. [Safety and data handling](05-safety-and-data.md) — policy enforcement, risk, secrets, PII, and evidence redaction.
6. [Human handoff](06-human-handoff.md) — escalation, control ownership, same-session operation, and resumption.
7. [Frontend and UX](07-frontend-ux.md) — the production-quality control-plane experience.
8. [API contract](08-api-contract.md) — public HTTP, WebSocket, and result interfaces.
9. [Testing and evidence](09-testing-and-evidence.md) — verification layers and required demonstration artifacts.
10. [Implementation plan](10-implementation-plan.md) — ordered milestones and completion gates.
11. [Requirement traceability](11-requirement-traceability.md) — assignment requirement to design/test/evidence mapping.
12. [Decisions and cuts](12-decisions-and-cuts.md) — explicit trade-offs, rejected alternatives, and boundaries.

## Governing principles

- The model discovers; the artifact defines the reusable capability; replay executes it deterministically.
- Every model, browser, database, storage, and transport dependency sits behind a typed port.
- Modularity means explicit ownership and replaceable adapters, not additional deployable services.
- Replay is correct only when it proves a checkpoint and returns a typed outcome.
- Business outcomes, recoverable conditions, and hard failures are separate concepts.
- Safety policy is evaluated before every action, including actions proposed during discovery.
- Sensitive values are redacted before persistence, not only before display.
- Human takeover uses the same live session and an explicit single-owner control lease.
- The product should look and behave like modern operational infrastructure while remaining straightforward to run and explain.
- No feature is added solely to appear sophisticated. Every dependency and screen must serve an evaluated requirement.

## Locked baseline

| Concern | Decision |
|---|---|
| Architecture | Modular monolith with ports and adapters |
| Runtime | Python 3.12, FastAPI, Pydantic, Playwright |
| Control plane | Next.js App Router, strict TypeScript, Tailwind, shadcn/ui |
| Target | Synthetic member-servicing web application |
| Primary capability | Look up a member's current savings balance |
| Discovery | Screenshot-led model loop with normalized actions |
| Replay | Locator-led, model-free execution |
| Artifact | Versioned YAML validated by Pydantic and JSON Schema |
| Metadata | PostgreSQL through repository ports |
| Evidence | Redacted files through an evidence-store port |
| Live provider | OpenAI reference adapter; provider-neutral core |
| Handoff | Embedded live Chromium session with an exclusive control lease |
| Stretch scope | Cross-tenant reuse and agent-facing capability invocation |

## Definition of done

ReplayForge is not complete because its UI renders or because a browser script succeeds once. It is complete only when a reviewer can run a genuine discovery, inspect the resulting artifact, invoke a model-free replay, observe typed error handling, take over the same session, resume it, and verify redacted evidence using documented commands from a clean clone.
