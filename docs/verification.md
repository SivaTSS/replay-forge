# Verification and evidence

[Documentation index](README.md)

Tests, genuine discovery, and committed evidence are distinct claims. Only a provider-backed
recording is called discovery. The current distribution contains one target UI and three business
capabilities; retired applications and their runs are not presented as current proof.

## One command

```bash
bash scripts/verify.sh
```

The gate checks Markdown links and diagrams, formatting, types, evidence hashes, target business
rules, both frontend builds, and sequential unit/Chromium tests. Domain coverage remains
branch-aware on the unit suite with a 90% threshold and the checked-in adapter exclusions.
Browser tests run without coverage tracing: instrumentation can push OCR past the production
grounding deadline. The deadline is not increased for tests. Builds and browser tests run
sequentially to bound memory use.

For focused checks after dependencies and frontends have been built:

```bash
uv run pytest backend/tests/unit -q
PLAYWRIGHT_BROWSERS_PATH=/tmp/replayforge-playwright-browsers \
uv run pytest backend/tests/integration -q
uv run python scripts/verify_evidence_bundles.py evidence
uv run python scripts/check_docs.py
```

## What is proved where

| Claim | Executable check | Boundary |
|---|---|---|
| Real model discovery | Three bundles below | Screenshots and synthetic data; no scripted discovery |
| Task-independent compilation | [Generic compiler tests](../backend/tests/unit/discovery/test_generic_compiler.py) | New task means goal and input contract, not a task adapter |
| Model-free reuse | [Replay matrix](../backend/tests/integration/test_visual_portability.py) | Both tenants, changed member/inputs, 1440×900; provider credentials unset |
| Single deployed UI | [Route tests](../backend/tests/integration/test_demo_routes.py) | Old paths return 404 |
| Bank correctness | [Workstation tests](../backend/tests/integration/test_servicing_workstation.py), [interaction tests](../backend/tests/integration/test_servicing_interactions.py), target unit tests | Scripted application tests, not discovery |
| Same-session handoff | [Session test](../backend/tests/integration/test_playwright_surface.py), [console test](../backend/tests/integration/test_operator_console.py) | Sensitive policy injected into a temporary copy of the genuine payoff artifact |
| Blocked discovery handoff | [Discovery console test](../backend/tests/integration/test_discovery_handoff.py), [continuation tests](../backend/tests/unit/discovery/test_continuation.py) | Explicit blocking provider; real browser control, same-session resume, re-pause, termination and shutdown; not genuine discovery evidence |
| Live replay/history | [Console test](../backend/tests/integration/test_operator_console.py), [managed replay matrix](../backend/tests/integration/test_visual_portability.py) | Actual PNGs before completion; history remains read-only across resume; refresh reconnects without another run |
| Viewer isolation | [Viewer unit tests](../backend/tests/unit/runs/test_viewing.py), [HTTP tests](../backend/tests/unit/api/test_viewing_api.py) | Token authorization, bounded frame/event retention, expiry, no-cache responses |
| Error semantics | [Replay engine tests](../backend/tests/unit/replay/test_engine.py) | Declared outcomes, recoveries, ambiguous targets, safe retries, and failures |
| Privacy | Artifact, journal, evidence, and provider unit suites | Classification/known-value guards and full-viewport masking; not a universal PII detector |
| Empty-registry onboarding | [Runtime tests](../backend/tests/unit/runtime/test_composition.py) | No application-specific seed capability required |

The handoff fixture is explicitly marked `injected-handoff-test`, not `openai`. It adds a
sensitive step and an observable resume condition to a temporary artifact. The operator then
acts in the real retained browser and replay completes in that same session. This tests control
transfer without falsely publishing a special model-discovered handoff capability.

All three tasks have genuine successful-flow discovery evidence. Payoff `1.0.2` additionally
declares two negative outcomes, two application failures, and one recovery, with genuine discovery
and fresh replay on both tenants. Card lock `1.0.2` adds three business outcomes, two application
failures, and a notice recovery. Transaction exception coverage is still being established.
Unknown states fail closed; engine tests alone do not prove application-specific branches.

The submission gate also requires independently exported successful and failed model-free replay
logs; merely finding valid discovery manifests is insufficient. The failure must include a richer
attachment. An empty evidence directory fails verification.

## Scenario matrix

