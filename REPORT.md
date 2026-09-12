# ReplayForge Design Report

## 1. Architecture

ReplayForge is a modular monolith: one FastAPI process owns discovery, replay, policy, browser sessions, evidence, and handoff. Two Next.js applications remain separate deployables because they have separate trust roles: the demo bank is the automation target; the control plane is the human intervention client.

```text
caller ──HTTP──► FastAPI ──► discovery or replay engine ──SurfaceSession──► Playwright
                         ├──► policy evaluator                         └──► demo bank
operator console ────────┤
                         └──► redacting journal ──EvidenceStore──► local files

discovery engine ──ModelProvider──► OpenAI ──metrics──► local Langfuse
replay engine     ── no model dependency
```

The engines depend on typed ports, not FastAPI, OpenAI, Playwright, or filesystem types. A structural test prevents the replay package from importing model providers. Each run receives an isolated Chromium context and one `SerialSessionWorker`; all browser and later human operations execute on that owner thread.

| Option considered | Choice | Reason |
|---|---|---|
| Microservices and a queue | Rejected | Adds deployment and recovery problems without improving the evaluated local flow |
| Unstructured single script | Rejected | Makes policy, surfaces, and evidence inseparable |
| Modular monolith with ports | **Chosen** | Strong replaceable boundaries with one-process operability |
| PostgreSQL and object storage | Deferred | Operational metadata is in memory; atomic evidence files keep the submission runnable and inspectable |

The implementation is intentionally narrower than the design seam. It supports one Playwright web surface. Discovery combines screenshots with normalized DOM/accessibility facts; replay uses deterministic semantic locators. The assignment permits DOM automation and asks for a credible heterogeneous extension, not a mandatory desktop build. A future visual or desktop adapter can implement the same surface port, but canvas, Citrix, OCR, image-anchor, and native-desktop execution are not claimed here.

## 2. Artifact schema

The production program is a strict YAML artifact, not a model transcript or generated browser script.

```text
identity/version
├── compatibility: application family, tenant variants, surface contract, entry point
├── typed input/output object contracts and data classifications
├── preconditions
├── ordered steps: action, scoped locator candidates, postconditions, timeout, retry, risk
├── business outcomes, application failures, bounded recoveries
├── final checkpoint
├── capability policy ceiling
└── provenance and canonical SHA-256
```

Pydantic models reject unknown fields and invalid cross-references. Every required output must be extracted by the main flow and checked by the final checkpoint. Published `(capability ID, version)` content is immutable; discovery receives the next patch version. Symbolic values such as `input.member_id` make a recording reusable without retaining the discovery value.

Target bundles contain frame scope, a human-readable description, ordered candidates, expected match count, visibility/enabled state, and portability. The implemented artifact prefers role/name and label locators, then relative label/value structure for extraction. The resolver accepts only one exact match.

| Option considered | Choice | Reason |
|---|---|---|
| Executable Playwright script | Rejected | Surface-specific, difficult to validate, and capable of escaping policy |
| Raw JSON | Rejected as review format | Strict but less readable for a step-oriented capability |
| YAML validated into frozen models | **Chosen** | Human-reviewable and machine-strict; raw YAML is never executed |
| Record coordinates only | Rejected | Viewport and layout changes make deterministic replay brittle |
| Model writes an arbitrary artifact | Rejected | The specialized compiler accepts only the verified savings-balance trace shape |

## 3. Determinism & error handling

Replay validates inputs and tenant compatibility before opening Chromium. For each ordered step it asserts automation ownership, verifies preconditions, resolves exactly one target, asks policy, records intent, acts, records the result, detects declared exceptional states, and verifies postconditions. Success additionally requires the final checkpoint and all five output schemas.

```text
invocation
  → validate
  → open isolated session + lease
  → [resolve → policy → intent → act → result → verify] × steps
  → checkpoint + typed outputs
  → one discriminated result
```

The result contract separates four meanings:

| Result | Meaning | Demonstration |
|---|---|---|
| `success` | Checkpoint and outputs verified | Version `1.0.0` |
| `business_outcome` | Legitimate negative answer | `member_not_found` |
| `failure` | Known application, mechanical, policy, or verification failure | Permission denial in `1.0.2` |
| `intervention_required` | Session is live but automation may not proceed | Sensitive submit in `2.0.0` |

Retries require a named recoverable code, remaining attempts, and—when declared—proof that the previous effect is absent. Recovery is an explicit, bounded step list with a fixed resume point; nested recovery and sensitive recovery actions are invalid. Version `1.0.1` demonstrates one known interstitial dismissal. No replay path can ask a model to improvise.

The key choice was checkpoint-led correctness rather than action-led optimism: a successful click receipt proves only dispatch, while a postcondition proves effect and the final checkpoint proves the business state. Fuzzy selection and open-ended LLM recovery were rejected because they weaken reproducibility and auditability.

## 4. Heterogeneity & multi-tenant

The seam is `SurfaceDriver`/`SurfaceSession`: open, observe, resolve, act, evaluate, extract, capture evidence, and close. Normalized observations and actions contain no Playwright handles. A desktop adapter could map them to window identity and OS accessibility controls; a visual adapter could map locator candidates to OCR or image anchors. Those adapters still need deterministic confidence, calibration, and drift rules and are not implemented.

