# Assignment requirements

[Documentation index](README.md)

This matrix distinguishes implementation from design. It does not credit planned behavior as complete.
The assignment prioritizes system design, core-loop correctness, runtime error handling, and real
control transfer. The [decision index](architecture.md#critical-decision-index) explains the choices;
the [challenge table](#how-to-challenge-the-claims) links directly to their proof boundaries.

Source: [Assignment A — Computer-Use Automation System](../Assignment%20A%20%E2%80%94%20Computer-Use%20Automation%20System.pdf),
all ten pages. Section numbers below refer to that PDF, not to these reference documents.

## PDF traceability

| PDF section | Delivery and decision | Qualification |
|---|---|---|
| §1–2: real back-office UI → reusable automation | One synthetic staff workstation; three non-trivial, parameterized tasks | No banking API or real customer access; UI breadth is test context, not the evaluation claim |
| §3.1: live goal-driven loop | Real model proposals, executed UI actions, bounded stop conditions | Provider-backed evidence, not scripted discovery |
| §3.2: deliberate capability schema | Ordered actions, robust targets, typed inputs/outputs, checkpoint, immutable versions | [Schema and invariants](data-models.md#capability-artifact) |
| §3.3: model-free replay and error taxonomy | Verified success, business outcomes, bounded recovery, hard failures | [Runtime-fault coverage](#runtime-fault-coverage) distinguishes learned cases from generic stops |
| §3.4: configurable safety and no raw sensitive persistence | Layered allowlists, conservative risk, artifact guards, pre-write redaction | Synthetic-only assurance; not a universal PII detector or production authorization system |
| §3.5: structured evidence and richer failure signal | Ordered logs, masked PNG, and scanned value-free diagnostic ZIP | Explains phase, condition kind, dispatch uncertainty and retry state without retaining screen content |
| §3.6: stuck detection and real human control transfer | Same browser, exclusive lease, audited manual input, validated resume in both modes | Ordinary replay target/action blockages route after bounded handling; policy/identity violations and unavailable sessions stay terminal |
| §3.7: heterogeneity and scale design | Browser surface port, desktop extension design, shared artifact across two tenants | Desktop, fleet scheduling and vendor overlays are not built; the PDF permits design-only delivery here |
| §4: explain technology and target choices | [Architecture decisions](architecture.md#architecture-decisions), [provider decisions](discovery.md#provider-decision), [schema decisions](capability-and-replay.md#schema-and-version-decisions) | No unsupported claim that the chosen model is the best model |
| §5, §7: depth, integration, appropriate simplicity | Tested core contracts, immutable evidence, bounded resources; explicit cuts | Test counts do not prove every possible state or deployment |
| §6: exact deliverable paths and short report | [README](../README.md), seven-part [REPORT](../REPORT.md), [evidence](../evidence/README.md) | Submission/access checks below remain separate from implementation |
| §8: at most one or two stretch goals | Typed agent-facing invocation; demonstrated cross-tenant reuse | No code generation, model replay fallback, approval product, or statistical stability claim |
| §9: legitimate target, secret hygiene, ownership | Local synthetic data; genuine recorded runs; documented verification and cuts | No real credentials or PII used in the target; configured model secrets stay ignored |
| §10: shared terminology | [Documentation glossary](README.md#shared-terminology) and explicit result taxonomy | A business outcome is not a crash; deterministic rules do not freeze external business data |
| §11: public repository and email | Repository URL must be publicly readable and emailed from the applicant's address | Not completed by local tests or a private Git push |

## Implementation detail

| Requirement | Status | Concrete implementation | Proof / limit |
|---|---|---|---|
| Goal + target input | Implemented | `DiscoveryInvocation` accepts goal, family, tenant, entry point, inputs, limits | Target is registered, not an arbitrary URL |
| Live LLM loop | Evidenced | `DiscoveryEngine` + `OpenAIModelProvider` | Genuine primary and scenario bundles for three workstation tasks |
| Real UI actions | Evidenced | Chromium mouse/keyboard on the canvas workstation | Local synthetic target |
| Bias beyond clean DOM | Implemented, Tested | Local OCR, frame-local layout graph, canonical visual signatures | Canonical flow has no DOM targets |
| Typed reusable artifact | Evidenced | Strict Pydantic aggregate serialized as YAML | Three independently discovered workstation artifacts |
| Robust control identity | Implemented | Rendered text, label relations, frame-local components, canonical signature, uniqueness gate | Fails closed on absent or ambiguous matches |
| Typed inputs/outputs | Implemented | Closed object contracts and runtime validation | Distinct transaction, payoff, and card contracts |
| Success checkpoint | Evidenced | Route, visible text, output validity, input/output identity | Artifact conditions + exact-output browser tests |
| Model-free replay | Evidenced | `ReplayEngine` has no provider import | Structural test + bundle |
| Business outcome | Evidenced | Declared outcome conditions and exact typed results | Negative scenarios in all three tasks discovered and replayed on both tenants |
| Recoverable condition | Evidenced | Bounded recovery, lease renewal, fixed resume point | Payoff and card notice recoveries followed by verified task completion on both tenants |
| Hard failure | Evidenced, Tested | Typed application/mechanical failures and masked evidence | Invalid-date/reason and restricted-member branches; separate mechanical-failure bundle with masked screenshot. Role-denial discovery is not claimed |
| Explicit allowlist | Implemented | Origin/route/action/risk policy intersections | Policy decision tests |
| Conservative risk | Implemented, Tested | Sensitive pauses; irreversible denies | Policy and real-session browser tests; handoff fixtures are not genuine discovery bundles |
| No sensitive persistence | Hardened for synthetic demo; bounded | Restricted journal/terminal fields, keyed pseudonyms, artifact leak guard, full-frame masks, image capture disabled by default | Tested boundaries, not a universal PII detector; see [data policy](safety-and-handoff.md#data-exposure-boundaries) |
| Structured evidence | Evidenced | Ordered events, result, hashes, masked frames, bounded diagnostic trace | Historical discovery/replay proof plus fresh finalization evidence; raw UI values and screen history are excluded |
| Detect and route intervention | Implemented, Tested | Blocked discovery, sensitive replay, and exhausted target/action faults use the same operator surface | Real-browser obstruction test uses the unchanged published artifact; no new model-discovery claim |
| Same-session control | Implemented, Tested | Retained context and worker; bounded human input | Live browser tests with explicit policy fixtures |
| Explicit ownership | Implemented, Tested | TTL lease, owner, version, CAS | Conflict/race tests |
| Safe resume | Implemented, Tested | Dispatch-aware retry/advance, fresh output operands, preserved budgets; discovery requires manual input and changed allowed state | Missing effect contract rejects uncertain-dispatch resume; fresh unattended replay still gates publication |
| Surface abstraction | Implemented | `SurfaceDriver` and `SurfaceSession` protocols | One Playwright adapter |
| Canvas visual control | Implemented, Tested | One canvas-only workstation exposes three discovered tasks using geometry-free rendered candidates and CSS-pixel re-grounding | Browser transport only |
| Native desktop extension | Designed | Surface ports and PNG-based grounding seam | No OS transport adapter |
| Multi-tenant reuse | Evidenced | Each current artifact validates Harbor and Summit | Changed-input/viewport tests and genuine tenant validation |
| Per-tenant/version drift | Implemented checks; extension Designed | Pre-launch registration contract and live entry landmarks; per-step verification | No vendor-release detector or overlay repository; [design](heterogeneity-and-compatibility.md) |
| Human operator surface | Implemented | Next.js launcher/viewer, temporary replay history, and same-session control | Local trust boundary, not production authentication or video streaming |
| Agent-facing invocation | Implemented | `/invoke` with typed arguments and discriminated result; execution catalog exposes input contracts | Durable run-history browser is not implemented |

## How to challenge the claims

| Evaluation question | Inspect | Boundary to keep in mind |
|---|---|---|
| Was discovery genuine? | [Recorded discovery and scenario bundles](verification.md#scenario-matrix) | Three task families on the sole workstation, with separate primary/exception runs; old-UI bundles are removed |
| Does replay avoid model decisions? | [Dependency test](../backend/tests/unit/replay/test_dependency_rule.py) and [browser matrix](../backend/tests/integration/test_visual_portability.py) | The target and local OCR are still required |
| Is the compiler task-independent? | [Generic compiler tests](../backend/tests/unit/discovery/test_generic_compiler.py) and [runtime import rules](../backend/tests/unit/runtime/test_dependency_rules.py) | New applications still require registration |
| What does task success actually prove? | [Annotated real artifact](capability-and-replay.md#worked-example-temporary-card-lock) | Typed output validity is not automatically a business-value assertion |
| Is handoff real control transfer? | [Session integration](../backend/tests/integration/test_playwright_surface.py) and [console integration](../backend/tests/integration/test_operator_console.py) | Same live context, but no authentication or crash recovery |
| Can the saved program survive restart? | [Durability model](data-models.md#durability) and [registry tests](../backend/tests/unit/capabilities/test_registry.py) | Durable artifacts are separate from transient run and lease state |

## Deliberate interpretation choices

| Assignment ambiguity | Alternatives | Interpretation used |
|---|---|---|
| “Target” | Arbitrary caller URL or registered entry point | Registered symbolic target prevents SSRF and policy bypass |
| “Stable targeting” | Coordinates, fuzzy vision, DOM/accessibility | Geometry-free semantic OCR and frame-local visual signatures first; DOM locators optional |
| “No clean DOM” | Design bias or executable proof | Canvas-only canonical flow proves the full contract from rendered pixels |
| “Take control” | New browser, headed local window, remote input | Bounded input into the retained browser context |
| “Replay failure” | HTTP error or typed domain result | HTTP success with discriminated automation result |
| “Scale” | Build queues/database or preserve seams | Ports and immutable IDs now; distributed infrastructure cut |

## Important non-claims

- The canonical demo surface is one canvas; Playwright still provides browser transport and input dispatch.
- A new task in the registered bank needs a goal and discovery run, not a task-specific compiler,
  entry-point edit, or runtime code change. A new application still needs one policy-reviewed
  registration; a new surface contract still needs an adapter.
- Rendered OCR, label relations, and visual signatures execute today; optional image-matching primitives have unit coverage. Old artifact schemas, accessibility-path adapters, and native desktop adapters are not accepted.
- Registration and semantic landmark compatibility are enforced. Observation hashes remain
  provenance; they are not literal pixel-equality drift gates.
- Published capabilities and evidence survive restart; operational run and handoff state does not.
- The operator console is not a complete product UI.
- Direct invocation is synchronous; the viewer launches background executions through HTTP 202
  and polls their state. There is no WebSocket endpoint.
- Authentication, authorization, retention enforcement, PostgreSQL, object storage, and distributed workers are not implemented.

## Discovery handoff versus publication

PDF §3.6 includes live human takeover when discovery gets stuck. This is different from approving
an already successful discovery: the runtime supports the former and does not introduce the latter.
Only fresh deterministic suite validation publishes a capability, including read-only tasks.

## Runtime-fault coverage

The PDF's examples are not interchangeable. A member restriction is not a role-permission denial;
an automation lease expiry is not an application's login-session expiry.

| PDF runtime concern | Current behavior | Proof boundary |
|---|---|---|
| Record not found | Positive learned branch returns `business_outcome`, without invented outputs | Genuine missing-member cases for all three tasks and account-scoped missing transaction |
| Validation error | Observed date/reason rejection returns its exact application failure code | Genuine payoff/card scenarios; malformed invocation shapes additionally reject before UI execution |
| Unexpected dialog/interstitial | Declared notice correction, restored boundary, then original task completion | Genuine payoff/card recovery on both tenants; not arbitrary-dialog recovery |
| Permission denial | Typed failure detector is supported; target enforces training roles | Engine and application tests; role-denial discovery is not claimed |
| Session expiry | Out-of-policy routes or missing expected state stop execution | No application-login expiry scenario, credential flow, or learned reauthentication; control-lease tests prove a different boundary |
| Slow or failed load | Bounded polling/waits, effect-absent retries, policy-checked handoff on exhausted target/action faults | Engine/adapter tests; no genuine transient-load recovery recording in the three-task matrix |
| Ambiguous or missing control | Exact-one resolution, bounded handling, then policy-checked handoff; unattended validation fails | Real-browser obstruction → manual clearance → same-step replay, without editing the published artifact |
| Uncertain mutation | Pause to inspect/correct; advance only after fresh effect verification, never blind repetition | Engine tests; absent effect contract rejects resume; no transactional rollback or crash recovery |

## Remaining concerns

The bounded implementation pass closes the two core gaps identified by the earlier documentation
audit. Wider target-specific fault discovery remains outside this final pass.

| Concern | Present limit | What closes it |
|---|---|---|
| Broader stuck-replay handoff (§3.6) | **Closed within the declared browser scope**: ordinary target/action faults route through policy and dispatch-aware continuation | [Boundary rules and decisions](safety-and-handoff.md#same-session-handoff); real-browser injected-obstruction test and unit safety regressions |
| Useful retained failure diagnostics (§3.5) | **Closed without unmasking screens**: ordered diagnostic snapshots and one final scanned ZIP | [Retention contract](safety-and-handoff.md#evidence-path); fresh trace export and integrity checks |
| Wider application fault coverage (§3.3) | Declared cases are proved; application session expiry and role-denial discovery are not | Genuine observed scenarios on a suitable authorized target, with exact model-free outcome/recovery checks |

No test suite certifies every possible target state. Production authentication, native desktop,
distributed storage and a full operator product remain separate, explicit cuts. Application-login
expiry and role-denial discovery would require a broader authorized discovery campaign; those
examples are not fabricated or conflated with existing notice/restriction scenarios.

## Submission checklist

| Deliverable | Repository state | Final action |
|---|---|---|
| `/README.md` | Setup, credentials, no-live-service inspection, exact discovery → replay commands | Run from the intended submission environment |
| `/REPORT.md` | Seven exact headings; concise design summary targeting the PDF's approximately 1–3 pages | Render/page-count check recorded in [verification](verification.md#documentation-audit) |
| `/evidence/` | Exact artifacts, genuine discovery logs, model-free success/failure/recovery logs | Integrity gate passes; do not alter historical bundles |
| Public GitHub repository | Confirmed **private** by authenticated GitHub metadata on 2026-09-14; anonymous lookup returns `404` | Owner must make the intended submission repository public and verify access anonymously; this audit does not change visibility |
| Submission email | Not sent by this audit | Email the public repository URL to `assignments@interface.ai`, from the application email address, with the URL on its own line; no ZIP |

The PDF welcomes a recording but does not require one. No video, PostgreSQL, S3, or human
post-discovery approval is a missing mandatory deliverable. Historical versions on the current
workstation are provenance, not a second demo UI, and remain referenced by evidence and tests.
