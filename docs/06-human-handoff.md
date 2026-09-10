# Human Escalation and Same-Session Handoff

## 1. Objective

When ReplayForge cannot proceed safely, it must pause, preserve the live session, give one human operator control, record what happens, and resume only after state is revalidated.

A notification or a new browser session is not a handoff. The browser context, cookies, page history, windows, frames, and current target state must remain the same.

## 2. Escalation triggers

### Discovery triggers

- Repeated no-progress fingerprint
- Repeated equivalent model action
- Model uncertainty below the configured safe threshold
- Model requests help
- Proposed sensitive action requires approval
- Policy denies the action needed to continue
- Unknown dialog or application state
- Maximum safe autonomous step count reached

### Replay triggers

- Artifact explicitly declares human intervention for the observed condition
- Sensitive action requires approval
- Session recovery requires interactive authentication
- Unexpected dialog cannot be classified
- Locator drift is plausible but unsafe to resolve automatically
- Recovery budget is exhausted and the session remains actionable

### Non-escalatable failures

Some failures terminate rather than preserve a session:

- Invalid artifact or input
- Unsupported schema
- Browser process unavailable
- Evidence persistence unavailable when audit evidence is mandatory
- Explicit irreversible-action policy violation
- Session already destroyed

## 3. Intervention record

An intervention is immutable in identity and append-only in events.

Required fields:

- Intervention ID
- Run and session IDs
- Capability identity/version, when applicable
- Goal summary
- Application family and tenant
- Discovery/replay mode
- Current step and attempt
- Trigger code and explanation
- Current route classification
- Screenshot evidence key
- Observation summary
- Last completed action
- Policy decision reference
- Created timestamp
- Status
- Control owner
- Lease version
- Operator identity when claimed
- Resolution and resume checkpoint

## 4. Status model

```text
OPEN
  -> CLAIMED
  -> RESUMING
  -> RESOLVED

OPEN or CLAIMED
  -> TERMINATED

CLAIMED
  -> OPEN          operator releases or lease expires safely
```

Illegal transitions return a conflict and do not change state.

## 5. Control ownership

Control ownership is separate from run status.

Owners:

- `automation`
- `automation_paused`
- `human:<principal_id>`
- `none`

The control lease contains:

- Session ID
- Owner
- Monotonic version
- Issued timestamp
- Last heartbeat
- Expiry timestamp
- Associated intervention, if any

Every action command includes the expected lease version. The lease repository performs an atomic compare-and-swap. This prevents a stale browser tab or automation task from acting after ownership changes.

## 6. Pause protocol

1. The automation task decides escalation is required.
2. It stops producing new actions.
3. It waits for any already-dispatched surface action to return.
4. It captures a fresh observation and sanitized screenshot.
5. It transitions ownership from `automation` to `automation_paused`.
6. It creates the intervention in the same database transaction as the durable run-state transition.
7. It begins or maintains the live frame stream.
8. It emits an intervention-created event.

If the database transaction fails, control remains paused and no human input is accepted until state is reconciled.

## 7. Claim protocol

1. The operator opens the intervention.
2. The UI displays the current screenshot, reason, step, and policy context.
3. The operator selects Claim Control.
4. The runtime validates operator identity, intervention status, session liveness, and lease version.
5. Ownership atomically changes to `human:<principal_id>`.
6. A new lease version is returned to the UI.
7. Input forwarding becomes active only after the client acknowledges that version.

Concurrent claims return a conflict containing only the current owner display name and claim time.

## 8. Live browser transport

### Frames

- Chromium frames are captured using a CDP screencast adapter.
- Frames contain session ID, sequence, dimensions, scale, timestamp, and encoded image.
- The client acknowledges the latest processed frame to provide backpressure.
- Old frames may be dropped; control and audit events may not.
- Screenshot polling is an adapter fallback, not a separate product behavior.

The current local vertical slice implements that fallback as a lease-guarded PNG endpoint. Browser creation, frame capture, and teardown all execute on the same session-owner thread; continuous CDP screencasting remains a production transport follow-on.

Each fallback response carries an ordered frame sequence, authoritative viewport dimensions, and the next accepted client-input sequence in response headers. The operator cannot issue a coordinate command until one of these frames has been received.

### Human input

Supported input:

- Pointer move, down, up, click, and wheel
- Key down and up
- Text insertion
- Focus request

Each message contains:

- Session and intervention ID
- Lease version
- Client sequence
- Frame sequence used for coordinate mapping
- Viewport dimensions
- Input payload

The server rejects input when frame dimensions are stale beyond tolerance, the lease is invalid, or the session route is no longer allowed.

The implemented local HTTP fallback deliberately narrows this design to one left-click per latest frame, text insertion into the already-focused control, and a fixed navigation-key allowlist. It requires an exact frame and viewport match, accepts the next client sequence exactly once, invalidates the frame after dispatch, and executes on the retained browser's owner thread. Audit events retain input kind, sequence, coordinates or character count, but never typed text. Pointer movement, wheel, arbitrary key chords, and focus-by-selector remain part of the WebSocket/CDP follow-on rather than being silently approximated.

