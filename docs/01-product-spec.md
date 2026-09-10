# Product Specification

## 1. Product statement

ReplayForge is a computer-use capability compiler and execution runtime. It accepts an operator's natural-language goal, lets a model discover how to complete that goal through a real user interface, compiles the successful interaction into a typed capability, and executes later invocations deterministically without asking a model what to do.

The first concrete surface is a synthetic bank member-servicing application. The design must remain valid for modern web, legacy web, and native desktop adapters.

## 2. Problem definition

Financial institutions rely on operational applications that do not expose usable APIs. An AI agent may know what work should happen but cannot safely or reliably perform that work through those applications. Re-running an unconstrained model for every invocation is costly, difficult to review, nondeterministic, and unsafe.

ReplayForge separates exploration from production execution:

1. A model explores the UI once under strict policy.
2. The successful interaction is normalized and parameterized.
3. A reviewer can understand the resulting contract and flow.
4. The runtime replays the approved flow with stable targeting and explicit checks.
5. Exceptional conditions produce structured outcomes or request human assistance.

## 3. Users and jobs

### 3.1 Integration engineer

The integration engineer needs to:

- Register a target application and its allowed entry point.
- Describe a reusable goal in natural language.
- Run and observe discovery.
- Inspect whether the compiled artifact matches the intended behavior.
- Diagnose failures using evidence without seeing unredacted secrets.
- Specialize a base capability for a tenant without duplicating the entire flow.

### 3.2 Human operator

The operator needs to:

- See interventions requiring attention.
- Understand exactly why automation paused.
- Claim exclusive control of the existing session.
- Operate the live application without losing cookies, navigation state, or context.
- Return control to automation or terminate safely.
- Leave an auditable record of manual actions and decisions.

### 3.3 Calling agent

The calling agent needs to:

- Discover an available capability by name and description.
- Obtain a machine-readable input/output schema.
- Invoke it with typed parameters.
- Receive success, a known business outcome, or a debuggable failure.
- Avoid knowledge of selectors, browser sessions, or model prompts.

### 3.4 Reviewer

The reviewer needs to:

- Run the complete demonstration from documented commands.
- Confirm that discovery genuinely uses a model against a live UI.
- Confirm that replay cannot call the model.
- Inspect the artifact, locator choices, policies, outputs, and checkpoints.
- Exercise happy-path, business-outcome, recovery, hard-failure, and handoff paths.
- Trace every assignment requirement to code, tests, and evidence.

## 4. Primary workflow

The primary capability is `member.lookup_savings_balance`.

Input:

- `member_id`: non-empty string matching the configured synthetic member-ID format.

Output on success:

- `member_id`: redacted or tokenized according to the output audience.
- `account_type`: literal `savings`.
- `currency`: ISO 4217 currency code.
- `available_balance`: decimal represented as a string at transport and artifact boundaries.
- `as_of`: timestamp displayed by the target application.

Expected business outcome:

- `member_not_found`: the search completed correctly and the target application reported that no member exists.

Representative failure outcomes:

- Input rejected before session creation.
- Session expired and recovery budget exhausted.
- Permission denied by the target application.
- Required account row ambiguous or absent.
- Unexpected dialog cannot be safely classified.
- Final checkpoint does not match the extracted member/account.
- Policy blocks a proposed action or destination.

## 5. End-to-end user journey

### 5.1 Discovery

1. The engineer opens Run Studio and selects Discovery.
2. The engineer selects an application family and tenant variant.
3. The engineer enters the goal and declares candidate inputs and outputs.
4. The system validates policy, provider configuration, and target reachability.
5. The runtime opens a new isolated browser context.
6. The model observes the live surface and proposes one normalized action at a time.
7. Policy evaluates every proposed action before execution.
8. The surface adapter executes allowed actions and captures before/after evidence.
9. The recorder associates successful actions with robust control locators.
10. The model declares completion only when visible evidence supports it.
11. The compiler parameterizes values, assigns outcomes, and builds the artifact.
12. The runtime validates the artifact against its schema and performs static safety checks.
13. The control plane shows the resulting contract, flow, locators, and warnings.

### 5.2 Replay

1. A user or calling agent selects a capability version.
2. Inputs are validated against the declared schema.
3. Policy resolves the tenant configuration and execution permissions.
4. The runtime opens a session at the declared entry point.
5. The replay engine executes artifact steps in order with no model dependency.
6. Each step resolves a unique target, checks preconditions, acts, and checks postconditions.
7. Known exceptional states are classified before general failure handling.
8. Bounded recoveries may retry or dismiss a specifically declared condition.
9. The final checkpoint proves the requested state and binds declared outputs.
10. The caller receives a typed result and evidence references.

### 5.3 Human intervention

