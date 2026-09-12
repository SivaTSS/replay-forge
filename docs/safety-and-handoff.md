# Safety, evidence, and human handoff

## One action, three gates

```mermaid
flowchart LR
    A[Proposed action] --> O{Owns current lease?}
    O -- no --> D[Deny]
    O -- yes --> L{Origin, route, action allowlisted?}
    L -- no --> D
    L -- yes --> R{Effective risk}
    R -- irreversible --> D
    R -- above ceiling --> D
    R -- sensitive --> H[Pause for human]
    R -- allowed --> E[Record intent → execute → record result]
```

### Policy composition

Replay intersects five immutable layers:

```text
platform ∩ application ∩ tenant ∩ capability ∩ invocation
```

Allowed origins, route patterns, and action types become the set intersection. Forbidden field classes become the union. The maximum risk becomes the most restrictive ceiling. Discovery currently intersects platform and application layers.

Risk is the maximum of:

- Risk declared by the step or proposal.
- Risk registered by the resolved target.
- Risk inferred from action type and target language.

The model and artifact therefore cannot lower an independently detected risk.

| Risk | Runtime response |
|---|---|
| Read-only or reversible within policy | Allow |
| Sensitive within policy | Require human approval |
| Above policy ceiling | Deny |
| Irreversible | Always deny in this submission |

## Control ownership

Every browser session has one versioned lease with a 30-second TTL.

```mermaid
stateDiagram-v2
    [*] --> Automation: session opens / v1
    Automation --> Paused: intervention / version + 1
    Paused --> Human: claim / version + 1
    Human --> Human: heartbeat / version + 1
    Human --> Paused: release or begin resume
    Paused --> Automation: fresh state validates
    Paused --> Paused: validation fails; intervention reopens
    Paused --> None: terminate
    Human --> None: terminate by owner
```

Every mutation supplies the expected lease version and owner. The repository changes it with compare-and-swap. A stale operator tab, duplicate request, expired lease, or automation action after pause is rejected.

## Same-session handoff

```mermaid
sequenceDiagram
    participant A as Automation
    participant R as Runtime
    participant O as Operator console
    participant B as Retained browser
    A->>R: sensitive or stuck condition
    R->>B: capture masked before-frame
    R-->>A: intervention_required
    O->>R: claim(expected version)
    loop every 2 seconds
        O->>R: viewport(current lease)
        R->>B: screenshot on owner thread
        B-->>O: PNG + frame/viewport/sequence headers
    end
    O->>R: click/text/key bound to latest frame
    R->>B: execute on same page and context
    O->>R: resume(expected version)
    R->>B: fresh observation + interrupted postcondition
    alt valid and changed
        R->>B: continue remaining artifact steps
    else invalid or unchanged
        R-->>O: reopen intervention
    end
```

Accepted manual input is deliberately narrow: left click, text insertion, and ten navigation/editing keys. Text content is never placed in audit events; only its character count is recorded. Pointer evidence records coordinates, source frame, viewport, and sequence.

Discovery can pause and expose the same session, but deterministic continuation after manual work is currently implemented only for replay. Discovery resume reopens safely because no continuation is available.

## Stale-input protection

A human input command must match all of:

```text
intervention ID
  + human owner ID
  + exact lease version
  + latest frame sequence
  + next client sequence
  + exact viewport dimensions
```

After one accepted input, the frame is invalidated. The next action requires a fresh screenshot.

## Evidence path

```text
domain event / terminal result
        → structured redaction
        → forbidden-secret scan
        → atomic local write
        → SHA-256 + metadata sidecar
        → new manifest snapshot

browser failure/handoff frame
        → mask inputs, details, account table
        → PNG signature and size validation
        → atomic local write + manifest
```

The redactor drops credential-, token-, password-, cookie-, authorization-, and secret-shaped keys; drops personal fields; tokenizes customer identifiers; and replaces financial values. It also rejects known provider, cloud, GitHub, bearer, and private-key patterns that survive redaction.

The operator viewport is live and therefore unmasked for the authorized lease holder. Persisted failure and handoff screenshots are masked before capture.

## Decisions

| Stage | Alternatives | Chosen | Why |
|---|---|---|---|
| Policy | Single boolean guard, adapter-specific checks, layered policy | Layer intersection | Every authority can only narrow permission; decisions stay auditable |
| Risky action | Allow with logging, deny all, human approval | Sensitive → human; irreversible → deny | Demonstrates safe progress without pretending irreversible recovery is solved |
| Evidence redaction | Redact at display, redact after storage, redact before write | Before write | Sensitive bytes never enter durable evidence |
| Session takeover | Open new browser, expose existing browser | Existing context | Preserves cookies, route, form state, and the assignment's required seam |
| Ownership | UI convention, mutex only, versioned lease | Versioned lease + CAS | Makes stale and concurrent commands explicit conflicts |
| Transport | WebSocket/CDP stream, headed browser, HTTP polling | HTTP polling | Minimal real control path; sequence checks compensate for stale frames |
| Persistence | Database transactions, process memory | Memory for control metadata | Fits the local slice; restart loses interventions and is documented |

## Known limits

- Restarting FastAPI loses active leases, interventions, journals, continuations, and the capability registry's runtime state.
- There is no authentication or institution authorization layer; operator IDs are caller-supplied labels.
- HTTP is loopback-oriented and not production transport security.
- There is no continuous video, drag, right-click, clipboard, file upload, or multi-operator co-browsing.
- Evidence retention classes are metadata only; automated expiry is not implemented.
