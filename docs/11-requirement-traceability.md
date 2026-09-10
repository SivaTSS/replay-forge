# Requirement Traceability

This matrix is the under-delivery guardrail. Evidence paths are planned until genuine runs are produced; they must not be marked complete early.

| Requirement | Owning design | Verification | Planned evidence |
|---|---|---|---|
| Goal and target input | Product §4–6; API §4 | Discovery request validation and UI E2E | `discovery-success` |
| Model observe-decide-act loop | Discovery §2–6 | Provider/surface contracts; live smoke | `discovery-success/events.jsonl` |
| Real UI interaction | Architecture §3; Discovery §5 | Playwright integration | Discovery screenshots/trace |
| Stopping conditions | Discovery §4–6 | Budget/no-progress tests | Hard-failure run |
| Typed ordered artifact | Artifact §3–8 | Schema and round-trip tests | Produced capability YAML |
| Robust target identification | Artifact §9; Replay §9 | Locator fallback/ambiguity tests | Locator events/screenshots |
| Typed parameters and outputs | Artifact §6–7 | Validation/binding tests | Success request/result |
| Checkpoint | Artifact §13; Replay §10 | Mismatch tests | Success and mismatch results |
| Versioned/reviewable artifact | Artifact §2–4 | Immutability/hash tests; UI E2E | YAML, hash, capability view |
| Model-free deterministic replay | Replay §8–10 | Dependency guard and network denial | `evidence/replay-success` (verified) |
| Business outcome | Replay §12–13 | Member-not-found E2E | `evidence/replay-member-not-found` (verified) |
| Recoverable condition | Artifact §11; Replay §11–12 | Bounded recovery tests | `replay-recovery` |
| Hard debuggable failure | Replay §12–13 | Permission/checkpoint E2E | `replay-hard-failure` |
| Domain/route/action allowlist | Safety §3–6 | Policy decision table | Policy events |
| Risk treatment | Safety §7–9 | Block/approval tests | Policy block/handoff |
| Secret and PII protection | Safety §10–13 | Redaction/secret scans | All manifests report pass |
| Structured observability | Architecture §7; Testing §4 | Event schema/order tests | Three verified `events.jsonl` bundles |
| Rich failure evidence | Testing §4–5 | Evidence completeness test | Failure screenshot/trace |
| Detect and route intervention | Handoff §2–4 | Trigger/state tests | `human-handoff` |
| Same-session human control | Handoff §5–9 | Browser-context identity E2E | Handoff frames/events |
| Resume and preserve context | Handoff §10–12 | Resume-checkpoint E2E | Handoff result/timeline |
| Control ownership | Handoff §5–8 | Lease concurrency tests | Ownership events |
| Surface abstraction | Architecture §4–6, §11 | Fake/Playwright contracts | Design and contract results |
| Legacy/desktop extension | Architecture §11 | Design review | REPORT summary |
| Multi-tenant reuse | Artifact §5; UX §13 | Two-variant E2E | `evidence/tenant-reuse` (verified) |
| Tenant/version drift | Artifact §5; UX §13 | Fingerprint mismatch tests | Drift event/view |
| Exact README demo commands | Implementation §9 | Clean-clone walkthrough | Evidence manifests |
| Required seven REPORT headings | Docs index; Implementation §9 | Heading/length check | Root `REPORT.md` |
| Genuine discovery evidence | Testing §5 | Manual live run + integrity CI | `discovery-success` |
| Agent-facing capability API | API §5–7 | Schema and invocation E2E | Agent invocation result |

## Completion states

- `planned`: design and intended verification exist.
- `implemented`: behavior and automated test exist.
- `evidenced`: required sanitized artifact exists and validates.
- `complete`: all required columns are satisfied at the same commit.

No row may move directly from planned to complete.

The evidence index is authoritative for captured-versus-outstanding scenarios. A “verified” annotation above means the stable bundle exists and passes `scripts/verify_evidence_bundles.py`; it does not imply adjacent requirements are complete.
