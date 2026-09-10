# Discovery, Replay, and Error Semantics

## 1. Separation of responsibilities

Discovery and replay intentionally optimize for different properties.

| Property | Discovery | Replay |
|---|---|---|
| Decision source | Model | Artifact |
| Perception | Screenshot-led observation | Declared locators and conditions |
| Flexibility | Exploratory within policy | Strict and bounded |
| Output | Successful run plus compiled artifact | Typed invocation result |
| Model access | Required for genuine run | Structurally forbidden |
| Human escalation | Stuck, risky, or uncertain | Declared exceptional or unsafe state |

Sharing surface, policy, evidence, and intervention ports does not mean sharing decision logic.

## 2. Normalized observation

Each observation contains only what the discovery domain requires:

- Observation ID and timestamp
- Session and step number
- Screenshot evidence reference or in-memory image
- Viewport dimensions and device scale
- Current application-family route classification
- Window and frame inventory
- Active element summary
- Open dialog summary
- Compact accessibility landmarks when available
- Change summary from the previous observation
- Policy-relevant state

Raw DOM is not the primary model input. It may be captured as failure evidence or used internally by the web surface adapter to resolve a model-selected control.

## 3. Normalized action proposal

The provider adapter converts model output into one of:

- Click at visible coordinates
- Type text from an input reference or allowed literal
- Press a bounded key sequence
- Scroll a bounded amount
- Wait for an explicit duration within policy
- Extract visible data
- Declare completion with supporting observation facts
- Request human intervention with a reason

Each proposal includes:

- Provider call reference
- Concise action rationale suitable for an audit log
- Target description
- Confidence when supplied by the provider
- Proposed risk class
- Expected visible effect

Raw hidden chain-of-thought is neither requested nor persisted.

## 4. Discovery state machine

```text
CREATED
  -> VALIDATING
  -> STARTING_SESSION
  -> OBSERVING
  -> DECIDING
  -> POLICY_CHECK
  -> ACTING
  -> VERIFYING
  -> OBSERVING ...
  -> COMPILING
  -> SUCCEEDED
```

Alternate terminal or suspended paths:

```text
VALIDATING -> FAILED
DECIDING -> INTERVENTION_PENDING
POLICY_CHECK -> BLOCKED or INTERVENTION_PENDING
VERIFYING -> INTERVENTION_PENDING or FAILED
any active state -> TIMED_OUT or CANCELLED
```

Only legal transitions are persisted. The UI derives status from the domain state, not string conventions in logs.

## 5. Discovery loop

For every step:

1. Confirm automation owns the control lease.
2. Check run-wide step and time budgets.
3. Capture a normalized observation.
4. Record a redacted observation event.
5. Ask the configured provider for one normalized action.
6. Validate the action schema.
7. Reclassify risk independently of the model.
8. Evaluate effective policy.
9. Block or escalate when required.
10. Capture pre-action evidence according to policy.
11. Execute exactly one action.
12. Capture the surface result and a new observation.
13. Resolve the affected element into a locator bundle when applicable.
14. Evaluate the expected effect and progress signal.
15. Record the normalized action/result, not provider internals.
16. Continue, complete, escalate, or stop.

## 6. Progress and stuck detection

Discovery is stuck when any configured condition is met:

- Consecutive observations have the same perceptual fingerprint after actions.
- The provider repeats an equivalent action against the same state.
- The provider proposes invalid actions repeatedly.
- The intended target cannot be resolved after action execution.
- A page reports an unclassified error.
- The model expresses uncertainty below the safe threshold.
- The run reaches its maximum steps or wall-clock budget.
- Policy repeatedly denies required progress.

Stuck detection uses bounded counters and normalized fingerprints. It does not wait indefinitely for the model to admit failure.

## 7. Completion and compilation

A model completion proposal is advisory. The runtime accepts it only if:

- The current observation contains the claimed goal evidence.
- Declared outputs can be extracted and validated.
- The discovered input values can be replaced with symbolic references.
- The run contains no unresolved policy violation.
- A final checkpoint can be expressed with deterministic conditions.

The compiler performs:

1. Remove navigation and observation noise that has no semantic effect.
2. Preserve actions required to reach the verified state.
3. Replace invocation values with typed input references.
4. Convert affected controls to ordered locator bundles.
5. Infer only directly observed postconditions.
6. Attach explicitly recognized business outcomes and recoveries.
7. Bind extracted data to output fields.
8. Construct the final checkpoint.
9. Validate schema, policy, graph bounds, and redaction.
10. Emit an immutable draft artifact and evidence linkage.

No artifact is generated from a failed or unverified discovery.

## 8. Replay state machine

```text
CREATED
  -> VALIDATING_INPUT
  -> RESOLVING_ARTIFACT
  -> STARTING_SESSION
  -> VERIFYING_PRECONDITIONS
  -> RESOLVING_TARGET
  -> POLICY_CHECK
  -> EXECUTING_STEP
  -> VERIFYING_EFFECT
  -> next step
  -> VERIFYING_CHECKPOINT
  -> SUCCEEDED
```

At each step, outcome detectors and recovery triggers are evaluated before the general failure classifier.

## 9. Target resolution algorithm

For each locator candidate in order:

