# Capability Artifact Specification

## 1. Purpose

A capability artifact is the stable contract between discovery and production execution. It is not a transcript, macro, trace, or serialized provider response. It describes what a capability does, what it accepts and returns, how its controls are located, how success is proven, and which exceptional states are meaningful.

The artifact must be:

- Typed and schema-valid.
- Serializable and human-reviewable.
- Immutable once published.
- Parameterized rather than bound to one discovery run.
- Independent of a specific model provider.
- Explicit about safety, outcomes, recovery, and checkpoints.
- Usable by a deterministic executor without interpretation.

## 2. Representation

- Canonical authoring and review format: YAML.
- Runtime representation: validated Pydantic models.
- Machine contract: generated JSON Schema committed with the artifact schema version.
- Integrity: SHA-256 of canonical JSON serialization, excluding storage metadata.
- Text encoding: UTF-8.
- Decimal values: strings with schema constraints, never binary floating point.
- Timestamps: RFC 3339 UTC unless they are extracted source values with an explicit timezone rule.

YAML is chosen for reviewability. YAML is never trusted directly: it is parsed using safe loading, normalized, validated, and converted to a typed object before use.

## 3. Top-level contract

```yaml
schema_version: "1.0"
capability:
  id: "member.lookup_savings_balance"
  version: "1.0.0"
  name: "Lookup savings balance"
  description: "Find a member and return the current available balance of the savings account."
  application_family: "northstar_member_service"
  surface: "web"
  risk: "read_only"
  tags: ["member-service", "balance", "read-only"]
compatibility: {}
inputs: {}
outputs: {}
preconditions: []
steps: []
outcomes: []
failures: []
checkpoint: {}
policy: {}
provenance: {}
```

## 4. Identity and versioning

### `schema_version`

Version of the artifact language, independent of capability behavior.

- Major change: incompatible structural or semantic change.
- Minor change: backward-compatible optional field or new discriminated variant supported by the runtime.
- The runtime rejects unsupported major versions before opening a session.

### `capability.id`

A stable dotted identifier describing business intent, not implementation. It is lowercase, immutable, and unique within the registry.

### `capability.version`

Semantic version of the capability contract:

- Major: incompatible input, output, outcome, or safety behavior.
- Minor: backward-compatible behavior or supported-tenant addition.
- Patch: locator, wait, evidence, or recovery correction that preserves the external contract.

### Immutability

Published artifact content cannot be updated in place. A content change creates a new version. Registry metadata such as display labels may change separately, but never alters execution semantics.

## 5. Compatibility block

The compatibility block declares where the artifact may execute.

```yaml
compatibility:
  application_family: "northstar_member_service"
  base_variant: "standard"
  supported_variants:
    - "harbor_credit_union"
    - "summit_community_bank"
  surface_contract: "web.v1"
  entry_point: "member_search"
  fingerprint:
    required_landmarks:
      - kind: "heading"
        value: "Member Search"
      - kind: "field"
        value: "Member ID"
    forbidden_landmarks:
      - kind: "text"
        value: "System maintenance"
```

The effective configuration is resolved as:

```text
application-family defaults
  + capability compatibility constraints
  + validated tenant overlay
  = immutable execution configuration
```

Tenant overlays may adjust entry URLs, timeouts, aliases, frame paths, and locator candidates. They may not widen policy, alter inputs/outputs, remove checkpoints, or introduce actions.

## 6. Input contract

Inputs use a deliberately limited JSON-Schema-compatible type system:

- string
- integer
- boolean
- decimal string
- date
- timestamp
- enum
- object composed of allowed types

Every input declares:

- Type
- Required status
- Description
- Validation constraints
- Sensitivity classification
- Example using synthetic data

Example:

```yaml
inputs:
  type: object
  additional_properties: false
  required: [member_id]
  properties:
    member_id:
      type: string
      description: "Synthetic institution member identifier."
      pattern: "^[0-9]{5,10}$"
      min_length: 5
      max_length: 10
      data_classification: "customer_identifier"
      persistence: "redacted"
      example: "12345"
```

Raw invocation values are not written into the artifact. Steps reference inputs symbolically:

