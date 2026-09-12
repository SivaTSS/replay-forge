# Operations and HTTP contract

## Local topology

```text
http://127.0.0.1:3000  operator console
http://127.0.0.1:3001  synthetic bank
http://127.0.0.1:8000  runtime API and /api/docs
http://127.0.0.1:3100  optional local Langfuse for discovery
```

Use Python 3.12, Node.js 22+, `uv`, and pnpm 10.15.1. Exact setup and demo commands are in the root [README](../README.md).

## Implemented endpoints

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/health/live` | Process is serving requests |
| `GET` | `/health/ready` | Registry and demo target are available |
| `POST` | `/api/v1/discoveries` | Runs discovery synchronously; `202` only if intervention is returned |
| `GET` | `/api/v1/capabilities/schema` | Returns the artifact JSON Schema |
| `POST` | `/api/v1/capabilities/validate` | Parses, validates, and hashes supplied YAML |
| `POST` | `/api/v1/capabilities/{id}/replays` | Runs replay synchronously; `202` only if paused |
| `POST` | `/api/v1/capabilities/{id}/invoke` | Alias of the replay endpoint |
| `GET` | `/api/v1/interventions/{id}` | Reads intervention and current lease |
| `POST` | `/api/v1/interventions/{id}/claim` | Transfers paused ownership to one operator |
| `POST` | `/api/v1/interventions/{id}/release` | Returns human ownership to paused state |
| `POST` | `/api/v1/interventions/{id}/resume` | Revalidates fresh state and continues replay |
| `GET` | `/api/v1/interventions/{id}/viewport` | Returns non-cacheable PNG and sequence headers |
| `POST` | `/api/v1/interventions/{id}/heartbeat` | Renews ownership and increments lease version |
| `POST` | `/api/v1/interventions/{id}/input` | Applies one frame-bound click, text, or key action |
| `POST` | `/api/v1/interventions/{id}/terminate` | Terminates an open or owner-claimed intervention |

There are no implemented capability-list, run-read, event-read, evidence-download, cancellation, WebSocket, authentication, or fault-control endpoints. Evidence is inspected from the filesystem in this local submission.

## Replay request and results

```json
{
  "tenant": "harbor",
  "version": "1.0.0",
  "inputs": {"member_id": "12345"}
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

Pydantic models forbid unknown request fields. Validation errors contain field locations and types, never submitted values. Every HTTP response receives an accepted or generated correlation ID.

## Configuration

| Setting | Default | Purpose |
|---|---|---|
| `REPLAYFORGE_ARTIFACT_DIRECTORY` | `capabilities` | Startup YAML registry |
| `REPLAYFORGE_EVIDENCE_DIRECTORY` | `evidence/runtime` | Mutable local evidence |
| `REPLAYFORGE_DEMO_BASE_URL` | `http://127.0.0.1:3001` | Credential-free target origin |
| `REPLAYFORGE_BROWSER_HEADLESS` | `true` | Chromium mode |
| `REPLAYFORGE_MODEL_POLICY_FILE` | `config/model-policy.yaml` | Reviewed discovery budget |
| `REPLAYFORGE_OPENAI_API_KEY` | unset | Enables live discovery only |
| `REPLAYFORGE_LANGFUSE_BASE_URL` | `http://127.0.0.1:3100` | Must be loopback HTTP |
| `REPLAYFORGE_LANGFUSE_PUBLIC_KEY` / `REPLAYFORGE_LANGFUSE_SECRET_KEY` | unset | Must be configured together |

Settings reject credentials in URLs, non-local Langfuse endpoints, missing artifact directories, partial Langfuse credentials, and OpenAI discovery without Langfuse credentials.

## Operational decisions

| Option | Decision | Why |
|---|---|---|
| Asynchronous queue and polling API | Rejected for this slice | Adds persistence and worker lifecycle without improving the core demonstration |
| Synchronous invocation | **Chosen** | Exact behavior is visible in one request; browser work still stays on its owner thread |
| Generated frontend client | Not implemented | The small console uses local TypeScript shapes; API models remain authoritative |
| Environment-selected model | Rejected | Prevents callers from bypassing reviewed cost and reasoning limits |
| Local evidence path in API | Rejected | Callers receive opaque `evidence://` keys rather than filesystem paths |
| Public target | Rejected | Cannot guarantee availability, fault injection, or acceptable automation terms |

## Failure behavior

| Condition | HTTP/result behavior |
|---|---|
| Invalid request shape | `422 request_validation_failed` |
| Unknown capability/version | `404 capability_not_found` |
| Target unavailable at readiness | `503 runtime_not_ready` |
| Discovery dependencies unavailable | `503 discovery_not_ready` |
| Stale lease, state, or owner | `409 intervention_transition_conflict` |
| Stale frame/client input | `409 human_input_conflict` |
| Valid replay failure | HTTP `200` with typed `failure` result |
| Automation paused | HTTP `202` with `intervention_required` |

The distinction is deliberate: HTTP describes whether the runtime processed the request; the discriminated result describes the business/automation outcome.
