# Architecture

## System shape

ReplayForge is a modular monolith with two separate Next.js applications: one is the synthetic target, the other is the operator console.

```mermaid
%%{init: {"htmlLabels":false,"themeVariables":{"lineColor":"#6E7781","signalColor":"#6E7781"},"flowchart":{"curve":"linear"},"sequence":{"wrap":true}}}%%
flowchart LR
    subgraph Clients[Clients]
        direction TB
        C([Calling agent or curl])
        O([Human operator])
    end

    subgraph Runtime[ReplayForge runtime · one Python process]
        direction TB
        API[FastAPI adapter]
        API --> RS[Replay application service]
        API --> DS[Discovery application service]
        API --> SS[Discovery suite service]
        API --> IS[Intervention service]
        RS --> RE[Replay engine]
        DS --> DE[Discovery engine]
        SS --> DS
        DE --> MP[ModelProvider port]
        RE --> SP[Surface ports]
        DE --> SP
        RE --> PE[Policy evaluator]
        DE --> PE
        RE --> EJ[Run journal]
        DE --> EJ
    end

    subgraph Adapters[Adapters and local dependencies]
        direction TB
        OA[OpenAI adapter]
        PW[Playwright adapter]
        DB[Demo bank]
        FS[(Local evidence store)]
        AR[(Application registry YAML)]
        LF[Local Langfuse]
    end

    C -->|JSON / HTTP| API
    O -->|Next.js proxy / HTTP| API
    MP --> OA
    SP --> PW
    PW --> DB
    EJ --> FS
    API --> AR
    PW --> AR
    OA --> LF

```

### Deployable units

| Unit | Port | Responsibility | Does not do |
|---|---:|---|---|
| FastAPI runtime | `8000` | Discovery, replay, policy, sessions, evidence, intervention | Render the target or operator UI |
| Control plane | `3000` | Claim and operate a paused live session | Browse runs or edit capabilities |
| Demo bank | `3001` | Synthetic two-tenant target and controlled faults | Expose a task-completion API |
| Langfuse stack | `3100` | Local model-call metrics for discovery | Participate in replay |

PostgreSQL is not part of the implemented runtime. Capability, lease, intervention, journal, application-registration, and discovery-suite metadata live in process memory. Sanitized evidence is written to disk. The application catalog is checked-in YAML so onboarding is reviewable; its repository interface is the later PostgreSQL seam.

`config/applications.yaml` is the onboarding boundary. It owns only application facts: a
credential-free origin, tenant variants, symbolic entry points, route aliases, readiness
landmarks, and policy ceilings. A new task in a registered application therefore supplies a
goal and a fresh discovery suite; it does not add task code or route constants. A new web app
adds one reviewed registration. A new surface contract still requires a surface adapter.

## Dependency direction

```text
FastAPI / OpenAI / Playwright / filesystem
                    │ implement
                    ▼
           typed ports and services
                    │ use
                    ▼
       domain models and deterministic rules
```

The key rule is structural: `replay` does not import `providers`. A test enforces that rule. Replacing OpenAI cannot alter replay; replacing Playwright does not require changing the replay state machine.

| Boundary | Port or contract | Current adapter |
|---|---|---|
| Model decision | `ModelProvider` | OpenAI Responses API |
| UI perception/action | `SurfaceDriver`, `SurfaceSession` | Synchronous Playwright |
| Artifact storage | `CapabilityRegistry` | Thread-safe memory loaded from YAML |
| Evidence bytes | `EvidenceStore` | Atomic local files |
| Run audit | `RunRecorder` | In-memory journal backed by evidence files |
| Control ownership | `ControlLeaseRepository` | Thread-safe compare-and-swap memory |

## Module ownership

| Module | Owns | Main entry |
|---|---|---|
| `api` | HTTP validation, correlation IDs, safe error mapping | [`api/app.py`](../backend/src/replayforge/api/app.py) |
| `capabilities` | Artifact aggregate, YAML, schema, hashing, immutable versions | [`capabilities/models.py`](../backend/src/replayforge/capabilities/models.py) |
| `applications` | Validated application onboarding, launch resolution, route aliases, and policy ceilings | [`applications/models.py`](../backend/src/replayforge/applications/models.py) |
| `discovery` | Contract planning, bounded observe-decide-act loop, and generic trace compilation | [`discovery/engine.py`](../backend/src/replayforge/discovery/engine.py) |
| `replay` | Model-free step interpreter, recovery, outcomes, checkpoint | [`replay/engine.py`](../backend/src/replayforge/replay/engine.py) |
| `surfaces` | Technology-neutral session port and Playwright implementation | [`surfaces/ports.py`](../backend/src/replayforge/surfaces/ports.py) |
| `policy` | Allowlist intersection, risk inference, decision records | [`policy/evaluator.py`](../backend/src/replayforge/policy/evaluator.py) |
| `interventions` | Intervention lifecycle and exclusive lease transitions | [`interventions/service.py`](../backend/src/replayforge/interventions/service.py) |
| `evidence` | Redaction, local storage, manifests, bundle verification/export | [`evidence/redaction.py`](../backend/src/replayforge/evidence/redaction.py) |
| `runs` | Invocation orchestration, discovery suites, terminal results, ordered journal | [`runs/service.py`](../backend/src/replayforge/runs/service.py) |
| `providers` | OpenAI request/response translation only | [`providers/openai.py`](../backend/src/replayforge/providers/openai.py) |
| `observability` | Bounded model-call metrics | [`observability/model_calls.py`](../backend/src/replayforge/observability/model_calls.py) |
| `runtime` | Settings, dependency wiring, worker/session retention | [`runtime/composition.py`](../backend/src/replayforge/runtime/composition.py) |
| `shared` | Stable IDs and clocks only | [`shared/ids.py`](../backend/src/replayforge/shared/ids.py) |