| Original genuine discovery bundle | Original published version | Proof |
|---|---|---|
| [Transaction investigation](../evidence/discovery-servicing-transaction/manifest.json) | `member.transaction_investigation/1.0.1` | Six fields; transaction and account identity comparisons; Harbor/Summit validation |
| [Loan payoff](../evidence/discovery-servicing-loan-payoff/manifest.json) | `member.servicing_loan_payoff_quote/1.0.1` | Issued quote and receipt; returned date matches requested date; Harbor/Summit validation |
| [Temporary card lock](../evidence/discovery-servicing-card-lock/manifest.json) | `member.temporary_card_lock/1.0.1` | Reversible mutation, fresh pre/post card identity checks, exact final locked status, receipt; Harbor/Summit validation |

The following are genuine **scenario discovery** bundles, extending the original payoff trace.
Their distinct model-free replay proofs follow below.

| Payoff scenario discovery | Observed evidence |
|---|---|
| [Missing member](../evidence/discovery-payoff-member-not-found/manifest.json) | Lookup prefix and positively verified no-match state |
| [Unavailable date](../evidence/discovery-payoff-quote-date-unavailable/manifest.json) | Requested date preserved; application's quote-window rejection observed |
| [Invalid calendar date](../evidence/discovery-payoff-invalid-payoff-date/manifest.json) | Actual invalid-date rejection, without substituting a date |
| [Restricted member](../evidence/discovery-payoff-member-restricted/manifest.json) | Restriction observed without changing permissions |
| [Member notice recovery](../evidence/discovery-payoff-acknowledge-member-notice/manifest.json) | Blocker marked before correction; corrective actions and restored-state assertion recorded |

Payoff [version 1.0.2](../capabilities/member.servicing_loan_payoff_quote/1.0.2.yaml) was published
only after this complete matrix passed with model credentials disabled:

| Case | Exact result | Harbor replay | Summit replay |
|---|---|---|---|
| Normal task | `success` | [Evidence](../evidence/replay-payoff-harbor-primary/manifest.json) | [Evidence](../evidence/replay-payoff-summit-primary/manifest.json) |
| Missing member | `business_outcome:member_not_found` | [Evidence](../evidence/replay-payoff-harbor-member-not-found/manifest.json) | [Evidence](../evidence/replay-payoff-summit-member-not-found/manifest.json) |
| Unavailable date | `business_outcome:quote_date_unavailable` | [Evidence](../evidence/replay-payoff-harbor-quote-date-unavailable/manifest.json) | [Evidence](../evidence/replay-payoff-summit-quote-date-unavailable/manifest.json) |
| Invalid calendar date | `failure:invalid_payoff_date` | [Evidence](../evidence/replay-payoff-harbor-invalid-payoff-date/manifest.json) | [Evidence](../evidence/replay-payoff-summit-invalid-payoff-date/manifest.json) |
| Restricted member | `failure:member_restricted` | [Evidence](../evidence/replay-payoff-harbor-member-restricted/manifest.json) | [Evidence](../evidence/replay-payoff-summit-member-restricted/manifest.json) |
| Member notice | `success` + named `recovery_completed` | [Evidence](../evidence/replay-payoff-harbor-acknowledge-member-notice/manifest.json) | [Evidence](../evidence/replay-payoff-summit-acknowledge-member-notice/manifest.json) |

Card lock [version 1.0.2](../capabilities/member.temporary_card_lock/1.0.2.yaml) passed the following
matrix with model credentials disabled. Each branch has a separate genuine discovery trace:

