# Implementation Plan

This document sequences later implementation. It does not authorize coding during the documentation stage.

## Milestone 0 — Contract freeze

Deliver artifact schema, API schemas, result/error vocabulary, run and lease state machines, policy decision table, target scenarios, and architecture dependency rules. Gate: examples validate conceptually and no requirement lacks an owner.

## Milestone 1 — Workspace and contracts

Establish pnpm/uv workspace, quality tools, Docker Compose, PostgreSQL migrations, generated OpenAPI workflow, configuration validation, health/readiness, IDs/clocks, and domain packages. Gate: clean bootstrap; backend/frontend types; database migration; no feature module violates dependency rules.

## Milestone 2 — Demo bank

Build both tenant variants, synthetic fixtures, member search/account detail flow, iframe/table challenges, deterministic fault controls, and polished enterprise UI. Gate: all scenarios are manually reproducible and inaccessible fault controls are disabled outside demo/test mode.

## Milestone 3 — Core ports and evidence

Implement surface, policy, evidence, repositories, run lifecycle, event recording, redaction-before-write, content hashing, and Playwright adapter. Gate: fake/real adapter contract suites pass; no unsanitized evidence reaches storage.

## Milestone 4 — Discovery and compilation

Implement provider-neutral normalized observations/actions, OpenAI adapter, stopping/progress rules, policy interception, locator capture, compiler parameterization, static artifact validation, and registry persistence. Gate: one genuine discovery creates a valid artifact; blocked actions never reach Playwright.

## Milestone 5 — Deterministic replay

Implement input validation, tenant resolution, locator engine, pre/postconditions, output binding, outcome precedence, bounded recovery, checkpoint verification, and terminal results. Gate: model-disabled success, business outcome, recovery, and hard-failure tests pass.

## Milestone 6 — Handoff

Implement intervention records, lease transitions, CDP screencast adapter, WebSocket messages, human input relay/audit, claim/release/resume/terminate, and resume checkpoint. Gate: same-session takeover is proven; concurrent/stale control fails safely.

## Milestone 7 — Control plane

Build application shell, Run Studio, Live Session, Capability Registry/detail, Runs/Evidence, Intervention Queue, Applications/tenants, and settings/readiness. Complete empty/loading/error/disconnected states and accessibility. Gate: primary UI E2E and accessibility suites pass.

## Milestone 8 — Stretch commitments

Demonstrate second-tenant overlay/fingerprint/drift behavior and agent-facing typed capability invocation. Gate: same artifact hash executes both variants; invocation schema and result validate.

## Milestone 9 — Evidence and hardening

Produce genuine evidence scenarios, validate hashes/redaction, finalize README and required REPORT, run clean-clone walkthrough, remove dead code/config, pin dependencies, and verify public-repo secret history. Gate: traceability is complete and submission checklist passes.

## Implementation discipline

- Finish each vertical gate before starting the next.
- Prefer typed domain contracts over framework convenience.
- Add a dependency only when it removes more risk/complexity than it adds.
- Do not create a new deployable unit for an internal module.
- Do not claim an outcome without observable verification.
- Keep commits reviewable and tests adjacent to behavior.
- Update relevant docs in the same change as contract behavior.
