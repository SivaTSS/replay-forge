# Frontend and UX Specification

> Implementation status: the current `apps/control-plane` vertical slice implements intervention lookup, exclusive claim, renewable heartbeat, lease-guarded viewport polling, release, begin-resume, and termination. The broader navigation and authoring surfaces below remain the product architecture, not a claim of completed UI breadth.

## Experience objective

ReplayForge should feel like high-trust operational infrastructure. The UI is the primary surface for launching runs, reviewing capabilities, diagnosing evidence, and taking control—not a decorative wrapper around CLI commands.

Principles:

- Make run state, control owner, risk, and next action immediately visible.
- Show summaries first and technical evidence on demand.
- Never present an action as complete until its postcondition is verified.
- Use distinct language for success, business outcome, recovery, intervention, and failure.
- Preserve context between run, capability, intervention, and evidence.
- Avoid vanity charts, template dashboards, and decorative complexity.

## Stack and module boundaries

- Current stable Next.js App Router, React, and strict TypeScript
- Tailwind CSS v4; shadcn/ui with Radix primitives
- TanStack Query v5 for server state; Zod at trust boundaries
- Generated OpenAPI client; no duplicate handwritten transport types
- React Flow for read-only capability visualization; Lucide icons
- WebSocket for live frames, events, and control input
- Vitest, Testing Library, and Playwright

Feature modules own their components, queries, schemas, and presentation logic: `run-studio`, `live-session`, `capabilities`, `runs`, `evidence`, `interventions`, `applications`, and `settings`. Shared UI contains only design-system primitives. Transient state stays local; no global store is added without a concrete need.

## Information architecture

Primary navigation: Overview, Run Studio, Capabilities, Runs, Interventions, Applications, and Settings. The shell always exposes environment, runtime readiness, provider availability, active runs, pending interventions, global search, and documentation.

## Visual language

- Neutral slate surfaces and restrained indigo interaction accents.
- Green exclusively means verified success; amber means recovery/approval/intervention; red means blocked/unsafe/failed.
- Blue represents automation and purple represents human ownership.
- Status always includes text and icon, never color alone.
- Sans-serif product text; monospace only for IDs, routes, locators, hashes, JSON, and YAML.
- Tabular numerals for timings and financial values.
- Motion is short, meaningful, and respects reduced-motion preferences.

## Overview

Show runtime readiness, Start Discovery and Run Capability actions, active runs, pending interventions, recent capabilities, meaningful terminal outcomes, and evidence-completeness warnings. The empty state explains the shortest valid demo path.

## Run Studio

Discovery captures application, tenant, entry point, goal, typed/classified inputs, typed outputs, provider/model, limits, browser visibility, and evidence level. Replay captures immutable capability version, compatible tenant, schema-generated inputs, effective policy, output contract, and evidence level.

A preflight panel validates runtime, provider, target, policy, entry point, database, and evidence storage. Start stays disabled with a precise reason until required checks pass.

## Live Session

Wide screens combine live browser, run summary, ownership/risk panel, and event timeline. The browser preserves aspect ratio, displays frame freshness, and is non-interactive unless the current user owns the lease.

The header shows run type, goal/capability, tenant, run ID, elapsed time, lifecycle state, and allowed controls. Ownership changes use a prominent badge and accessible live announcement.

Timeline events group by step: observation, model proposal for discovery, policy decision, action intent/dispatch, postcondition, recovery, outcome, and intervention. Expansion reveals reason codes, timestamps, locator attempts, evidence, and correlation IDs.

## Capability Registry

The list filters by application, tenant, risk, status, and tag. Rows show identity, version, contract summary, compatibility, last replay, and status.

Detail tabs: Overview, Contract, Flow, Locators, Outcomes and recovery, Policy, Versions, Replay history, and Raw artifact. React Flow renders typed nodes and branches read-only with an equivalent text representation. Node details expose target, conditions, retries, risk, and evidence rules.

Locator inspection shows ordered candidates, scope/frame path, expected count/state, portability, tenant overrides, and evidence. Raw artifact provides syntax-highlighted YAML, hashes, validation status, copy, and download.

## Runs and Evidence

Runs expose type, goal/capability, tenant, duration, terminal category, step count, intervention, and evidence completeness. Detail includes redacted inputs, outputs, timeline, recoveries, locator resolution, policies, human actions, screenshots, trace, and artifact. Business outcomes use neutral rather than failure styling.

Evidence supports before/after comparison, redaction status, step/timestamp correlation, and explicit missing/corrupt states. It never exposes local paths.

## Intervention Queue

Queue items show trigger, risk, waiting time, app/tenant, run type, step, owner, and Claim. The workspace combines live browser, escalation context, last safe state, policy explanation, human-action timeline, notes, and Claim, Release, Resume, Complete, and Terminate controls.

Sensitive approval has a dedicated surface naming the exact action, target, scope, and reason; it is not hidden in a generic modal.

## Applications and tenants

Application detail shows entry points, allowed origins/routes, surface, landmarks, timing, and capabilities. Tenant detail shows aliases, branding, entry-point/frame/locator/timing overrides, fingerprint, and drift. The UI marks which fields may narrow behavior and which safety/contract fields cannot change.

## Resilience and accessibility

- Skeletons match final layout; refresh never erases stable content.
- WebSocket reconnect displays degraded state and disables human input.
- REST data remains available while live transport is disconnected.
- Mutations prevent duplicate submission and show durable completion.
- Errors provide a recovery action and safe correlation ID.
- Controls have names, focus, and keyboard support; dialogs restore focus; tables have headers.
- Primary flows work at 200% zoom with no critical automated accessibility violations.
- Tablet layouts use drawers; phone layouts retain review but disable live control explicitly.

## Acceptance criteria

- A first-time reviewer can start the documented demo unaided.
- State and control owner are always obvious.
- Terminal categories are visually and linguistically correct.
- A capability is understandable without opening YAML.
- Human input cannot be sent while disconnected or without ownership.
- Loading, empty, invalid, disconnected, and failure states are tested.
