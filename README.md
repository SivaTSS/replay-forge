# ReplayForge

ReplayForge discovers a task through a rendered UI, compiles the verified run into a typed YAML capability, and replays that capability without a model in the decision loop.

```mermaid
%%{init: {"htmlLabels":false,"themeVariables":{"lineColor":"#6E7781","signalColor":"#6E7781"},"flowchart":{"curve":"linear"},"sequence":{"wrap":true}}}%%
flowchart LR
    G([Goal]) --> D[Guided discovery]
    D --> A[(Versioned YAML capability)]
    A --> R[Deterministic replay]
    R --> X([Typed result + redacted evidence])
    R -. sensitive or stuck .-> H([Same-session human handoff])
```

The demo's front door is a [dated servicing workstation](docs/demo-bank.md) with member and account
inquiry, transaction research, internal transfers, card maintenance, holds, payoff quotes, service
cases, and a journal. Open `http://127.0.0.1:3001/harbor/servicing` after starting the target. This
expanded application has its own business-rule and UI tests; new model discovery against it is
still pending. Existing evidence and capability versions remain tied to the earlier fixture routes.

The earlier canvas-only member workbench supports three non-trivial servicing tasks: investigate an exact
transaction, calculate a dated loan payoff quote, and temporarily lock a selected card. Discovery
creates a separate typed capability for each goal; the runtime and compiler contain no task names,
route constants, output names, or required action order. These artifacts store semantic target identity,
never coordinates or relative regions, and are resolved again from each current frame. The earlier
savings-balance versions remain immutable regression and handoff fixtures.

## What is real

| Path | Model? | Surface | Result |
|---|---:|---|---|
| Discovery | Yes | Screenshot + local OCR tokens; compact DOM facts when available | Publishes an immutable artifact |
| Replay | **No** | Rendered pixels first; optional semantic DOM candidates second | Success, business outcome, failure, or intervention |
| Handoff | No | The same retained Chromium context | Operator input followed by deterministic replay continuation |

