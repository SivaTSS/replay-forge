# Assignment requirements

[Documentation index](README.md)

This matrix distinguishes implementation from design. It does not credit planned behavior as complete.
The assignment prioritizes system design, core-loop correctness, runtime error handling, and real
control transfer. The [decision index](architecture.md#critical-decision-index) explains the choices;
the [challenge table](#how-to-challenge-the-claims) links directly to their proof boundaries.

| Requirement | Status | Concrete implementation | Proof / limit |
|---|---|---|---|
| Goal + target input | Implemented | `DiscoveryInvocation` accepts goal, family, tenant, entry point, inputs, limits | Target is registered, not an arbitrary URL |
| Live LLM loop | Evidenced | `DiscoveryEngine` + `OpenAIModelProvider` | Three current workstation bundles |
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
| Conservative risk | Evidenced | Sensitive pauses; irreversible denies | Policy and real-session browser tests |
| No sensitive persistence | Hardened for synthetic demo; bounded | Restricted journal/terminal fields, keyed pseudonyms, artifact leak guard, full-frame masks, image capture disabled by default | Tested boundaries, not a universal PII detector; see [data policy](safety-and-handoff.md#data-exposure-boundaries) |
| Structured evidence | Evidenced | Ordered events, result, hashes, manifest | Three genuine discoveries plus successful and failed model-free replay bundles |
| Detect and route intervention | Implemented | Replay and blocked discovery route to the same operator surface | Discovery correction is not a post-publication approval stage |
| Same-session control | Implemented, Tested | Retained context and worker; bounded human input | Live browser tests with explicit policy fixtures |
| Explicit ownership | Implemented, Tested | TTL lease, owner, version, CAS | Conflict/race tests |
| Safe resume | Implemented | Replay checks the interrupted contract; discovery requires manual input and changed allowed state | Human actions are audited, not invented as automation; fresh replay still gates publication |
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
| Was discovery genuine? | [Three recorded discovery bundles](verification.md#scenario-matrix) | Three executions on the sole workstation; old-UI bundles are removed |
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
