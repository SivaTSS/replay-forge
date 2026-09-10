# Safety and Data Handling

## 1. Safety objective

ReplayForge operates software used to view or change regulated financial information. Safety must be enforced by deterministic platform controls, not by asking a model to behave carefully.

The submission uses synthetic data, but its architecture must demonstrate the controls required for real deployments.

## 2. Trust boundaries

Untrusted or partially trusted inputs:

- Natural-language goals
- Invocation parameters
- Model-proposed actions
- Target-application content
- Tenant overlays
- Uploaded artifact YAML
- Operator input events
- Provider responses

Trusted enforcement components:

- Artifact validator
- Policy evaluator
- Route classifier
- Risk classifier
- Control-lease manager
- Redaction pipeline
- Output validator
- Evidence writer

No untrusted component may directly execute a surface action or persist unsanitized evidence.

## 3. Effective policy

Effective permission is the intersection of five layers:

```text
platform policy
  ∩ application-family policy
  ∩ tenant policy
  ∩ capability policy
  ∩ invocation constraints
```

A lower layer may narrow but never widen its parent. If policy layers conflict, the more restrictive result wins and the conflict is recorded.

## 4. Policy inputs

Every decision receives:

- Principal type and identity
- Run type: discovery, replay, or human control
- Capability ID and version
- Application family and tenant
- Current normalized route
- Proposed action and target description
- Proposed input source
- Independently classified risk
- Current control owner
- Prior approval reference, when applicable
- Relevant data classifications

## 5. Policy decision

The evaluator returns one of:

- `allow`
- `deny`
- `require_human_approval`

Every decision contains:

- Stable decision ID
- Stable reason code
- Human-readable explanation
- Matched policy rules
- Effective risk class
- Required evidence
- Redaction directives
- Expiry and scope of approval, if relevant

A policy denial occurs before the action reaches the surface adapter.

## 6. Allowlists

### Origins

- Exact schemes and hosts are registered per application family.
- Wildcards are forbidden by default.
- Ports are explicit in local development.
- Redirects are re-evaluated before following.
- Popups and new windows inherit no permission automatically.

### Routes

- Routes use normalized patterns such as `/members/search` or `/accounts/:account_id/details`.
- Query parameters are separately allowlisted.
- Sensitive values are never embedded in logged normalized routes.
- Unknown routes are denied until classified.

### Actions

Allowed action types are configured per capability. A read-only capability cannot propose selection or submission actions that mutate account state.

### Fields

Type actions are allowed only into controls whose purpose and data classification match policy. Password, security-answer, token, and payment-instruction fields are denied in this submission.

### Navigation

Artifact navigation references registered symbolic entry points. Direct model-proposed URLs are rejected unless policy resolves them to an allowed entry point.

## 7. Risk model

### `read_only`

Examples:

- Search for a member
- Open account details
- Read a balance
- Change tabs without altering server state

Default behavior: allow when origin, route, target, and action type are allowlisted.

### `reversible`

Examples:

- Change a local filter
- Open or close a non-destructive dialog
- Modify a draft that is not submitted

Default behavior: allow only when the capability explicitly declares it and a postcondition proves the reversible state.

### `sensitive`

Examples:

- Reveal full account identifiers
- Submit a profile change
- Reach a transaction confirmation action

Default behavior: require a scoped, recorded human approval immediately before execution.

### `irreversible`

Examples:

- Submit a transfer
- Close an account

Default behavior for this project: deny. Human takeover does not automatically override the denial.

## 8. Independent risk classification

The runtime does not trust the artifact or model-provided risk label alone. It classifies based on:

- Action type
- Target text and role
- Route category
- Form method and destination when available
- Keywords such as submit, transfer, close, approve, or confirm
- Tenant policy metadata
- Known target-control registration

The effective risk is the maximum of declared and independently inferred risk. A disagreement is recorded as a policy signal.

## 9. Approval semantics

An approval is narrow and non-transferable. It contains:

- Approving principal
- Run, session, capability, and step
- Exact action fingerprint
- Exact target description
- Risk and reason
- Issued and expiry timestamps
- Lease version

Approval is consumed once. Any meaningful change to state, action, target, or lease invalidates it.

## 10. Sensitive-data classification

Supported classifications:

- `public`
- `operational`
- `customer_identifier`
- `personal`
- `financial`
- `credential`
- `secret`

Classification drives display, logging, persistence, and evidence behavior.