The canonical flow never queries a DOM control: the target exposes one canvas, and all typing, clicking, extraction, and verification are grounded from rendered pixels. Playwright supplies the browser, CSS-pixel screenshot, mouse, and keyboard—not element targeting. See [Architecture](docs/architecture.md#surface-reality).

## Run the core replay

Requires Python 3.12, `uv`, Node.js 22+, and pnpm 10.15.1.

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv sync --extra dev
npm_config_cache=/tmp/replayforge-npm-cache npx --yes pnpm@10.15.1 install --frozen-lockfile
PLAYWRIGHT_BROWSERS_PATH=/tmp/replayforge-playwright-browsers UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run playwright install chromium
cp .env.example .env
```

OCR inference runs locally. A fresh RapidOCR installation may download model weights at first
use; provision those files before running in a network-isolated environment.

Start the target:

```bash
npm_config_cache=/tmp/replayforge-npm-cache npx --yes pnpm@10.15.1 --filter @replayforge/demo-bank dev --hostname 127.0.0.1 --port 3001
```

Start the runtime in another terminal:

```bash
PLAYWRIGHT_BROWSERS_PATH=/tmp/replayforge-playwright-browsers UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run uvicorn replayforge.main:app --host 127.0.0.1 --port 8000
```

Invoke the visual-first artifact:

```bash
curl --fail-with-body --silent --show-error \
  -H 'content-type: application/json' \
  -d '{"tenant":"harbor","version":"3.2.0","inputs":{"member_id":"12345"}}' \
  http://127.0.0.1:8000/api/v1/capabilities/member.lookup_savings_balance/invoke
```

Expected: `status: success`, five validated outputs, and checkpoint `savings_balance_verified`.

## Exercise each runtime result

| Behavior | How to run | Expected |
|---|---|---|
| Canvas-only visual workbench | `3.2.0`, member `12345` | `success` without DOM targets |
| Reflow + DPR portability | `3.2.0`, six CSS viewports, DPR `1–2` | Same artifact succeeds across compact cards and wide table layouts |
| Cross-tenant visual workbench | `3.2.0`, tenant `summit` | Same artifact resolves the reordered Savings row |
| Delayed result | `3.2.0`, member `13579` | Bounded wait, then `success` |
| Known notice | `3.2.0`, member `67890` | One bounded recovery, then `success` |
| Visual ambiguity | `3.2.0`, member `33333` | `failure/target_ambiguous` before a click |
| Changed visual target | `3.2.0`, member `44444` | `failure/target_absent` |
| Duplicate field label | `3.2.0`, member `55555` | `failure/target_ambiguous` at the affected extraction |
| Happy path | `1.0.0`, member `12345` | `success` |
| Business outcome | `1.0.0`, member `99999` | `business_outcome/member_not_found` |
| Second tenant | `1.0.0`, tenant `summit` | Same artifact succeeds |
| Known recovery | `UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run python scripts/capture_recovery_run.py` | Interstitial dismissed once, then `success` |
| Hard failure | `UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run python scripts/capture_hard_failure_run.py` | `failure/permission_denied` + masked frame |
| Human handoff | `UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run python scripts/capture_handoff_run.py` | Claim, same-session input, resume, `success` |

All versions in this table refer to `member.lookup_savings_balance`. Omitting `version` selects
the latest publication for that capability, currently `3.2.0` in the committed registry.
Request `2.0.0` for handoff, `3.0.0` for the original visual-terminal fixture, or `3.1.0`
for its prior repeated-row fixture.

## Operator console

Start it after the target and runtime:

```bash
npm_config_cache=/tmp/replayforge-npm-cache npx --yes pnpm@10.15.1 --filter @replayforge/control-plane dev --hostname 127.0.0.1 --port 3000
```

Open `http://127.0.0.1:3000` and invoke savings-balance version `2.0.0` using the replay command
above. The paused replay appears in the active-intervention inbox with its capability, tenant,
interrupted step, route, and pause reason. Select it, claim the retained browser, provide the
manual input, and choose **Resume automation**. Direct ID lookup remains available for debugging.

The console polls because this slice needs a minimal real handoff, not continuous co-browsing. Every transition uses an exclusive, expiring, monotonically versioned lease. Heartbeats preserve active ownership; an abandoned expired claim can be reclaimed without allowing an active lease to be stolen. Operator IDs are local caller-supplied labels, not authentication.

## Run genuine discovery

Discovery requires an OpenAI key and authenticated local Langfuse. Replay does not.

Create the ignored key file and edit its placeholder locally:

```bash
install -D -m 600 config/openai.env.example .secrets/openai.env
```

Create or reuse ignored Langfuse credentials, then start the pinned loopback-only stack:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run python scripts/bootstrap_langfuse_credentials.py
scripts/start_local_langfuse.sh
```

Restart the runtime so it loads the credentials, keep the demo bank running, then discover,
cross-tenant validate, finalize, and write all configured workflow artifacts:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run python scripts/capture_demo_workflows.py
```

Then invoke the latest published card-lock capability without a model call:

```bash
curl --fail-with-body --silent --show-error \
  -H 'content-type: application/json' \
  -d '{"tenant":"harbor","inputs":{"member_id":"12345","card_last4":"0110"}}' \
  http://127.0.0.1:8000/api/v1/capabilities/member.temporary_card_lock/invoke
```

The script exports into a fresh private directory under ignored `.local/discovery-captures` and
reports the actual published versions. Supply that `version` in the invocation body
to pin a run, or omit it to resolve the latest publication. The committed discoveries can also be
replayed with these commands without configuring model credentials or starting Langfuse.

The reviewed [model policy](config/model-policy.yaml) fixes provider, model, reasoning effort, token/call limits, timeout, frame size, and cost ceiling. Requests cannot override it. Provider calls use strict structured output, no tools, and `store=false`.

## Verify everything

```bash
bash scripts/verify.sh
```

This runs documentation checks, locked dependency setup, Ruff, strict mypy, a 90% branch-aware domain-coverage gate, evidence verification, TypeScript checks, both frontend builds, and real Chromium integration tests.

Run the focused visual portability matrix after starting or building the demo bank:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache \
PLAYWRIGHT_BROWSERS_PATH=/tmp/replayforge-playwright-browsers \
uv run pytest backend/tests/integration/test_visual_portability.py -q
```

Verify only the committed evidence:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run python scripts/verify_evidence_bundles.py evidence
```

## Repository map

```text
backend/src/replayforge/
├── api/             FastAPI contracts and adapter
├── capabilities/    artifact model, serialization, immutable registry
├── applications/    validated application onboarding and surface launch policy
├── discovery/       contract planning, bounded model loop, and generic compiler
├── replay/          deterministic, model-free interpreter
├── surfaces/        surface contracts and Playwright adapter
├── policy/          layered allowlists and risk decisions
├── interventions/   state machine and exclusive control leases
├── evidence/        redaction, hashing, manifests, local store
├── runs/            application services, results, audit journal
├── providers/       OpenAI adapter
├── observability/   bounded model-call metrics
├── shared/          IDs, clocks, unique-key YAML parser
└── runtime/         settings, composition, session worker

apps/demo-bank/      synthetic target on :3001
apps/control-plane/  intervention console on :3000
capabilities/        published immutable YAML versions
evidence/            committed reviewer bundles; runtime output is ignored
docs/                implementation-accurate design documentation
```

## Documentation

The [documentation index](docs/README.md) routes each question to its reference page and defines
shared terminology. Start with [Architecture](docs/architecture.md),
[Data models](docs/data-models.md), and [Capability and replay](docs/capability-and-replay.md).
[REPORT.md](REPORT.md) is the required seven-part design summary.

## Deliberate cuts

Published capability artifacts, content-addressed visual assets, and sanitized evidence are durable local files. Run journals, discovery-suite progress, leases, interventions, and live browser sessions remain in memory. The operator console is not a complete run or capability UI. There is no PostgreSQL adapter, WebSocket, distributed queue, authentication layer, native desktop adapter, or discovery continuation after human takeover. The visual adapter is implemented for browser-rendered surfaces; Citrix and native desktop transport remain outside this slice.
