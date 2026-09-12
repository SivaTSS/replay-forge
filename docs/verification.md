# Verification and evidence

## One command

```bash
bash scripts/verify.sh
```

```mermaid
%%{init: {"htmlLabels":false,"themeVariables":{"lineColor":"#6E7781","signalColor":"#6E7781"},"flowchart":{"curve":"linear"},"sequence":{"wrap":true}}}%%
flowchart TB
    V([verify.sh]) --> PY[Python]
    V --> WEB[Web applications]
    V --> SYS[System evidence]

    PY --> D[Locked dependency sync]
    PY --> F[Ruff format + lint]
    PY --> M[Strict mypy]
    PY --> U[Unit tests + 90% branch gate]
    WEB --> T[TypeScript checks]
    WEB --> B[Two Next.js builds]
    SYS --> E[Evidence bundle verification]
    SYS --> I[Real Chromium integration tests]

```

The Playwright adapter is excluded from the Python coverage percentage and tested separately through real Chromium integration tests.

## What is proved where

| Property | Test mechanism | Committed evidence |
|---|---|---|
| Genuine model-guided discovery | Provider, engine, compiler tests; recorded run | `evidence/discovery-success` |
| Replay cannot import a model provider | Structural dependency test | `evidence/replay-success` |
| Typed success and five outputs | Engine + composed integration | `evidence/replay-success` |
| Legitimate negative answer | Outcome precedence tests | `evidence/replay-member-not-found` |
| Finite recovery | Retry/recovery tests + Chromium | `evidence/replay-recovery` |
| Known hard failure | Failure classification + masked screenshot checks | `evidence/replay-hard-failure` |
| Same live browser handoff | Lease/runtime tests + captured frames | `evidence/human-handoff` |
| Shared artifact across tenants | Chromium integration | `evidence/tenant-reuse` |
| Canvas-only model-free replay | Chromium on Harbor and Summit | Automated `3.0.0` integration test |
| Visual fail-closed behavior | OCR cardinality, template movement, asset integrity tests | Unit suite |
| Artifact immutability and integrity | Registry/serialization tests | Artifact hash in every applicable bundle |
| Redaction before retention | Redactor/journal/store tests | Manifest directives and sanitized payloads |

## Evidence bundle anatomy

```text
evidence/<scenario>/
├── manifest.json       command, commit, source manifest, hashes, redaction
├── events.jsonl        ordered sanitized lifecycle events
├── result.json         exactly one typed terminal result
├── artifact.yaml       discovery scenario only
└── screenshots/        selected masked frames where required
```

The runtime first writes mutable evidence under ignored `evidence/runtime/`. Export tooling verifies the source manifest and creates stable reviewer bundles without overwriting an existing destination.

`verify_evidence_bundles.py` checks:

- Manifest schema.
- Every declared file and SHA-256.
- Event order and run identity.
- Exactly one terminal result.
- Artifact hash when present.
- Attachment ownership, size, media type, and PNG/ZIP signatures.
- Source-manifest linkage and declared redaction.

## Scenario matrix

| Directory | Version | Tenant | Terminal state | Specific proof |
|---|---:|---|---|---|
| `discovery-success` | compiled | Harbor | success | A real OpenAI/Luna loop produced an eight-step artifact |
| `replay-success` | `1.0.0` | Harbor | success | Model-free outputs and final checkpoint |
| `replay-member-not-found` | `1.0.0` | Harbor | business outcome | “No member” is not reported as a crash |
| `replay-recovery` | `1.0.1` | Harbor | success | Known notice dismissed once, then resumed |
| `replay-hard-failure` | `1.0.2` | Harbor | failure | Permission denial plus masked failure frame |
| `human-handoff` | `2.0.0` | Harbor | success | Pause, claim, input, fresh-state validation, continuation |
| `tenant-reuse` | `1.0.0` | Summit | success | Same artifact version and hash on a second tenant |

No Playwright trace archive is committed. This is an explicit optional evidence cut; screenshots are the richer failure/handoff signal.

## Testing decisions

| Option | Decision | Why |
|---|---|---|
| Mock-only browser tests | Rejected | Would not prove iframe, locator, navigation, or screenshot behavior |
| Live-model tests on every CI run | Rejected | Non-deterministic, credentialed, and paid |
| Unit fakes + real Chromium + committed live evidence | **Chosen** | Deterministic gates plus auditable proof of the one genuine model run |
| Trust exported evidence files | Rejected | Hash and structure verification makes tampering or omission visible |
| Store raw screenshots | Rejected | Evidence is masked before persistence |

## Secret audit

`.env`, `.secrets/`, local Langfuse data, runtime evidence, dependencies, build output, and the assignment PDF are ignored. Before the current public push, all tracked files and all reachable historical blobs were scanned for provider keys, cloud keys, repository tokens, bearer tokens, JWTs, and private keys. Actual local credential values were also compared against history without exposing them; no match was found.
