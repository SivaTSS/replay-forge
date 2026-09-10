# Architectural Decisions and Deliberate Cuts

## Decisions

### Modular monolith, not microservices

One runtime process keeps local setup and transactions straightforward. Ports, module ownership, and dependency direction provide modularity. Separate services would add deployment and failure modes without improving the evaluated core.

### Dedicated modern control plane

Next.js and strict TypeScript provide a polished, testable product surface. A server-rendered template console was rejected because it would under-deliver on live operations, artifact review, and human control. The UI remains feature-modular and uses generated contracts rather than duplicating backend rules.

### Separate realistic target application

The demo bank is isolated from the control plane so target behavior, origins, failures, and tenant variants are credible. ReplayForge may interact with it only through the rendered UI. A public demo site was rejected because availability, terms, and fault states would be uncontrolled.

### Screenshot-led discovery, locator-led replay

Screenshots keep discovery relevant to hostile or non-semantic surfaces. Stable locator bundles make replay deterministic and debuggable. DOM-only discovery would avoid the core environment; coordinate-only replay would be brittle.

### Provider-neutral domain with one reference adapter

Normalized observations and actions prevent provider SDK types from contaminating domain logic. One OpenAI adapter is sufficient for genuine evidence. Building multiple adapters before the core is proven would add breadth without demonstrating better judgment.

### PostgreSQL behind repositories

Runs, immutable versions, events, interventions, and control leases require transactions and useful queries. PostgreSQL is the professional default and remains local through Docker Compose. Repository ports preserve testability and future storage changes.

### YAML artifacts with strict validation

YAML is readable in review, while Pydantic and generated JSON Schema provide a strict runtime contract. Raw YAML is never executed or trusted. JSON-only storage was less approachable; generated browser scripts would couple the capability to one surface.

### Explicit result taxonomy

Success, business outcome, failure, and intervention are discriminated results. This prevents expected conditions such as member-not-found from being reported as crashes and forces callers to handle every terminal category.

### Bounded declared recovery only

Replay may use explicit finite recoveries but never open-ended model reasoning. Assisted LLM fallback was rejected because it weakens the production guarantee and is an optional stretch rather than a core requirement.

### Embedded same-session control

The operator receives a live viewport backed by the existing Chromium session and an exclusive versioned lease. Opening a replacement session would violate the assignment. A bare headed window would work but under-deliver on the control-plane experience and auditable ownership.

### Base capability plus tenant overlays

One application-family artifact is reused across two variants. Narrow overlays can adjust entry points, aliases, frames, locators, and timing but cannot change behavior or widen safety. Copying an artifact per tenant would hide the real scale problem.

### Filesystem evidence behind a port

Local sanitized files make the submission inspectable. Opaque keys and manifests allow later S3-compatible storage. Database blobs were rejected due to poor trace/screenshot ergonomics.

## Deliberate cuts

- No real bank system, credentials, or customer data.
- No native desktop adapter; only the surface contract and design seam.
- No second live model-provider adapter.
- No arbitrary workflow editor or automatic artifact mutation.
- No irreversible actions in the demo.
- No open-ended LLM recovery during replay.
- No microservices, queue, Kubernetes, or cloud deployment.
- No enterprise identity-provider integration.
- No multi-operator co-browsing.
- No capability breadth beyond the deeply exercised savings-balance flow.
- No approval/stability scoring beyond approvals required by safety and handoff.

## Dependency admission rule

A dependency is accepted only when it provides a required capability, materially reduces correctness/security risk, is actively maintained, and can be isolated behind an owned boundary. Convenience alone is insufficient. Versions are pinned and upgrades are verified by tests.

## Scope-change rule

New work is admitted only when it closes a traceability gap, strengthens a load-bearing requirement, or removes material reviewer friction. It is rejected when it primarily adds framework breadth, infrastructure theater, or visual decoration.

## Final review questions

- Does every module own a clear business responsibility?
- Can each external adapter be replaced by a contract fake?
- Can the reviewer prove replay did not call a model?
- Does every success claim have an observable checkpoint?
- Can every exceptional state be categorized without inspecting a stack trace?
- Can the operator prove they controlled the original session exclusively?
- Can the base artifact run across both tenant variants safely?
- Can a clean clone reproduce the complete demonstration?