```yaml
value:
  source: "input"
  path: "member_id"
```

## 7. Output contract

Outputs declare the caller-visible success payload. Each field states its source, type, validation, and sensitivity behavior.

```yaml
outputs:
  type: object
  additional_properties: false
  required: [account_type, currency, available_balance, as_of]
  properties:
    account_type:
      type: string
      const: "savings"
    currency:
      type: string
      enum: ["USD"]
    available_balance:
      type: string
      format: "decimal"
      pattern: "^-?[0-9]+\.[0-9]{2}$"
      data_classification: "financial"
    as_of:
      type: string
      format: "date-time"
```

Outputs are bound only after their extraction source and final checkpoint succeed. Partial extraction never produces a successful result.

## 8. Step model

Every step contains:

```yaml
- id: "search.submit"
  name: "Submit member search"
  action: {}
  target: {}
  preconditions: []
  postconditions: []
  timeout_ms: 10000
  retry: {}
  recovery_refs: []
  outcome_refs: []
  failure_refs: []
  risk: "read_only"
  evidence: {}
```

### Required step properties

- `id`: stable within the capability version.
- `name`: concise reviewer-facing description.
- `action`: one discriminated action variant.
- `target`: locator bundle when the action requires a control.
- `preconditions`: facts that must hold before acting.
- `postconditions`: observable effects proving the action completed.
- `timeout_ms`: total step budget including locator resolution.
- `retry`: bounded policy.
- `risk`: action classification used by policy.
- `evidence`: capture requirements.

### Action variants

- `navigate`: navigate to an artifact-declared symbolic entry point, not an arbitrary URL.
- `click`: activate one resolved target.
- `type`: focus, optionally clear, and enter a literal or parameter reference.
- `press_keys`: send a declared key chord.
- `select`: choose an option by stable value or visible label.
- `scroll`: bounded directional scroll within the window or resolved container.
- `wait_for`: wait for an observable condition.
- `extract`: read and transform visible data into a typed output binding.
- `assert`: verify an observable condition without mutating the surface.
- `switch_context`: enter a declared frame, window, or desktop scope.
- `checkpoint`: validate a named intermediate state.

Discovery-only intents such as `complete` and `escalate` are not replay actions.

## 9. Locator bundle

```yaml
target:
  description: "Search button in the member-search form"
  scope:
    window: "primary"
    frame_path:
      - locator:
          strategy: "title"
          value: "Member operations"
  candidates:
    - strategy: "role_name"
      role: "button"
      name: "Search"
      match: "exact"
      expected_count: 1
    - strategy: "relative_text"
      anchor: "Member ID"
      relation: "form_submit"
      element: "button"
      text: "Search"
      expected_count: 1
    - strategy: "css"
      value: "form.member-query button[type='submit']"
      expected_count: 1
  state:
    visible: true
    enabled: true
  tenant_overrides_allowed: true
```

Allowed strategies:

- `role_name`
- `label`
- `text`
- `relative_text`
- `placeholder`
- `title`
- `css`
- `accessibility_path`
- `image_anchor`
- `coordinates`

Rules:

- Candidates are tried in declared order.
- A candidate succeeds only when count and state expectations hold.
- Ambiguity is never resolved by selecting the first element.
- Dynamic database identifiers and discovery-only generated IDs are rejected.
- CSS selectors must be constrained and reviewed for volatility.
- Coordinate locators require explicit surface dimensions and a low portability classification.
- Tenant overrides can add or replace candidates only within their declared target.

## 10. Conditions

Conditions are typed observations, not arbitrary executable code.

Supported conditions:

- URL matches an allowlisted route pattern.
- Element exists or does not exist.
- Element state is visible, hidden, enabled, disabled, checked, or selected.
- Visible text matches exact, contains, or safe regular expression.
- Dialog matches a declared signature.
- Extracted value passes its type/format constraint.
- Surface fingerprint contains required landmarks.
- Prior output binding equals an input or constant.

Conditions support `all`, `any`, and `not` composition with bounded nesting. Arbitrary JavaScript or Python expressions are forbidden.

## 11. Retry and recovery

Retry is permitted only for actions whose effect is known to be idempotent or proven absent.