1. Discovery or replay identifies a condition it cannot safely handle.
2. Automation transitions to `pausing` and stops issuing actions.
3. The runtime creates an intervention with current state and evidence.
4. An operator claims the control lease.
5. The operator acts through the embedded live viewport on the same session.
6. Manual actions are logged as human-originated events.
7. The operator requests resume, completion, or termination.
8. On resume, the runtime captures a fresh observation and validates the resume checkpoint.
9. Automation continues only after ownership returns to it.

## 6. Functional requirements

### 6.1 Discovery

- Accept goal, target, tenant, model configuration, stopping limits, and declared data contract.
- Operate a real browser surface using screenshots and input actions.
- Support click, type, key press, scroll, wait, extract, complete, and escalate intents.
- Stop on success, maximum steps, wall-clock timeout, repeated no-progress, policy block, provider failure, or human escalation.
- Preserve normalized action history separately from provider transcripts.
- Compile only successful, checkpoint-supported runs.

### 6.2 Capability registry

- Persist immutable capability versions.
- Validate every artifact before it becomes invocable.
- Expose input and output schemas.
- Render flow, outcomes, recoveries, locators, and policy classifications.
- Resolve application-family defaults plus tenant overlays.
- Compute and expose an artifact content hash.

### 6.3 Replay

- Execute without constructing or calling a model provider.
- Resolve control targets through ordered locator candidates.
- Require unique, visible, enabled targets where appropriate.
- Enforce explicit time and retry budgets.
- Verify step postconditions and final checkpoints.
- Return one typed terminal result.
- Capture failure evidence before session teardown.

### 6.4 Safety

- Deny actions outside allowed origins, routes, action types, and fields.
- Classify actions as read-only, reversible, sensitive, or irreversible.
- Block irreversible actions in the submitted implementation.
- Require recorded human confirmation for sensitive actions.
- Redact configured sensitive values before persistence.
- Prevent secrets and raw model reasoning from entering artifacts.

### 6.5 Observability

- Assign trace, run, session, capability, and tenant identifiers.
- Emit structured lifecycle and step events.
- Capture screenshots at configurable boundaries.
- Capture a Playwright trace for failures and selected demonstration runs.
- Link persisted evidence from the run detail view.
- Make redaction status explicit.

### 6.6 Human handoff

- Detect stuck, blocked, risky, and unrecoverable conditions.
- Create actionable intervention requests.
- Maintain the same live session during handoff.
- Guarantee a single control owner.
- Record operator actions and ownership transitions.
- Revalidate state before automated resume.

## 7. Non-functional requirements

### Reliability

- Deterministic replay behavior must be reproducible for the same artifact, tenant configuration, inputs, and target state.
- A retry must never duplicate an unverified non-idempotent action.
- Runtime crashes must not misreport success.
- Terminal results are written once and are immutable.

### Security and privacy

- All credentials are supplied at runtime and excluded from repository history.
- Synthetic data is used throughout demonstrations.
- Sensitive fields are redacted before log/evidence serialization.
- The UI never receives provider credentials or browser storage state.

### Performance

- Control-plane interactions unrelated to model inference should feel immediate.
- Run events should appear in the UI within one second under local conditions.
- Browser stream degradation must not pause automation.
- Evidence capture must be bounded to avoid unbounded storage growth.

### Accessibility

- The control plane must support keyboard navigation and visible focus.
- Status cannot rely on color alone.
- Live announcements are used for ownership and terminal-state changes.
- Automated accessibility checks must report no critical violations on primary screens.

### Operability

- A clean clone must start with documented commands.
- Health and readiness endpoints must distinguish process health from dependency readiness.
- Configuration errors must be reported before a run starts.
- Offline mode must allow UI exploration and deterministic fixture replay without a live model.

## 8. In scope

- One deeply implemented savings-balance capability.
- Genuine LLM-driven discovery against a live synthetic banking surface.
- Typed artifact and deterministic replay.
- Explicit business, recovery, failure, and intervention outcomes.
- Same-session operator takeover.
- Two tenant variants for one application family.
- Agent-facing capability catalog and invocation endpoint.
- Modern control plane and detailed evidence inspection.

## 9. Out of scope

- Real banking systems, credentials, or PII.
- Native desktop implementation.
- General-purpose autonomous browsing.
- Unbounded model recovery during replay.
- Workflow authoring by arbitrary drag-and-drop editing.
- Production multi-region deployment.
- Enterprise identity-provider integration.
- Multi-operator co-browsing.
- More than one live model-provider adapter.

## 10. Product acceptance criteria

- A reviewer completes discovery and replay from the UI and CLI using documented steps.
- The capability artifact is understandable without reading provider output.
- Model access is demonstrably absent from replay.
- All required outcome classes can be triggered deterministically.
- Human control occurs within the original session and is audit-visible.
- One artifact replays across two tenant variants without duplication.
- The UI clearly represents every run state and control owner.
- No evidence fixture contains configured secrets or raw sensitive fields.
- Every assignment requirement maps to a test and evidence target.