The selected demo is legacy-style but not DOM-less: it uses an iframe, table layouts, server navigation, and no test IDs, while retaining usable labels and roles. Semantic locators were chosen over coordinates because they are more stable and reviewable for this concrete target. This demonstrates the web seam, not universal non-DOM automation.

One artifact lists both `harbor` and `summit` as supported variants. The adapter normalizes tenant-prefixed routes to one surface contract; a committed run proves the same artifact version and hash on Summit.

| Multi-tenant option | Choice | Reason |
|---|---|---|
| Copy one artifact per institution | Rejected | Creates review drift and hides shared vendor behavior |
| Arbitrary tenant patches | Rejected | Can silently change business behavior or widen policy |
| Shared application-family artifact | **Chosen** | Reuses one reviewed contract across compatible variants |

The artifact records required/forbidden landmarks and a target fingerprint, but automatic drift enforcement and an overlay repository are not implemented. A production extension would keep base semantics immutable and allow only narrow, validated locator/entry-point/timing overrides that cannot widen policy.

## 5. Escalation & handoff

Discovery intervenes on repeated state, repeated action, low confidence, explicit model escalation, or a policy requirement. Replay intervenes on sensitive policy decisions or surface errors marked for intervention.

```text
automation/v1 → paused/v2 → human:operator/v3 ──heartbeat──► human/v4...
                                      │
                                      └──resume──► paused → fresh validation → automation
```

The live Chromium context and its worker are retained. The operator claims an exclusive 30-second lease, polls PNG frames, and sends one bounded left-click, text, or allowlisted key command tied to the exact lease version, latest frame sequence, next client sequence, and viewport. After an accepted input the frame is invalidated. Stale or concurrent requests return conflict.

Resume captures fresh state, confirms the current location is allowed, checks the interrupted step's postcondition or business outcome, and rejects an unchanged fingerprint. Only then does ownership return to automation and replay continue with the remaining steps. Manual text is never logged; evidence records its character count. Discovery can pause on the same session, but automated discovery continuation after manual work is deliberately unavailable.

HTTP polling was chosen over a WebSocket/CDP stream. It is less fluid, but it provides a minimal real handoff with explicit stale-frame semantics and far less transport scope. Opening a new browser was rejected because it would lose session context and violate the requirement.

## 6. Safety

Each action crosses three independent controls: current lease ownership, effective allowlists, and effective risk. Replay intersects platform, application, tenant, capability, and invocation policy layers; discovery intersects platform and application layers. Sets narrow by intersection, forbidden classifications accumulate, and the lowest risk ceiling wins.

Risk is independently inferred from action type, target language, and observed target facts, then combined with the declared risk. Irreversible actions are always denied. Sensitive actions require human approval. Origins, normalized routes, and action types must be explicitly allowed.

Evidence is sanitized before persistence. Structured redaction drops secret-bearing keys and personal fields, tokenizes customer identifiers, replaces financial values, and scans remaining text for credential patterns. Persisted screenshots mask inputs, definition values, and account-table cells before capture. Atomic writes, SHA-256 metadata, and manifests make incomplete or changed evidence detectable. API errors expose stable codes and safe messages without submitted values or raw provider errors.

The trade-off is conservative capability: the system may stop where a broader automation could continue. That is intentional for financial operations. Authentication, operator authorization, TLS, automated evidence expiry, and durable control transactions are required before production deployment and are outside this local submission.

## 7. Cuts

Depth was concentrated on one complete workflow and its exceptional states.

| Cut | What exists instead | Next production step |
|---|---|---|
| PostgreSQL repositories | Thread-safe in-memory registry, journal metadata, leases, interventions | Transactional repositories and restart recovery |
| S3-compatible evidence | Opaque keys over atomic local files | Object store plus authorized download service |
| Full operations UI | Focused intervention console | Run list, capability catalog, evidence viewer, authentication |
| WebSocket/video co-browsing | PNG polling and bounded HTTP input | Backpressured stream with durable control events |
| Visual/native desktop execution | Surface ports and locator variants | OCR/image and OS-accessibility adapters with confidence rules |
| Generic discovery compiler | Fail-closed savings-balance compiler | Reviewed workflow templates or constrained compiler families |
| Discovery continuation after handoff | Safe reopen with retained session | Serializable discovery continuation and fresh-goal validation |
| Distributed workers/queues | One process and one browser-owner thread per run | Durable scheduling only when workload requires it |
| Open-ended model replay recovery | Declared finite recovery only | Optional single-step, policy-checked assisted fallback |
| Automatic tenant drift/overlays | Supported variants, route normalization, recorded fingerprints | Narrow overlay schema and compatibility gate |

The repository provides seven hash-verified evidence bundles: genuine discovery, replay success, member-not-found, bounded recovery, hard failure with masked screenshot, same-session handoff, and second-tenant reuse. `bash scripts/verify.sh` runs formatting, lint, strict typing, a 90% branch gate, evidence integrity, both frontend builds, and real Chromium integration tests.