| Classification | UI display | Structured log | Screenshot | Artifact |
|---|---|---|---|---|
| Public | Full | Full | Full | Allowed |
| Operational | Full | Controlled | Full | Metadata only |
| Customer identifier | Masked by default | Redacted/tokenized | Redacted region | Schema only |
| Personal | Masked | Removed or tokenized | Redacted region | Forbidden |
| Financial | Role-aware | Redacted by default | Redacted unless evidence requires synthetic value | Output schema only |
| Credential | Never | Never | Region removed | Forbidden |
| Secret | Never | Never | Region removed | Forbidden |

## 11. Redaction pipeline

Redaction occurs before persistence:

```text
raw in-memory event/image
  -> classify fields and regions
  -> redact/tokenize
  -> validate forbidden-pattern scan
  -> serialize
  -> hash
  -> write evidence
```

The system never writes raw evidence and then attempts to clean it later.

### Structured values

- Known sensitive fields use schema annotations.
- Invocation parameters are referenced by name in logs, not value.
- Member identifiers use deterministic run-local tokens or last-four masking.
- Financial values are retained only in explicitly declared synthetic demonstration outputs.
- Headers, cookies, tokens, browser storage, and provider authorization metadata are dropped.

### Screenshots

- Surface adapters identify known sensitive control bounding boxes.
- Configured regions are blurred or covered before writing.
- Full-screen failure evidence is permitted only after redaction.
- Redaction failure prevents persistence and may prevent auditable success.

### Free text

- Messages use structured templates and reason codes instead of dumping exceptions.
- A defense-in-depth scanner checks token-like strings, email addresses, account patterns, and configured synthetic secrets.
- Scanner findings fail the evidence write in strict mode.

## 12. Secrets

- Provider keys and database credentials enter through validated environment variables or a production secret manager adapter.
- Secrets are never accepted from frontend forms.
- `.env` files are ignored; only `.env.example` with placeholders is documented.
- Configuration errors report missing variable names but never values.
- Provider request/response logging strips authorization and request headers.
- Browser storage state is kept in memory for the live session and never committed.

## 13. Model safety

- The model receives only the goal, sanitized inputs, observations, permitted action vocabulary, and policy guidance.
- Target-application text is treated as untrusted content, not developer instruction.
- A page asking the agent to ignore policy has no authority.
- Model actions are schema-validated and policy-evaluated.
- The model cannot navigate to arbitrary external origins.
- The model cannot request shell, network, database, or filesystem tools.
- Hidden reasoning is not requested or persisted.

## 14. Human-control safety

- The operator must own the current lease before input is forwarded.
- Operator actions receive the same route and risk evaluation.
- Sensitive actions require explicit approval and are recorded.
- Irreversible actions remain blocked in the submitted environment.
- Operator identity is attached to every manual input group.
- Disconnect revokes active input capability after the heartbeat grace period.
- Resume invalidates unused approvals.

## 15. Artifact safety validation

Artifacts are rejected when they contain:

- Literal credentials or token-like strings
- Raw customer/member data
- Arbitrary executable expressions
- Unbounded loops or retries
- Navigation outside registered entry points
- Risk above the capability maximum
- Coordinate-only targets without explicit portability metadata
- Tenant overlays that widen policy
- Outputs without classification
- Missing final checkpoint

## 16. Audit events

Required safety events:

- Policy evaluated
- Action allowed
- Action denied
- Human approval requested
- Human approval granted or rejected
- Approval consumed or expired
- Control ownership changed
- Redaction applied
- Evidence rejected by scanner
- Artifact validation rejected
- Route allowlist violation

Events include stable codes and correlations but exclude sensitive values.

## 17. Retention

The demonstration defaults to local evidence retained until explicit cleanup. The production design defines policy-driven classes:

- Operational metadata
- Sanitized screenshots
- Provider interaction metadata
- Human-control audit
- Failure traces

Deletion is out of scope for the first implementation, but evidence manifests must include retention class and creation time so a later lifecycle worker can enforce it.

## 18. Safety test matrix

- Deny unknown origin.
- Deny unknown route.
- Deny disallowed action type.
- Deny typing into a credential field.
- Require approval for sensitive action.
- Reject approval for a different action fingerprint.
- Reject stale lease approval.
- Block irreversible action during automation and human control.
- Redact customer identifiers from events.
- Remove authorization headers.
- Redact configured screenshot regions.
- Reject artifact containing a token-shaped literal.
- Treat prompt-injection text as page content.
- Prove tenant overlay cannot widen base policy.

## 19. Known limits

- Screenshot redaction relies on known regions and pattern detection; real deployments require institution-specific data maps and stricter isolation.
- A local development principal is not enterprise authentication.
- Policy cannot determine the full business meaning of every unknown control; unknown high-impact actions must stop.
- The synthetic target does not reproduce all regulatory or institutional controls.
