# Assignment requirements

This matrix distinguishes implementation from design. It does not credit planned behavior as complete.

| Requirement | Status | Concrete implementation | Proof / limit |
|---|---|---|---|
| Goal + target input | Implemented | `DiscoveryInvocation` accepts goal, family, tenant, entry point, inputs, limits | Target is registered, not an arbitrary URL |
| Live LLM loop | Evidenced | `DiscoveryEngine` + `OpenAIModelProvider` | `evidence/discovery-success` |
| Real UI actions | Evidenced | Chromium clicks, types, navigates, and extracts through iframe | Local synthetic target |
| Bias beyond clean DOM | Partial by design | Screenshot + normalized facts; surface port | Replay still uses DOM/accessibility locators |
| Typed reusable artifact | Evidenced | Strict Pydantic aggregate serialized as YAML | Four committed immutable versions |
| Robust control identity | Implemented | Ordered scoped locators; exactly-one/state checks | No visual resolver implemented |
| Typed inputs/outputs | Implemented | Closed object contracts and runtime validation | Five outputs on success |
| Success checkpoint | Evidenced | Route, visible text, output validity, input/output identity | `replay-success` |
| Model-free replay | Evidenced | `ReplayEngine` has no provider import | Structural test + bundle |
| Business outcome | Evidenced | `member_not_found` detector | Dedicated bundle |
| Recoverable condition | Evidenced | One bounded interstitial recovery | Dedicated bundle |
| Hard failure | Evidenced | Permission-denied detector with expected/observed state | Masked screenshot bundle |
| Explicit allowlist | Implemented | Origin/route/action/risk policy intersections | Policy decision tests |
| Conservative risk | Evidenced | Sensitive pauses; irreversible denies | `human-handoff` |
| No sensitive persistence | Implemented | Structured redaction, secret scan, masked frames | Evidence/store tests and manifests |
| Structured evidence | Evidenced | Ordered events, result, hashes, manifest | Seven verified bundles |
| Detect and route intervention | Evidenced | Stuck, low confidence, model escalation, surface recommendation, sensitive policy | Handoff bundle |
| Same-session control | Evidenced | Retained context and worker; bounded human input | Handoff before/after frames |
| Explicit ownership | Evidenced | TTL lease, owner, version, CAS | Conflict/race tests |
| Safe resume | Evidenced for replay | Fresh location, postcondition, and changed-fingerprint checks | Discovery continuation is cut |
| Surface abstraction | Implemented | `SurfaceDriver` and `SurfaceSession` protocols | One Playwright adapter |
| Desktop/visual extension | Designed | Surface contract and locator discriminators | No executable adapter |
| Multi-tenant reuse | Evidenced | One artifact supports Harbor and Summit | `tenant-reuse` |
| Per-tenant drift/overlays | Designed only | Compatibility landmarks and surface fingerprint fields | No overlay repository or automatic drift gate |
| Human operator surface | Implemented | Next.js intervention console | No run list, auth, or WebSocket |
| Agent-facing invocation | Implemented | `/invoke` with typed arguments and discriminated result | Capability catalog endpoint is not implemented |

## End-to-end thread

```text
goal
 → real OpenAI decision loop
 → real Chromium UI interaction
 → verified trace
 → strict YAML capability
 → immutable registry version
 → model-free replay with new input
 → typed outcome and hash-linked evidence
 → optional same-session human pause/claim/resume
```

## Deliberate interpretation choices

| Assignment ambiguity | Alternatives | Interpretation used |
|---|---|---|
| “Target” | Arbitrary caller URL or registered entry point | Registered symbolic target prevents SSRF and policy bypass |
| “Stable targeting” | Coordinates, fuzzy vision, DOM/accessibility | Ordered semantic locators for the concrete web surface |
| “No clean DOM” | Mandatory DOM-free implementation or design bias | The brief explicitly permits DOM; non-DOM support is a designed extension |
| “Take control” | New browser, headed local window, remote input | Bounded input into the retained browser context |
| “Replay failure” | HTTP error or typed domain result | HTTP success with discriminated automation result |
| “Scale” | Build queues/database or preserve seams | Ports and immutable IDs now; distributed infrastructure cut |

## Important non-claims

- The demo is legacy-style, not a truly DOM-less surface.
- Image-anchor and accessibility-path locator values validate in the schema but are not executed by the Playwright adapter.
- Compatibility fingerprints are recorded; startup/runtime drift enforcement is not implemented.
- Operational metadata is not durable across process restart.
- The control plane is not a complete product UI.
- The API is synchronous and has no WebSocket endpoint.
- Authentication, authorization, retention enforcement, PostgreSQL, object storage, and distributed workers are not implemented.
