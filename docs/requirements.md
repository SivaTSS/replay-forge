# Assignment requirements

This matrix distinguishes implementation from design. It does not credit planned behavior as complete.

| Requirement | Status | Concrete implementation | Proof / limit |
|---|---|---|---|
| Goal + target input | Implemented | `DiscoveryInvocation` accepts goal, family, tenant, entry point, inputs, limits | Target is registered, not an arbitrary URL |
| Live LLM loop | Evidenced | `DiscoveryEngine` + `OpenAIModelProvider` | `evidence/discovery-success` |
| Real UI actions | Evidenced | Chromium mouse/keyboard on iframe and canvas surfaces | Local synthetic target |
| Bias beyond clean DOM | Implemented, tested | Local OCR, frame-local layout graph, canonical visual signatures | Canonical flow has no DOM targets |
| Typed reusable artifact | Evidenced | Strict Pydantic aggregate serialized as YAML | Legacy fixtures plus independently discovered task artifacts |
| Robust control identity | Implemented | Rendered text, label relations, frame-local components, canonical signature, uniqueness gate | Fails closed on absent or ambiguous matches |
| Typed inputs/outputs | Implemented | Closed object contracts and runtime validation | Distinct contracts for balance, transaction, payoff, and card tasks |
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
| Canvas visual control | Implemented, tested | One canvas-only workbench exposes three substantial tasks using geometry-free rendered candidates and CSS-pixel re-grounding | Browser transport only |
| Native desktop extension | Designed | Surface ports and PNG-based grounding seam | No OS transport adapter |
| Multi-tenant reuse | Evidenced | `3.2.0` uses one artifact across Harbor and Summit with reordered rows | 15-case visual/DPR matrix |
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
- Rendered OCR, label relations, and visual signatures execute today; legacy image anchors remain supported; accessibility-path and native desktop adapters do not.
- Compatibility fingerprints are recorded; startup/runtime drift enforcement is not implemented.
- Published capabilities and evidence survive restart; operational run and handoff state does not.
- The control plane is not a complete product UI.
- The API is synchronous and has no WebSocket endpoint.
- Authentication, authorization, retention enforcement, PostgreSQL, object storage, and distributed workers are not implemented.