```yaml
retry:
  max_attempts: 2
  backoff_ms: [500, 1500]
  retry_on:
    - "target_temporarily_absent"
    - "navigation_timeout"
  require_effect_absent: true
```

Recovery is a named, bounded sequence triggered by a specifically detectable condition:

```yaml
recoveries:
  session_expired:
    trigger:
      element_text: "Your session has expired"
    max_uses: 1
    steps:
      - action: "navigate"
        entry_point: "member_search"
    resume_at: "search.enter_member_id"
```

Recovery rules may not call a model, widen policy, mutate capability inputs, or loop without a hard maximum.

## 12. Business outcomes and declared application failures

Business outcomes are legitimate answers produced by the target application.

```yaml
outcomes:
  - code: "member_not_found"
    description: "The application completed the search and found no member."
    detect:
      all:
        - text: "No member found"
        - route: "/members/search"
    allowed_after_steps: ["search.submit"]
    result:
      status: "business_outcome"
      details:
        member_id:
          source: "input"
          path: "member_id"
          redaction: "last4"
```

Outcome detection is evaluated before treating a missing happy-path target as a failure. Outcomes cannot be inferred solely from timeout or absence.

Known application error states remain failures, but the artifact declares how to identify and
classify them so replay does not collapse them into a generic timeout or postcondition mismatch:

```yaml
failures:
  - code: permission_denied
    description: The current role cannot view the requested member.
    detect:
      kind: text
      value: Permission denied
      match: exact
    allowed_after_steps: [search.submit]
    expected_state: member_results
    observed_state: permission_denied
    recoverable: false
```

A step must explicitly reference the failure code, and the declaration must explicitly allow
that step. Expected and observed state labels are static, reviewable metadata rather than raw
screen content, so the typed failure remains debuggable without persisting customer data.

## 13. Checkpoint

The final checkpoint proves the goal, output provenance, and identity relationship.

```yaml
checkpoint:
  id: "savings_balance_verified"
  all:
    - route: "/accounts/*/details"
    - text: "Savings"
    - output_valid: "available_balance"
    - output_valid: "as_of"
    - extracted_member_matches_input: "member_id"
```

Success is impossible without checkpoint satisfaction. Completing all steps is insufficient.

## 14. Policy declaration

The artifact narrows global and tenant policy; it never widens it.

```yaml
policy:
  allowed_action_types: ["navigate", "click", "type", "wait_for", "extract", "assert"]
  allowed_entry_points: ["member_search"]
  maximum_risk: "read_only"
  forbidden_text_inputs: ["password", "security answer"]
  output_redaction:
    member_id: "last4"
```

Effective permission is the intersection of platform, application-family, tenant, capability, and invocation policy.

## 15. Provenance

Provenance supports review without coupling execution to a transcript:

- Discovery run ID
- Provider and model identifier
- Prompt policy version
- Surface adapter version
- Compiler version
- Creation timestamp
- Synthetic target fingerprint
- Evidence manifest key
- Artifact content hash

Provider reasoning, raw prompts containing values, cookies, credentials, and screenshots remain outside the artifact.

## 16. Static validation

Before publication, validation must prove:

- Schema and semantic versions are supported.
- IDs are unique and references resolve.
- Every input reference exists.
- Every declared output is bound before the checkpoint.
- Every target has at least one permitted locator.
- Every retry is bounded.
- Recovery graphs cannot cycle beyond declared maximums.
- All navigation uses registered entry points or allowlisted patterns.
- Step risk does not exceed capability policy.
- Business outcomes have observable positive evidence.
- The checkpoint validates all required outputs.
- No forbidden keys or likely secrets appear.
- Tenant overlays cannot modify protected fields.

## 17. Review checklist

- Can a reviewer explain the business operation from the artifact alone?
- Are the input and output contracts precise?
- Are values parameterized rather than recorded?
- Do locators prefer stable human-visible semantics?
- Does every mutating action have effect verification?
- Are business outcomes distinct from failures?
- Are recovery sequences bounded and safe?
- Does the checkpoint prove the requested goal?
- Does policy narrow execution appropriately?
- Is provenance sufficient without exposing sensitive data?
