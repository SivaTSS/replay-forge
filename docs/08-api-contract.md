# API Contract

## Conventions

- Base path: `/api/v1`; JSON fields use `snake_case`.
- IDs are opaque prefixed strings; timestamps are RFC 3339 UTC; decimals are strings.
- OpenAPI generates frontend types and clients.
- Mutations support idempotency where duplicate submission is plausible.
- Errors expose stable codes, safe messages, structured details, correlation ID, and retryability—never stack traces, secrets, cookies, or raw provider output.

```json
{"error":{"code":"artifact_incompatible_with_tenant","message":"Capability 1.0.0 is not compatible with this tenant.","details":{},"correlation_id":"trc_...","retryable":false}}
```

## Discoveries

### `POST /api/v1/discoveries`

Accepts application family, tenant, symbolic entry point, goal, invocation inputs, declared input/output schemas, model selection, step/time limits, browser mode, and evidence level. Returns `202` with run/session summaries after synchronous validation and durable run creation.

`Idempotency-Key` returns the existing run when normalized request hashes match and conflicts when they differ.

### `POST /api/v1/runs/{run_id}/cancel`

Requests safe cancellation and returns current state. It does not falsely promise immediate browser termination.

## Capabilities

### `GET /api/v1/capabilities`

Cursor-paginated list filterable by application family, tenant compatibility, risk, status, tag, and search text. Items include identity, current version, contract summary, compatibility, and last replay.

### `GET /api/v1/capabilities/{capability_id}`

Returns metadata and immutable version summaries.

### `GET /api/v1/capabilities/{capability_id}/versions/{version}`

Returns validated artifact representation, schema version, content hash, compatibility, provenance, and evidence-manifest reference.

### `GET /api/v1/capabilities/{capability_id}/versions/{version}/artifact`

Downloads canonical YAML whose bytes match the stored content hash.

### `GET /api/v1/capabilities/{capability_id}/schema`

Returns machine-readable input, output, and business-outcome schemas for agent discovery.

## Replay

### `POST /api/v1/capabilities/{capability_id}/replays`

Accepts immutable version, tenant, typed inputs, limits, and evidence level. Returns `202`. Model-related fields are rejected.

```json
{"version":"1.0.0","tenant_id":"harbor_credit_union","inputs":{"member_id":"12345"},"limits":{"timeout_seconds":60},"evidence_level":"standard"}
```

## Agent-facing invocation

### `POST /api/v1/capabilities/{capability_id}/invoke`

Accepts tenant, compatible version constraint, arguments, wait preference, and timeout. With `wait=false`, returns `202`. With `wait=true`, returns a terminal result within budget or `202` plus status URL.

Success:

```json
{"status":"success","run_id":"run_...","capability":{"id":"member.lookup_savings_balance","version":"1.0.0"},"outputs":{"account_type":"savings","currency":"USD","available_balance":"1420.57","as_of":"2026-09-08T15:04:05Z"},"evidence_manifest_id":"evd_..."}
```

Business outcome:

```json
{"status":"business_outcome","run_id":"run_...","code":"member_not_found","message":"The application completed the search and found no member.","details":{"member_id":"***45"}}
```

Failure includes category, stable code, failed step/attempt, expected and sanitized observed state, recovery history, retryability, evidence, and correlation ID.

Intervention result includes intervention/session IDs, trigger, current step, risk, evidence, status URL, and expiry.

## Runs and evidence

- `GET /api/v1/runs/{run_id}` returns immutable identity, lifecycle, current step, terminal result, and links.
- `GET /api/v1/runs/{run_id}/events` returns cursor-paginated ordered sanitized events.
- `GET /api/v1/runs/{run_id}/evidence` returns the manifest, completeness, redaction status, hashes, and authorized download links.
- `GET /api/v1/evidence/{evidence_id}` streams one sanitized object without exposing storage paths.

## Interventions

- `GET /api/v1/interventions` filters by status, tenant, risk, owner, and age.
- `GET /api/v1/interventions/{id}` returns context, state, lease summary, and allowed transitions.
- `POST /api/v1/interventions/{id}/claim` requires expected lease version and returns a new version.
- `POST /api/v1/interventions/{id}/release` returns control to paused automation.
- `POST /api/v1/interventions/{id}/resume` starts deterministic revalidation; it does not immediately declare resumption.
- `POST /api/v1/interventions/{id}/complete` requires a verifiable manual-completion contract.
- `POST /api/v1/interventions/{id}/terminate` safely closes the run after final evidence.

All ownership mutations return `409` on stale lease versions.

## Applications and tenants

- `GET /api/v1/applications` and `GET /api/v1/applications/{family}` expose safe configuration summaries.
- `GET /api/v1/applications/{family}/tenants/{tenant}` exposes effective aliases, compatibility, fingerprint, and drift without secrets.
- Fault-injection mutation endpoints exist only in explicit demo mode and are absent/disabled otherwise.

## Live session WebSocket

### `WS /api/v1/sessions/{session_id}/stream`

Server messages: hello, frame, run event, ownership transition, intervention update, warning, heartbeat, and terminal close.

Client messages: frame acknowledgement, pointer, keyboard, text input, focus, and heartbeat.

Every human-input message contains intervention ID, expected lease version, client sequence, source frame sequence, and viewport dimensions. The server rejects missing ownership, stale frames, stale sequences, route violations, and disconnected interventions.

Frames may be dropped under backpressure. Control, ownership, audit, and terminal events may not be dropped.

## HTTP semantics

- `200`: successful read or synchronous terminal response.
- `201`: created configuration resource when applicable.
- `202`: accepted asynchronous run/transition.
- `400`: malformed request.
- `401/403`: absent identity or policy denial.
- `404`: unknown resource without leaking inaccessible existence.
- `409`: illegal state transition, stale lease, or idempotency conflict.
- `422`: typed validation failure.
- `429`: bounded capacity/rate limit.
- `503`: required dependency not ready.

## Contract acceptance criteria

- Generated frontend types compile without manual patches.
- Every result is a discriminated union.
- Replay requests cannot carry model configuration.
- Stale lease commands reliably conflict.
- Pagination order is stable.
- Error bodies contain no sensitive fields.
- OpenAPI examples pass schema validation.
