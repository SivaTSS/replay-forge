# Operations and HTTP contract

[Documentation index](README.md)

## Local topology

```text
http://127.0.0.1:3000  run launcher and live viewer; /interventions for operator inbox
http://127.0.0.1:3001  synthetic bank
http://127.0.0.1:8000  runtime API and /api/docs
http://127.0.0.1:3100  optional local Langfuse for discovery
```

Use Python 3.12, Node.js 22+, `uv`, and pnpm 10.15.1. Exact setup and demo commands are in the root [README](../README.md).

## Implemented endpoints

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/health/live` | Process is serving requests |
| `GET` | `/health/ready` | Capability registry, application catalog, and every registered target are available |
| `POST` | `/api/v1/discoveries` | Runs unattended discovery synchronously; returns success or failure |
| `POST` | `/api/v1/discovery-suites` | Creates a draft suite and runs its primary discovery trace |
| `GET` | `/api/v1/discovery-suites/{id}` | Reads sanitized suite status and coverage |
| `POST` | `/api/v1/discovery-suites/{id}/scenarios` | Adds observed outcome, failure, or recovery evidence |
| `POST` | `/api/v1/discovery-suites/{id}/validations` | Runs deterministic compatibility validation |
| `POST` | `/api/v1/discovery-suites/{id}/finalize` | Compiles and applies the publication risk gate |
| `GET` | `/api/v1/capabilities/schema` | Returns the artifact JSON Schema |
| `POST` | `/api/v1/capabilities/validate` | Parses, validates, and hashes supplied YAML |
| `POST` | `/api/v1/capabilities/{id}/replays` | Runs replay synchronously; `202` only if paused |
| `POST` | `/api/v1/capabilities/{id}/invoke` | Alias of the replay endpoint |
| `GET` | `/api/v1/interventions?run_mode=replay` | Lists active replay interventions oldest first |
| `GET` | `/api/v1/interventions/{id}` | Reads intervention and current lease |
| `POST` | `/api/v1/interventions/{id}/claim` | Transfers paused ownership to one operator |
| `POST` | `/api/v1/interventions/{id}/release` | Returns human ownership to paused state |
| `POST` | `/api/v1/interventions/{id}/resume` | Revalidates fresh state and continues replay |
| `GET` | `/api/v1/interventions/{id}/viewport` | Returns non-cacheable PNG and sequence headers |
| `POST` | `/api/v1/interventions/{id}/heartbeat` | Renews ownership and increments lease version |
| `POST` | `/api/v1/interventions/{id}/input` | Applies one frame-bound click, text, or key action |
| `POST` | `/api/v1/interventions/{id}/terminate` | Terminates an open or owner-claimed intervention |
| `GET` | `/api/v1/executions/catalog` | Capability descriptions and typed input/output contracts, registered targets, explicit synthetic presets |
| `POST` | `/api/v1/executions` | Starts managed replay or discovery; returns `202`, execution ID and viewer token |
| `GET` | `/api/v1/executions/{id}?after=N` | Authorized state, frame metadata, incremental sanitized timeline, final result |
| `GET` | `/api/v1/executions/{id}/frames/{sequence}` | Authorized transient PNG; `410` when a screen has expired |

Execution status and frame reads require `X-Viewer-Token`; credentials never belong in URLs.
There is no durable run-history browser, evidence-download API, active-run cancellation endpoint,
WebSocket, production authentication, or fault-control API. See [live viewing](live-viewing.md)
for launch semantics, replay history, limits, and privacy. Evidence remains inspectable on disk.

## Replay request and results

```json
{
  "tenant": "harbor",
  "version": "1.0.1",
  "inputs": {"member_id": "12345", "payoff_date": "2026-09-20"}
}
```

```text
success
  run_id + capability version + outputs + verified checkpoint + evidence manifest

business_outcome
  run_id + stable code + redacted details + evidence manifest

failure
  run_id + stable code + safe message + recoverable + optional step/expected/observed

intervention_required
  run_id + intervention/session context + reason + paused owner
