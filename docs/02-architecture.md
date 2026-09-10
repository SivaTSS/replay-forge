# Architecture

## 1. Architectural objective

ReplayForge must isolate policy and deterministic execution from the mechanisms used to perceive and operate a particular application. It must remain easy to run as a take-home project while presenting boundaries that can evolve toward multiple surfaces, tenants, and workers.

The selected architecture is a modular monolith with ports and adapters. It provides strong ownership and replaceability without distributed-system overhead.

## 2. System context

```text
Integration engineer ─┐
Human operator ───────┼──> ReplayForge control plane
Calling AI agent ─────┘             │
                                    │ typed HTTP/WebSocket contracts
                                    v
                         ReplayForge runtime service
                          │          │          │
                          │          │          └──> Model provider
                          │          └─────────────> PostgreSQL/evidence store
                          └────────────────────────> Target application surface
```

The calling agent interacts only with the capability catalog and invocation contract. It never receives direct browser control.

## 3. Deployable units

### 3.1 Control plane

A Next.js application responsible for presentation, navigation, forms, server-state synchronization, live run updates, artifact inspection, and operator control input.

It does not:

- Execute automation actions.
- Store provider secrets.
- Decide policy.
- Classify runtime outcomes.
- Mutate artifacts directly.

### 3.2 Demo bank

A separate Next.js application that behaves like a configurable vendor member-servicing product. It contains only synthetic data and deterministic fault controls.

It exists to provide a real, repeatable surface. ReplayForge may not call its internal data APIs to complete the capability. All task execution must occur through rendered UI interactions.

### 3.3 Runtime service

A Python/FastAPI service that owns domain behavior, browser sessions, model orchestration, artifact validation, replay, policy, handoff, and persistence coordination.

It is one process for the submission. Internal background tasks use structured concurrency; no external queue is introduced.

### 3.4 PostgreSQL

Stores queryable operational metadata:

- Application families and tenants
- Capability identities and immutable versions
- Run and session metadata
- Step/event indexes
- Intervention and control-lease state
- Evidence manifests and content hashes

Large screenshots and traces are not stored as database blobs.

### 3.5 Evidence store

The submitted adapter writes under a configured local evidence root. The port uses opaque evidence keys so an S3-compatible implementation can replace it without changing domain logic.

## 4. Backend module boundaries

### 4.1 `applications`

Owns application-family and tenant definitions, entry points, allowed origins, variant fingerprints, and overlay resolution.

Public services:

- Register or retrieve application family.
- Resolve effective tenant configuration.
- Validate that an artifact supports a tenant.
- Produce a surface fingerprint for drift comparison.

### 4.2 `discovery`

Owns goal-driven run orchestration and progress detection.

Dependencies:

- `ModelProvider`
- `SurfaceSession`
- `PolicyEvaluator`
- `EvidenceRecorder`
- `InterventionService`
- `ArtifactCompiler`

It may not depend on OpenAI, Playwright, SQLAlchemy, or filesystem classes directly.

### 4.3 `capabilities`

Owns the artifact model, validation, versioning, hashing, serialization, registry, and agent-facing metadata.

It treats provider transcripts as external evidence, never artifact content.

### 4.4 `replay`

Owns deterministic step execution, target resolution rules, wait/retry budgets, outcome classification, output binding, and checkpoint verification.

The package must not import any model interface. This is a structural guarantee that replay cannot invoke a model.

### 4.5 `policy`

Owns allowlist evaluation, action classification, approval requirements, data labels, redaction directives, and policy decision records.

Policy returns a decision object; adapters do not silently enforce independent rules that the audit trail cannot see.

### 4.6 `sessions`

Owns session lifecycle, surface creation, browser-context identifiers, expiry, and teardown.

Surface-specific details stay in adapters.

### 4.7 `interventions`

Owns escalation requests, exclusive control leases, operator claims, human actions, resume validation, and terminal disposition.

### 4.8 `evidence`

Owns evidence manifests, redaction-before-write, content hashes, retention classes, and secure lookup.

### 4.9 `runs`

Owns run identity, lifecycle state, terminal result, event indexing, and read models used by the control plane.

## 5. Dependency rule

```text
API/UI adapters ──────┐
Provider adapter ─────┤
Surface adapter ──────┼──> Application services ──> Domain models and ports
Persistence adapter ──┤
Evidence adapter ─────┘
```

Dependencies point inward. Domain modules contain no framework types. FastAPI request objects, SQLAlchemy models, OpenAI responses, Playwright handles, and WebSocket objects are converted at adapter boundaries.

Shared code is restricted to:

- Stable identifiers and time abstractions
- Result primitives
- Serialization-safe scalar types
- Cross-cutting tracing context

Feature-specific helpers remain with their owning module. There is no unrestricted `utils` package.

## 6. Primary ports

### `ModelProvider`