| Card case | Discovery | Exact result | Harbor replay | Summit replay |
|---|---|---|---|---|
| Normal task | [Original](../evidence/discovery-servicing-card-lock/manifest.json) | `success` | [Evidence](../evidence/replay-card-harbor-primary/manifest.json) | [Evidence](../evidence/replay-card-summit-primary/manifest.json) |
| Missing member | [Trace](../evidence/discovery-card-member-not-found/manifest.json) | `business_outcome:member_not_found` | [Evidence](../evidence/replay-card-harbor-member-not-found/manifest.json) | [Evidence](../evidence/replay-card-summit-member-not-found/manifest.json) |
| Already locked | [Trace](../evidence/discovery-card-card-already-locked/manifest.json) | `business_outcome:card_already_locked` | [Evidence](../evidence/replay-card-harbor-card-already-locked/manifest.json) | [Evidence](../evidence/replay-card-summit-card-already-locked/manifest.json) |
| Expired card | [Trace](../evidence/discovery-card-card-expired/manifest.json) | `business_outcome:card_expired` | [Evidence](../evidence/replay-card-harbor-card-expired/manifest.json) | [Evidence](../evidence/replay-card-summit-card-expired/manifest.json) |
| Invalid reason | [Trace](../evidence/discovery-card-invalid-maintenance-reason/manifest.json) | `failure:invalid_maintenance_reason` | [Evidence](../evidence/replay-card-harbor-invalid-maintenance-reason/manifest.json) | [Evidence](../evidence/replay-card-summit-invalid-maintenance-reason/manifest.json) |
| Restricted member | [Trace](../evidence/discovery-card-member-restricted/manifest.json) | `failure:member_restricted` | [Evidence](../evidence/replay-card-harbor-member-restricted/manifest.json) | [Evidence](../evidence/replay-card-summit-member-restricted/manifest.json) |
| Member notice | [Trace](../evidence/discovery-card-acknowledge-member-notice/manifest.json) | `success` + named `recovery_completed` | [Evidence](../evidence/replay-card-harbor-acknowledge-member-notice/manifest.json) | [Evidence](../evidence/replay-card-summit-acknowledge-member-notice/manifest.json) |

Recovery acknowledges the notice, scrolls to restore the learned target, and rejoins the original
program. Replay then verifies card identity, applies the lock, re-reads identity/status, and checks
the confirmation. Normal and terminal cases execute no recovery. An earlier acknowledgement-only
trace failed at the next target and is not part of this proof.

| Model-free replay bundle | Result | Proof boundary |
|---|---|---|
| [Payoff success](../evidence/replay-servicing-payoff/manifest.json) | Verified success | Changed member and date; exact outputs checked before redacted export; no provider credentials |
| [Missing record](../evidence/replay-servicing-missing-record/manifest.json) | `failure`, `target_absent` | Actual failed replay with masked screenshot; not a declared `business_outcome` |

Each manifest records the actual run ID, recording commit, command, artifact identity, and hashes.
Its primary result may identify the pre-publication draft: finalization adds validated tenants and
allocates the immutable published version. That expected difference is not a forged result.

Other failed or paused attempts stay in private runtime audit storage. They are not relabeled as
success, and no manual UI action is substituted for model discovery. The replay bundles were
captured from `a462cdf` (original pair), `7512f5e` (payoff branch matrix), and `9c9d051` (card matrix);
[reproduction instructions](../evidence/README.md) run the actual
saved capability with synthetic input data, not a scripted navigation substitute.

## Evidence bundle anatomy

```text
scenario/
├── artifact.yaml      Exact contract used by the recorded run
├── events.jsonl       Ordered, sanitized run events
├── result.json        Sanitized terminal result
└── manifest.json      Provenance, command, redaction metadata, closed-set hashes
```

A run requiring richer evidence may also include masked screenshots or sanitized trace attachments.
The current discovery bundles contain structured evidence; live screenshots sent to the model are
not stored as public screenshots. Full-viewport masks prove retention policy, not visual page content.

```bash
uv run python scripts/export_evidence.py EVIDENCE_MANIFEST DESTINATION \
  --scenario SCENARIO --artifact CAPABILITY_YAML \
  --commit-sha RECORDING_COMMIT --command 'ACTUAL_COMMAND'
```

Export verifies the source manifest and writes a new bundle without overwriting existing evidence.
The verifier rejects missing, changed, extra, or path-escaping files. Hash integrity is not signer
authenticity or proof that a model understood the task.

## Testing decisions

| Choice | Alternative | Reason |
|---|---|---|
| Exact output assertions on changed inputs | Only assert HTTP success | Detect wrong record, stale input, and neighboring-field extraction |
| Actual Chromium pointer/keyboard tests | Mock the application for end-to-end claims | Exercise rendering, focus, scrolling, and retained sessions |
| Temporary explicit policy fixtures | Keep obsolete production demo artifacts | Isolate engine fault/handoff tests without maintaining a second UI |
| Isolated operator test ports | Reuse fixed runtime/console ports | Avoid disrupting an operator's running services |
| One heavy verification workload at a time | Unbounded parallel browsers/builds | Keep memory predictable |

## Secret audit

Before publication, scan tracked files and reachable Git history for credential patterns and
configured credential values without printing those values. Secret files, raw evidence, discovery
captures, plans, and runtime state stay ignored. No scanner can certify the absence of every
possible secret; combine scanning with diff review and classification tests.