```

Pydantic models forbid unknown request fields. Validation errors expose at most 20 error types and
their transport boundary (`body`, `query`, `path`, or `header`), never submitted values or property
names. Detailed Pydantic locations were rejected because arbitrary mapping keys may themselves
contain sensitive data. Every HTTP response receives an accepted or generated correlation ID.

## Configuration

| Setting | Default | Purpose |
|---|---|---|
| `REPLAYFORGE_ARTIFACT_DIRECTORY` | `capabilities` | Startup YAML registry |
| `REPLAYFORGE_APPLICATION_REGISTRY_FILE` | `config/applications.yaml` | Reviewed application onboarding catalog |
| `REPLAYFORGE_CAPABILITY_ASSET_DIRECTORY` | `capabilities/_assets` | Content-addressed visual templates |
| `REPLAYFORGE_EVIDENCE_DIRECTORY` | `evidence/runtime` | Mutable local evidence |
| `REPLAYFORGE_DEMO_BASE_URL` | `http://127.0.0.1:3001` | Credential-free target origin |
| `REPLAYFORGE_BROWSER_HEADLESS` | `true` | Chromium mode |
| `REPLAYFORGE_VIEWER_PRESETS_FILE` | `config/servicing-discovery.yaml` | Explicit synthetic launch defaults; missing file or `None` in runtime composition means no presets |
| `REPLAYFORGE_BROWSER_VIEWPORT_WIDTH` / `REPLAYFORGE_BROWSER_VIEWPORT_HEIGHT` | `1280` / `800` | CSS viewport used by the browser surface |
| `REPLAYFORGE_BROWSER_DEVICE_SCALE_FACTOR` | `1.0` | Chromium DPR; screenshots and pointer regions remain CSS-pixel based |
| `REPLAYFORGE_MODEL_POLICY_FILE` | `config/model-policy.yaml` | Reviewed discovery budget |
| `REPLAYFORGE_VISION_POLICY_FILE` | `config/vision-policy.yaml` | Reviewed OCR, segmentation, similarity, pixel, and time budgets |
| `REPLAYFORGE_OPENAI_API_KEY` | unset | Enables live discovery only |
| `REPLAYFORGE_LANGFUSE_BASE_URL` | `http://127.0.0.1:3100` | Must be loopback HTTP |
| `REPLAYFORGE_LANGFUSE_PUBLIC_KEY` / `REPLAYFORGE_LANGFUSE_SECRET_KEY` | unset | Must be configured together |

Settings reject credentials in URLs, non-local Langfuse endpoints, missing artifact or application-registry files, partial Langfuse credentials, and OpenAI discovery without Langfuse credentials.

## Operational decisions

| Option | Decision | Reason |
|---|---|---|
| Bounded asynchronous launch with polling | **Chosen for visual runs** | Return an identity immediately and observe execution without keeping the launch request open; no database required |
| Synchronous invocation | Retained for API callers | Uses the same engines and owner-thread browser operations |
| Generated frontend client | Not implemented | The small console uses local TypeScript shapes; API models remain authoritative |
| Environment-selected model | Rejected | Prevents callers from bypassing reviewed cost and reasoning limits |
| Local evidence path in API | Rejected | Callers receive opaque `evidence://` keys rather than filesystem paths |
| Public target | Rejected | Cannot guarantee availability, fault injection, or acceptable automation terms |

## Discovery publication and capture

The [demo workstation](demo-bank.md) has one entry point, `legacy_servicing`. All three
business capabilities use it. Restart the runtime after changing registration.

Run `uv run python scripts/capture_demo_workflows.py --spec config/servicing-discovery.yaml --timeout-seconds 600`.
Use `--workflow temporary_card_lock` (or another key in that file) to select one goal.
The runner checks the draft's ID, risk, and requested input/output contract before validation
or publication, then validates tenant reuse and finalizes automatically. No post-discovery reviewer exists.

The runtime publishes each new capability version to its configured registry. The capture command
exports a separate owner-only copy under a fresh `.local/discovery-captures/capture-*` directory;
its summary includes the actual version and path. Re-running discovery never overwrites committed
fixtures. `--output-directory` changes the private export root.

## Failure behavior

| Condition | HTTP/result behavior |
|---|---|
| Invalid request shape | `422 request_validation_failed` |
| Unknown capability/version | `404 capability_not_found` |
| Target unavailable at readiness | `503 runtime_not_ready` |
| Discovery dependencies unavailable | `503 discovery_not_ready` |
| Stale lease version or state | `409 intervention_transition_conflict` |
| Wrong human owner | `403 intervention_forbidden` |
| Expired human lease | `409 control_lease_expired` |
| Stale frame/client input | `409 human_input_conflict` |
| Valid replay failure | HTTP `200` with typed `failure` result |
| Automation paused | HTTP `202` with `intervention_required` |

The distinction is deliberate: HTTP describes whether the runtime processed the request; the discriminated result describes the business/automation outcome.
