# ReplayForge

ReplayForge discovers a workflow through a rendered UI, compiles the verified interaction into a typed YAML capability, and replays later invocations deterministically without a model in the replay decision loop.

The implemented vertical slice looks up a synthetic member's savings balance in a multi-tenant legacy-style banking application. It covers iframe targeting, controlled faults, five-layer policy intersection, immutable versions, typed business outcomes, redaction-first events, and exclusive intervention leases.

## Prerequisites and bootstrap

Requires Python 3.12, `uv`, Node.js 22+, and pnpm 10.15.1. Replay needs no credential; live discovery also requires an OpenAI API key and explicitly selected model.

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

Terminal 3 — deterministic invocation:

```bash
curl --fail-with-body --silent --show-error -H 'content-type: application/json' \
  -d '{"tenant":"harbor","version":"1.0.0","inputs":{"member_id":"12345"}}' \
  http://127.0.0.1:8000/api/v1/capabilities/member.lookup_savings_balance/invoke
```

Expected status is `success`, with five outputs and a verified checkpoint. Use member `99999` for the typed `member_not_found` outcome. Change `harbor` to `summit` to replay the same artifact against the second tenant.

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

Set both values only in the ignored local `.env`:

```dotenv
REPLAYFORGE_OPENAI_API_KEY=<runtime credential>
REPLAYFORGE_OPENAI_MODEL=<structured-output-capable model ID>
```

Restart the API, then submit:

```bash
curl --fail-with-body --silent --show-error -H 'content-type: application/json' \
  -d '{"goal":"Look up the synthetic member and return the current savings balance.","application_family":"northstar_member_service","tenant":"harbor","entry_point":"member_search","inputs":{"member_id":"12345"},"max_steps":20,"timeout_seconds":120}' \
  http://127.0.0.1:8000/api/v1/discoveries
```

Discovery sends ephemeral rendered PNG frames and normalized state. Customer input values are excluded from the model instruction payload. Responses use strict structured output, bounded token/time budgets, no tools, and `store=false`. A successful trace is checkpoint-verified and atomically published as the next immutable patch version.

## Verification

```bash
bash scripts/verify.sh
```

This runs formatting, lint, strict typing, the 90% branch-coverage gate, frontend checks/build, artifact validation, evidence-integrity tests, and real Chromium integrations.

## Repository map

- `backend/src/replayforge/` — domain, services, adapters, and ASGI composition
- `apps/demo-bank/` — two-tenant synthetic target with controlled faults
- `capabilities/` — reviewed immutable YAML versions
- `evidence/` — local redacted event blobs and immutable hash manifests produced at runtime
- `schemas/` — artifact JSON Schema
- `docs/` — architecture, safety, handoff, API, testing, and traceability specifications
- `REPORT.md` — assignment report and deliberate cuts

Start with [the documentation index](docs/README.md) and [requirement traceability](docs/11-requirement-traceability.md).
The [evidence index](evidence/README.md) separates genuine captured scenarios from outstanding evidence.

## Current boundaries

Run evidence is written atomically to the configured local evidence directory; registry, lease, and intervention metadata remain thread-safe and in-memory, with PostgreSQL repository contracts specified but not yet implemented. Same-session preservation, leases, intervention transitions, and lease-guarded viewport polling are implemented; continuous screencasting, input forwarding, and the operator UI remain follow-on work. These limits are explicit so a reviewer cannot mistake a partial control plane for a completed safety control.
