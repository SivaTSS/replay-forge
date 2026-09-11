# Testing and Evidence Strategy

## 1. Verification principle

Tests prove contracts; evidence proves that the integrated system actually performed the assignment. Fixtures must never be presented as a genuine discovery run.

## 2. Test layers

### Domain unit tests

- Artifact version and semantic validation
- Input/output binding
- Condition composition
- Outcome precedence
- Retry/effect-verification rules
- Run state transitions
- Control lease compare-and-swap
- Policy intersection and risk escalation
- Redaction decisions
- Tenant overlay restrictions

Use deterministic clocks and identifiers. Domain tests require no database, browser, network, or model.

### Property-based tests

Use Hypothesis to generate artifacts, locators, policies, and results. Prove that unsupported schemas reject, retries remain bounded, overlays never widen policy, canonical hashes are stable, invalid output bindings fail, and serialized valid artifacts round-trip.

### Adapter contract tests

Every implementation of ModelProvider, SurfaceDriver, repositories, and EvidenceStore runs a shared contract suite. Fakes must behave according to the same success/failure vocabulary as real adapters.

### Persistence tests

Run against real PostgreSQL in CI. Cover transaction boundaries, immutable versions, ordered events, one terminal result, concurrent intervention claims, stale leases, migrations from empty database, and rollback on failure.

### Surface integration tests

Use Playwright against the running demo bank. Cover frames, generated IDs, semantic and structural locator fallbacks, dialogs, navigation, extraction, traces, screenshots, session expiry, latency, permission denial, and browser teardown.

### Replay contract tests

Construct replay without a model provider. Fail the test if any model adapter is instantiated or network model call occurs. Cover success, business outcome, recovery success/exhaustion, ambiguous/missing target, disabled target, unexpected dialog, output parse failure, identity mismatch, checkpoint mismatch, cancellation, and evidence failure.

### Handoff tests

- Automation stops after lease transfer.
- Two operators cannot claim the same version.
- Stale client input is rejected.
- Human input changes the original page/context.
- Disconnect disables input.
- Resume captures a new observation.
- Failed resume checkpoint reopens intervention.
- Termination records final evidence and closes session.

### Frontend tests

Component tests cover all result cards, forms, status badges, timelines, locator views, evidence states, lease conflicts, and disconnected states. Playwright E2E covers discovery submission, capability review, replay, business outcome, intervention claim/control/resume, and tenant selection.

### Accessibility and quality

- Automated axe checks on primary pages
- Keyboard-only primary flows
- Focus restoration and live-region behavior
- Ruff, Pyright, ESLint, and strict TypeScript
- OpenAPI generation drift check
- Artifact JSON Schema drift check
- Dependency and secret scans

## 3. Deterministic fault scenarios

The demo bank exposes fault configuration only in demo/test mode:

- `none`
- `member_not_found`
- `slow_once`
- `known_interstitial`
- `session_expired_once`
- `permission_denied`
- `unexpected_dialog`
- `application_error`
- `checkpoint_identity_mismatch`

Fault state is configured before a run and recorded in the evidence manifest. Replay still detects faults through the rendered UI; it does not query fault state.

## 4. Required evidence layout

```text
evidence/
  README.md
  discovery-success/
  replay-success/
  replay-member-not-found/
  replay-recovery/
  replay-hard-failure/
  human-handoff/
  tenant-reuse/
```

Each scenario contains:

- `manifest.json`: versions, commands, synthetic scenario, hashes, redaction status
- `events.jsonl`: ordered sanitized domain events
- `result.json`: typed terminal result
- `screenshots/`: selected redacted before/after/failure images
- `trace.zip`: where required
- Artifact reference and content hash

Run manifests carry binary screenshots and traces in a separate bounded `attachments` collection,
so ordered JSON domain events remain independently parseable. Attachment media type, size, content
hash, run-key ownership, and PNG/ZIP signature are checked both before persistence and during
independent verification. Reviewer-bundle manifests map them only to numbered `screenshots/*.png`
or one `trace.zip`; older bundles without attachments remain valid.

The successful discovery directory also includes provider/model metadata and the produced YAML artifact. It excludes credentials, raw chain-of-thought, and sensitive request headers.

## 5. Evidence scenarios

1. Genuine discovery: model searches synthetic member, opens savings, extracts balance, and emits a valid artifact.
2. Successful replay: same artifact with a different member input; model disabled; typed output and checkpoint succeed.
3. Business outcome: unknown member produces `member_not_found`, not failure.
4. Recovery: one transient slow/interstitial condition is detected, recovered within budget, and recorded.
5. Hard failure: permission denial or checkpoint mismatch returns expected/observed state and rich failure evidence.
6. Handoff: automation pauses, operator claims the same session, performs a manual step, resumes, and ownership history is visible.
7. Tenant reuse: base artifact replays on the second variant through a narrow overlay.
8. Agent invocation: capability API validates typed arguments and returns the replay result.

For any replay failure after a browser session opens, the engine attempts a masked `failure-state`
capture before teardown and records whether that capture was `captured` or `unavailable` on the
typed failure event. Pre-browser contract failures are explicitly `not_applicable`; a screenshot
adapter failure never replaces the original business-relevant failure code.

## 6. Evidence integrity

- Manifest lists SHA-256 for every file.
- Artifact hash matches the registry version.
- Event sequence is monotonically ordered.
- Terminal result is represented exactly once.
- Redaction scanner passes before commit.
- Commands and commit SHA needed to reproduce the run are recorded.
- Synthetic member IDs are identified as synthetic.
- Missing optional evidence is explicitly listed; it is never silently omitted.

## 7. CI gates

Required on every pull request:

- Backend lint, types, unit/property tests
- Frontend lint, types, component tests
- Artifact schema and OpenAPI drift
- PostgreSQL integration tests
- Offline replay E2E
- Accessibility smoke tests
- Secret and evidence-redaction scans

Live-model tests are manually triggered because they require credentials and incur cost. The committed genuine evidence is produced from a successful manual run and validated by CI without calling the model.

## 8. Completion gate

The project is complete only when README commands work from a clean clone, every traceability row has a passing test and evidence reference, replay tests prove model absence, all required evidence passes integrity/redaction validation, and the full reviewer walkthrough succeeds without undocumented intervention.