- Accepts goal, normalized observation, policy guidance, and action history.
- Returns a normalized action proposal plus provider evidence reference.
- Exposes provider/model metadata for run provenance.
- Has no replay-facing interface.

### `SurfaceDriver`

- Opens a session for a target and tenant.
- Captures normalized observations.
- Executes normalized surface actions.
- Resolves a control at a point or from a locator bundle.
- Detects dialogs and navigation.
- Produces screenshots and optional traces.
- Exposes live frames and accepts human input when supported.

### `PolicyEvaluator`

- Evaluates proposed action, current location, target, risk, and principal.
- Returns allow, deny, or require-human-approval.
- Returns stable reason codes and redaction directives.

### `CapabilityRepository`

- Stores immutable versions.
- Resolves a specific or current compatible version.
- Lists capabilities by application family and tenant.
- Prevents mutation of published content.

### `RunRepository`

- Creates runs and appends ordered events.
- Performs legal state transitions.
- Stores exactly one terminal result.
- Supports control-plane read models.

### `EvidenceStore`

- Writes sanitized bytes with metadata and content hash.
- Reads by opaque evidence key.
- Never accepts a payload that has not passed redaction.

### `ControlLeaseRepository`

- Creates a lease owned by automation.
- Claims, transfers, and releases ownership with version checks.
- Rejects stale or concurrent commands.

## 7. Runtime data flow

### 7.1 Discovery

```text
Create run
  -> resolve tenant and policy
  -> open surface session
  -> observe
  -> request normalized model action
  -> policy decision
  -> execute or intervene
  -> observe result and record locator/evidence
  -> repeat until completion/stopping condition
  -> compile artifact
  -> validate schema, safety, and checkpoint
  -> persist artifact version and terminal run result
```

### 7.2 Replay

```text
Validate invocation
  -> resolve immutable artifact and tenant overlay
  -> open surface session
  -> evaluate preconditions
  -> resolve unique target for step
  -> policy decision
  -> execute action
  -> verify postcondition
  -> classify known outcomes/recoveries
  -> verify final checkpoint and bind outputs
  -> persist terminal result and evidence manifest
```

### 7.3 Human handoff

```text
Automation detects escalation
  -> stop issuing actions
  -> capture state and create intervention
  -> transfer lease to human
  -> relay human input to same surface
  -> record human events
  -> operator requests resume
  -> transfer lease through RESUMING
  -> capture fresh observation and verify resume condition
  -> return lease to automation
```

## 8. State consistency

- Database transactions protect run transitions, intervention creation, and lease changes.
- Browser state cannot participate in a database transaction; therefore every action uses an explicit intent/result event pair.
- A run event is appended before and after an external side effect.
- If a process dies after acting but before recording success, replay must re-observe rather than blindly retry.
- Non-idempotent actions require an effect-verification checkpoint before any retry.
- Terminal results use compare-and-set behavior to prevent double completion.

## 9. Real-time transport

Durable state uses REST and TanStack Query. Ephemeral live state uses one WebSocket per observed session.

Server-to-client message classes:

- Session frame
- Run event
- Ownership transition
- Intervention update
- Heartbeat
- Stream warning

Client-to-server message classes:

- Pointer action
- Key action
- Text input
- Viewport acknowledgement
- Heartbeat

Every human-control message includes session ID, intervention ID, lease version, and monotonically increasing client sequence. Stale lease versions are rejected.

## 10. Failure containment

- A model-provider failure may fail discovery but cannot affect replay availability.
- A control-plane disconnect does not stop safe automation.
- A live-frame failure does not invalidate the browser session.
- Evidence-store failure prevents a run from claiming auditable success when required evidence cannot be persisted.
- PostgreSQL unavailability prevents new runs and reports readiness failure.
- One browser-session failure cannot terminate other runs.
- Tenant overlays are resolved and validated before browser creation.

## 11. Extension paths

### Desktop surface

A desktop adapter implements the same observation, normalized action, locator, and evidence ports using OS accessibility APIs, window identity, and screenshot coordinates. Artifacts identify surface-specific locator candidates without changing capability inputs, outputs, outcomes, or checkpoints.

### Distributed workers

If scale later requires workers, application services can be hosted behind a durable command queue. Run IDs, immutable artifacts, explicit state transitions, and evidence keys already define the boundary. The submission does not build this infrastructure.

### PostgreSQL and object storage

The evidence-store port can move payloads to S3-compatible storage while PostgreSQL continues to store metadata and hashes. Artifact YAML can also be mirrored to object storage without changing registry behavior.

## 12. Architecture acceptance criteria

- Domain tests can run without FastAPI, PostgreSQL, Playwright, or a model SDK.
- Replay imports no model-provider package.
- The Playwright adapter can be replaced by a fake surface in tests.
- The OpenAI adapter can be replaced without changing discovery domain logic.
- Ownership changes are impossible without a valid lease version.
- Every external effect has correlated run and trace identifiers.
- A single documented local topology runs the complete system.