### Audit grouping

High-frequency pointer moves are not stored individually. Semantically meaningful actions—click, text entry, key chord, scroll, navigation, and dialog response—are grouped and recorded with before/after observations.

## 9. Operator experience

The intervention workspace displays:

- Live viewport
- Control-owner badge
- Connection quality
- Goal and capability
- Current step and last action
- Escalation reason
- Policy/risk context
- Evidence timeline
- Claim, Resume, Complete, Release, and Terminate actions

Potentially sensitive controls use an explicit confirmation panel showing the exact action, target, scope, and reason.

## 10. Resume protocol

1. The operator selects Resume Automation.
2. The UI sends the expected lease version and optional note.
3. Ownership transitions from human to `automation_paused`; intervention status becomes `RESUMING`.
4. Human input is immediately disabled.
5. The runtime captures a fresh observation.
6. Policy re-evaluates the route and session state.
7. The resume checkpoint is evaluated.
8. Unused approvals are invalidated.
9. If valid, ownership changes to `automation` and execution continues at the declared resume point.
10. If invalid, the intervention returns to `OPEN` with a new reason and screenshot.

Automation never resumes based only on an operator button click.

The current replay implementation retains a typed continuation containing the interrupted step index, validated inputs, accumulated outputs, original session, and initial fingerprint. Resume revalidates the effective origin/route policy, declared business outcomes, every interrupted-step postcondition, and an observable state change on the browser-owner thread. Failure returns the intervention to `OPEN`; success returns the lease to automation, skips the already human-completed step, executes only the remaining deterministic steps, finalizes evidence exactly once, and closes the session. A subsequent escalation rebinds the same live session to its new intervention ID. Discovery-loop continuation remains a documented follow-on and safely reopens rather than pretending to resume.

## 11. Resume checkpoint

The checkpoint identifies acceptable post-human states. It may require:

- Allowed route
- Expected page heading
- Required control present
- Dialog absent
- Member identity still matches the invocation
- Prior action effect visible
- No error banner

The checkpoint may select one of several explicit resume points. It cannot allow arbitrary continuation.

## 12. Human completion

The operator may mark the run complete only when an artifact-declared checkpoint or explicit manual-completion contract can be validated. Operator assertion alone cannot produce automated success.

If the operator completes a step not represented by the artifact:

- The run may return a human-completed result distinct from deterministic success.
- The action is not automatically learned into the artifact.
- A future discovery/review process may incorporate it into a new version.

## 13. Release and termination

### Release

The operator can release control without resuming. Ownership returns to `automation_paused`, and the intervention becomes available for another claim.

### Termination

Termination:

- Disables all input.
- Captures final failure evidence.
- Sets owner to `none`.
- Closes the surface session after evidence completes.
- Produces a terminal run result.

## 14. Disconnect and expiry

- The UI sends heartbeats while human control is active.
- Missing heartbeats first disable input, then expire the lease after a grace period.
- Lease expiry returns the intervention to `OPEN`; automation does not resume automatically.
- Reconnection requires a new claim and lease version.
- Browser-stream interruption does not destroy the session.
- Server restart recovery marks live sessions unavailable unless a surface adapter explicitly supports reconnection.

## 15. Human-action recording

Recorded fields:

- Principal ID and display label
- Timestamp range
- Normalized action type
- Redacted target description
- Route classification
- Policy decision
- Before/after evidence references
- Result
- Lease version

Raw keystrokes that may contain sensitive data are never stored. Text input is recorded as a field classification and length unless a safe synthetic value is explicitly allowed.

## 16. Security invariants

- Automation and human cannot own control simultaneously.
- A stale client cannot issue actions.
- A human cannot act outside the session tied to the intervention.
- A takeover does not disable origin, route, or irreversible-action policy.
- Resume always re-observes the live surface.
- Session frames never include cookies or browser storage metadata.
- Provider credentials never reach the control plane.

## 17. Test scenarios

- Successful claim, manual dialog dismissal, and resume.
- Two operators race to claim; exactly one succeeds.
- Automation attempts action after pause; command is rejected.
- Client sends input with stale lease; input is rejected.
- Client maps coordinates from an old frame size; input is rejected.
- Operator disconnects; input disables and lease expires safely.
- Operator resumes from valid state; automation continues.
- Operator resumes from wrong member page; intervention reopens.
- Operator requests irreversible action; policy blocks it.
- Browser stream fails while session remains alive; intervention remains recoverable.
- Human completion lacks checkpoint proof; success is rejected.

## 18. Acceptance criteria

- The exact browser context ID remains unchanged across pause, claim, manual action, and resume.
- Audit evidence distinguishes model, replay, and human actions.
- The UI always displays the authoritative control owner.
- Concurrent input is prevented by lease enforcement, not UI convention.
- Resume is conditional on deterministic state validation.
- The complete handoff is demonstrable locally without an external co-browsing service.