See [Data models](data-models.md) for the objects passed across these boundaries.

## Per-run isolation

```mermaid
%%{init: {"htmlLabels":false,"themeVariables":{"lineColor":"#6E7781","signalColor":"#6E7781"},"flowchart":{"curve":"linear"},"sequence":{"wrap":true}}}%%
sequenceDiagram
    autonumber
    box Entry
        participant API
    end
    box ReplayForge runtime
        participant Service
        participant Worker as Worker thread
    end
    box Browser boundary
        participant Browser as Chromium context
    end
    API->>Service: invoke artifact + inputs
    Service->>Worker: create executor
    Worker->>Browser: open isolated context
    loop ordered artifact steps
        Worker->>Browser: observe / resolve / act / verify
    end
    alt terminal result
        Worker->>Browser: close
    else intervention
        Worker-->>Service: retain worker + browser
    end
```

Playwright's synchronous objects are thread-affine. `SerialSessionWorker` therefore gives each run one bounded queue and one owner thread. API requests for viewport capture or human input are marshalled back to that thread.

## Surface reality

The production path is rendered-surface first. Playwright is still the browser transport, but the canonical capability uses it only for CSS-pixel screenshots and input dispatch; it never asks Playwright for a target element.

| Option considered | Decision | Reason |
|---|---|---|
| Persisted coordinates or relative ROIs | Rejected | Reflow and responsive breakpoints invalidate recorded geometry |
| Raw CSS/XPath recording | Rejected as primary | Couples artifacts to markup shape and generated identifiers |
| Semantic DOM/accessibility locators | Optional fallback | Useful when a trustworthy semantic surface exists; unavailable on the canvas contract |
| Semantic OCR + frame-local layout graph + visual signature | **Chosen primary** | Uses rendered identity, derives current geometry, and survives reflow, tenant styling, and DPR |
| OS accessibility/desktop driver | Designed, not built | Fits the surface port, but the assignment requires only one concrete surface |

`VisionGrounder` runs RapidOCR locally and resolves four geometry-free strategies: rendered text, a rendered label-to-control relation, a rendered label-to-value relation, and a rendered group label plus content-addressed visual signature. OCR phrases are rebuilt from the current frame; edge components are segmented from that same frame; relations use measured text height rather than saved offsets. Each result must be unique and satisfy the centrally loaded OCR, segmentation, similarity, pixel, and time budgets. Failure returns `target_absent` or `target_ambiguous`; replay never guesses.

The `visual-terminal` demo route exposes the original workflow as one canvas with no usable control or value nodes. The `visual-workbench` route keeps that contract while switching between responsive cards and a wide table, rendering three account-row actions, and injecting delayed, notice, permission, ambiguity, changed-icon, and duplicate-label fixtures. Harbor and Summit change palette, font metrics, horizontal placement, and row order. Version `3.2.0` resolves the current rendered `Savings` label, ranks same-group visual components against a content-addressed signature, and dispatches the resulting CSS-pixel point only for that frame. No artifact target stores coordinates or target-specific geometry; new schema `1.4` artifacts reject them recursively while legacy `1.3` fixtures remain loadable.

## Decisions

| Stage | Alternatives | Chosen | Why |
|---|---|---|---|
| Process topology | Microservices, queued workers, monolith | Modular monolith | Preserves explicit boundaries without adding deployment failure modes |
| Browser concurrency | Shared browser thread, async Playwright, worker per run | Worker per run | Keeps the synchronous Playwright session on one thread, including handoff |
| Target | Public sandbox, real bank, local synthetic app | Local synthetic app | Legal, deterministic, credential-free, and able to inject failures |
| Persistence | PostgreSQL/S3, memory/files, browser-local state | Memory + atomic files | Small runnable submission; repository ports leave a migration seam |
| Tenant model | Artifact copy per tenant, free-form overrides, shared contract | Shared supported-variant list | Demonstrates reuse without unsafe override complexity |
| Responsive grounding | Saved offsets, ordinal row selection, frame-local graph | Frame-local graph + canonical visual signature | Recomputes geometry after layout changes and fails closed on ties |
| DPR handling | Rescale stored pixels, screenshot in device pixels, CSS-pixel capture | CSS-pixel capture + explicit context DPR | Mouse coordinates and screenshot regions remain in one coordinate space |
| Live control | WebSocket stream, headed browser, polling | Versioned HTTP polling/input | Minimal real same-session control with stale-command protection |

## Honest extension path

```text
Capability semantics stay stable
  inputs → ordered actions → outputs → checkpoint
                         │
                         ├── web.v1: rendered OCR + signatures   [primary]
                         ├── web.v1: semantic Playwright locators [optional]
                         └── desktop.v1: accessibility/window IDs [designed]
```

A new transport must define observation normalization, input dispatch, screenshots, and condition evaluation. The visual grounding layer is transport-independent over PNG frames and viewport dimensions; a native desktop adapter can reuse it, but no native transport is claimed here.
