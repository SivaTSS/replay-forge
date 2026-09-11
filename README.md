# ReplayForge

ReplayForge discovers a workflow through a rendered UI, compiles the verified interaction into a typed YAML capability, and replays later invocations deterministically without a model in the replay decision loop.

The implemented vertical slice looks up a synthetic member's savings balance in a multi-tenant legacy-style banking application. It covers iframe targeting, controlled faults, five-layer policy intersection, immutable versions, typed business outcomes, redaction-first events, and exclusive intervention leases.

## Prerequisites and bootstrap

Requires Python 3.12, `uv`, Node.js 22+, and pnpm 10.15.1. Replay needs no credential; live discovery requires an OpenAI API key and authenticated local Langfuse monitoring.

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv sync --extra dev
npm_config_cache=/tmp/replayforge-npm-cache npx --yes pnpm@10.15.1 install --frozen-lockfile
PLAYWRIGHT_BROWSERS_PATH=/tmp/replayforge-playwright-browsers UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run playwright install chromium
cp .env.example .env
```

The example environment contains no secret. Leave its commented OpenAI settings disabled for replay-only operation.

## Run the vertical slice

Terminal 1 — synthetic target:

```bash
npm_config_cache=/tmp/replayforge-npm-cache npx --yes pnpm@10.15.1 --filter @replayforge/demo-bank dev --hostname 127.0.0.1 --port 3001
```

Terminal 2 — API:

```bash
PLAYWRIGHT_BROWSERS_PATH=/tmp/replayforge-playwright-browsers UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run uvicorn replayforge.main:app --host 127.0.0.1 --port 8000
```

Optional Terminal 3 — intervention console:

```bash
npm_config_cache=/tmp/replayforge-npm-cache npx --yes pnpm@10.15.1 --filter @replayforge/control-plane dev --hostname 127.0.0.1 --port 3000
```

Terminal 3 or 4 — deterministic invocation:

```bash
curl --fail-with-body --silent --show-error -H 'content-type: application/json' \
  -d '{"tenant":"harbor","version":"1.0.0","inputs":{"member_id":"12345"}}' \
  http://127.0.0.1:8000/api/v1/capabilities/member.lookup_savings_balance/invoke
```

Expected status is `success`, with five outputs and a verified checkpoint. Use member `99999` for the typed `member_not_found` outcome. Change `harbor` to `summit` to replay the same artifact against the second tenant.

Version `1.0.0` is the normal model-free replay. Immutable patch `1.0.1` exercises one bounded declared recovery for the demo bank's known informational interstitial while preserving the same typed contract. Patch `1.0.2` exercises a declared permission-denial failure with masked failure-state evidence. Version `2.0.0` classifies search submission as sensitive so reviewers can exercise claim, same-session control, fresh-state validation, and deterministic resume.

With the API and demo bank running, reproduce the recovery path through the public HTTP contract:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run python scripts/capture_recovery_run.py
```

Reproduce the typed permission failure and its sanitized screenshot:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run python scripts/capture_hard_failure_run.py
```

With the API and demo bank running, reproduce that complete handoff through the public HTTP contract:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run python scripts/capture_handoff_run.py
```

Verify the returned `evidence_manifest`, ordered events, hashes, and exactly-one terminal result independently:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run python scripts/verify_evidence.py \
  --root evidence/runtime 'evidence://<run_id>/<manifest_file>'
```

To publish a stable reviewer bundle, replace the placeholders with the returned key and current 7–40 character commit SHA:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run python scripts/export_evidence.py \
  'evidence://<run_id>/<manifest_file>' evidence/replay-success \
  --root evidence/runtime --scenario replay-success \
  --artifact capabilities/member.lookup_savings_balance/1.0.0.yaml \
  --commit-sha '<commit_sha>' --command '<exact replay command>'
```

## Live model-driven discovery

Create the ignored, owner-readable `.secrets/openai.env`, then replace the placeholder locally:

```bash
install -D -m 600 config/openai.env.example .secrets/openai.env
```

Generate matching ignored client and headless-server credentials without printing their values:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run python scripts/bootstrap_langfuse_credentials.py
```

This preserves existing `.env` settings, writes the matching Langfuse SDK keys to `.env`, writes
the server's headless-initialization and infrastructure secrets to
`.secrets/langfuse-server.env`, and enforces mode `0600` on both. It is idempotent: subsequent
runs reuse the existing project keypair and server secrets. The runtime refuses to start model
discovery unless the keys are paired, the endpoint is loopback, and `auth_check()` succeeds.
The OpenAI key is never printed, committed, or sent to Langfuse.

Start the pinned Langfuse `v4.33.0` Docker Compose deployment:

```bash
scripts/start_local_langfuse.sh
```

The wrapper verifies the exact upstream commit, applies the reviewed loopback-only port
override, waits for `/api/public/ready`, and authenticates the SDK project credentials. Langfuse
is available at `http://127.0.0.1:3100`. PostgreSQL, ClickHouse, Redis, and the worker expose no
host ports; MinIO is loopback-only. Upstream Langfuse telemetry and its in-app agent are disabled.

The reviewed [model policy](config/model-policy.yaml) is the only authority for model choice and
budgets. Environment variables and API requests cannot override the cost-sensitive model, low
reasoning effort, output-token ceiling, per-run call ceiling, or provider timeout.

Restart the API, then capture the genuine run and its hash-verified compiled artifact to a new
file (the command refuses to overwrite an existing review artifact):

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run python scripts/capture_discovery_run.py \
  --artifact-output /tmp/replayforge-genuine-discovery.yaml
```

The committed [`evidence/discovery-success`](evidence/discovery-success/) bundle records a
successful genuine run, its OpenAI/Luna provenance, the compiled artifact, redacted lifecycle
events, terminal result, and independently verifiable hashes.

Discovery sends ephemeral rendered PNG frames and normalized state. Customer input values are excluded from the model instruction payload. Responses use strict structured output, bounded token/time budgets, no tools, and `store=false`. A successful trace is checkpoint-verified and atomically published as the next immutable patch version.

## Verification

```bash
bash scripts/verify.sh
```

This runs formatting, lint, strict typing, the 90% branch-coverage gate, frontend checks/build, artifact validation, evidence-integrity tests, and real Chromium integrations.

## Repository map

- `backend/src/replayforge/` — domain, services, adapters, and ASGI composition
- `apps/demo-bank/` — two-tenant synthetic target with controlled faults
- `apps/control-plane/` — lease-aware same-session intervention console
- `capabilities/` — reviewed immutable YAML versions
- `evidence/` — local redacted event blobs and immutable hash manifests produced at runtime
- `schemas/` — artifact JSON Schema
- `docs/` — architecture, safety, handoff, API, testing, and traceability specifications
- `REPORT.md` — assignment report and deliberate cuts

Start with [the documentation index](docs/README.md) and [requirement traceability](docs/11-requirement-traceability.md).
The [evidence index](evidence/README.md) separates genuine captured scenarios from outstanding evidence.

## Current boundaries

Run evidence is written atomically to the configured local evidence directory; registry, lease, and intervention metadata remain thread-safe and in-memory, with PostgreSQL repository contracts specified but not yet implemented. Same-session preservation, renewable leases, intervention transitions, ordered viewport polling, bounded pointer/text/key forwarding, and fresh-state deterministic replay continuation are implemented through the operator console. Continuous screencasting and discovery-loop continuation remain follow-on work. The HTTP fallback intentionally accepts one command against the latest frame rather than approximating unrestricted co-browsing.
