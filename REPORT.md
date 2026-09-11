# ReplayForge Design Report

## 1. Architecture

ReplayForge is a modular monolith with framework-independent domain packages and ports for UI surfaces, model providers, artifact registry, evidence, run journals, and interventions. FastAPI, Playwright, OpenAI, and local repositories are adapters. A run gets an isolated browser runtime, retained only for typed same-session intervention. See [Architecture](docs/02-architecture.md).

## 2. Artifact schema

Strict frozen Pydantic aggregates serialize to reviewable YAML and generated JSON Schema. They contain symbolic inputs, ordered locators, conditions, bounded retries, business outcomes, checkpoint, compatibility, policy, and provenance. Versions are immutable and hashed; rediscovery atomically receives the next patch version. Executable code and model transcripts are forbidden. See [Artifact](docs/03-capability-artifact.md).

## 3. Determinism & error handling

Replay imports no model provider. It validates inputs before opening a browser, resolves exactly one visible target, evaluates policy before every action, applies bounded effect-aware retries, validates outputs, and returns one discriminated terminal result. Known absence is a business outcome. Ambiguity, drift, and unsafe state fail closed or intervene. See [Discovery and Replay](docs/04-discovery-and-replay.md).

## 4. Heterogeneity & multi-tenant

One reviewed artifact supports Harbor and Summit through a shared application-family contract. The adapter normalizes tenant routes and scopes controls through the legacy iframe. Future overlays may narrow entry points, locators, aliases, and timing, but cannot widen policy or alter business semantics.

## 5. Escalation & handoff

Discovery/replay can pause while retaining the live Playwright runtime on a dedicated owner thread. Intervention identity binds the same run and session. A renewable monotonic lease permits one owner and rejects stale commands. The operator console drives claim, heartbeat, ordered viewport polling, bounded left-click/text/key input, release, resume, and terminate through version-checked HTTP contracts. Input is single-use against the latest frame, runs on the browser-owner thread, and is audited without typed text. Replay resume captures fresh state, verifies the interrupted step's postcondition and location, transfers ownership to automation, and executes only remaining steps; a mismatch reopens the intervention. See [Human Handoff](docs/06-human-handoff.md).

## 6. Safety

Permission is the intersection of platform, application, tenant, capability, and invocation layers. Origins/routes/actions are exact allowlists and risk is independently classified from observed control semantics. Irreversible behavior is denied; sensitive behavior requires human approval. Events cross redaction and secret scanning before retention. API errors omit submitted values, settings use `SecretStr`, and commits are staged-secret-scanned. See [Safety](docs/05-safety-and-data.md).

## 7. Cuts

Gates enforce Ruff, strict mypy, 90% branch coverage, structural model-free replay, artifact hash integrity, lease races, and real Chromium. Composed integration loads YAML, executes eight steps, returns five outputs, verifies identity/checkpoint, persists redacted events and exactly one terminal result with SHA-256 manifest entries, independently verifies that manifest, and closes resources. Genuine bundles cover success, member-not-found, bounded interstitial recovery, typed permission failure with a masked screenshot, same-session handoff, and second-tenant reuse; outstanding scenarios are explicit in the [evidence index](evidence/README.md). Commands are in [README](README.md); strategy is in [Testing](docs/09-testing-and-evidence.md).

Depth was prioritized: one complete workflow, two tenant variants, and controlled faults. Metadata is currently in-memory rather than PostgreSQL. Continuous screencasting, broad co-browsing gestures, and discovery-loop continuation remain; the local operator console uses an intentionally bounded HTTP frame/input fallback. A paid genuine OpenAI evidence run requires a runtime key; the bounded provider path is implemented and tested, while replay needs no credential. See [Decisions and Cuts](docs/12-decisions-and-cuts.md).