1. Resolve declared window and frame scope.
2. Query using the candidate strategy.
3. Wait only within the remaining step budget.
4. Count matches.
5. Validate visibility, enabled state, and other declared state.
6. If exactly the expected set matches, select it.
7. If ambiguous, record the ambiguity and try a genuinely independent fallback only when allowed.
8. If absent, proceed to the next candidate.

Resolution fails when:

- All candidates are absent.
- A candidate remains ambiguous and no safer independent candidate succeeds.
- Frame/window scope is missing.
- The resolved target violates the expected state.
- A drift rule requires human review.

The resolver never silently uses a nearby element or fuzzy model choice.

## 10. Step execution semantics

Before acting:

- Validate the current route and preconditions.
- Evaluate known terminal business outcomes.
- Confirm control ownership.
- Classify and authorize the action.
- Record an action-intent event.

After acting:

- Record adapter-level action status.
- Re-observe the relevant scope.
- Detect known dialogs and application errors.
- Evaluate postconditions.
- Verify whether the action effect occurred.
- Persist sanitized evidence.
- Record an action-result event.

An adapter reporting that a click was dispatched does not mean the step succeeded.

## 11. Retry rules

Retry requires all of the following:

- The artifact explicitly allows retry for the observed condition.
- The maximum attempt count has not been reached.
- The total step deadline has not expired.
- Policy still allows the action.
- The prior action is idempotent, or its intended effect is proven absent.

Examples:

- Safe to retry: wait for account table after a transient load timeout.
- Safe to retry after proof: click Search only when results and loading state both prove the search was not submitted.
- Unsafe to retry: submit an irreversible transfer without a transaction-idempotency guarantee.

## 12. Error taxonomy

### Validation failures

Detected before browser creation:

- Invalid input
- Unsupported artifact schema
- Incompatible tenant
- Missing entry point
- Policy configuration conflict
- Missing runtime dependency

### Business outcomes

Expected target-application answers:

- Member not found
- Account type absent
- Account closed, if explicitly declared as a capability outcome

They return `business_outcome`, not `failure`.

### Recoverable conditions

Conditions with an explicit bounded response:

- Slow load
- Known informational interstitial
- One session-expiry recovery
- Temporarily absent element
- Stale frame after known navigation

Exhausted recovery becomes a hard failure with recovery history.

### Policy outcomes

- Denied action: terminal `policy_blocked` failure when the capability cannot proceed.
- Approval required: `intervention_required` with a live session.
- Unsafe destination: stop before navigation.

### Surface failures

- Target absent
- Target ambiguous
- Target disabled
- Frame missing
- Unexpected window
- Browser crash
- Navigation failure
- Unsupported dialog

### Application failures

- Permission denied
- Internal application error
- Validation error not declared as a business outcome
- Session expired beyond recovery budget
- Inconsistent or incomplete rendered data

### Verification failures

- Postcondition mismatch
- Extraction parse failure
- Output validation failure
- Identity mismatch
- Checkpoint mismatch

### Infrastructure failures

- Database unavailable
- Evidence write failure
- Provider unavailable during discovery
- Run worker cancellation
- WebSocket failure, only when human control depends on it

## 13. Terminal result contract

### Success

```json
{
  "status": "success",
  "run_id": "run_...",
  "capability": {"id": "member.lookup_savings_balance", "version": "1.0.0"},
  "outputs": {
    "account_type": "savings",
    "currency": "USD",
    "available_balance": "1420.57",
    "as_of": "2026-09-08T15:04:05Z"
  },
  "checkpoint": {"id": "savings_balance_verified", "verified": true},
  "evidence_manifest": "evidence://..."
}
```

### Business outcome

```json
{
  "status": "business_outcome",
  "run_id": "run_...",
  "code": "member_not_found",
  "details": {"member_id": "***2345"},
  "evidence_manifest": "evidence://..."
}
```

### Failure

```json
{
  "status": "failure",
  "run_id": "run_...",
  "code": "target_ambiguous",
  "step_id": "account.open_savings",
  "message": "Expected one Savings row and observed two.",
  "expected": {"count": 1},
  "observed": {"count": 2},
  "recoverable": false,
  "evidence_manifest": "evidence://..."
}
```

### Intervention required

```json
{
  "status": "intervention_required",
  "run_id": "run_...",
  "intervention_id": "int_...",
  "code": "unexpected_dialog",
  "step_id": "search.submit",
  "session_live": true,
  "control_owner": "automation_paused"
}
```

## 14. No-model replay guarantee

The guarantee is enforced at multiple layers:

- Replay has no dependency on `ModelProvider`.
- Replay service constructors do not accept a model client.
- Replay deployment permissions do not require a provider API key.
- Tests install a provider that fails immediately if called.
- Replay evidence records provider calls as zero.
- Offline replay runs with all outbound model access disabled.

## 15. Required scenario behavior

| Scenario | Expected behavior |
|---|---|
| Valid member | Return typed balance and verified checkpoint |
| Unknown member | Return `member_not_found` business outcome |
| Slow result load | Wait/retry within declared budget, then continue |
| Known interstitial | Execute declared recovery once |
| Session expiry | Recover once, otherwise hard failure or intervention |
| Permission denied | Return application failure with screenshot |
| Duplicate savings rows | Stop with target ambiguity; never guess |
| Unexpected confirmation | Pause and request human intervention |
| Output cannot parse | Return extraction/validation failure |
| Final member mismatch | Return checkpoint failure, no outputs |
| Model unavailable | Discovery fails; replay remains operational |
