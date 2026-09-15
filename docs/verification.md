# Verification and evidence

[Documentation index](README.md)

ReplayForge's verification combines genuine model-driven discovery, deterministic browser replay,
and focused contract tests. The evidence connects each published workflow to its recorded execution;
regression tests exercise the safety and control-transfer rules shared by all workflows.

## Current replay verification

The [fresh 32-case matrix](../evidence/README.md#current-replay-matrix) on `6c5a304` passed for
the three unchanged published capabilities on both tenants: 10 successes (including four notice
recoveries), 14 business outcomes, and 8 expected application failures. Each failure has an
unmasked, integrity-verified screenshot. No model calls or artifact republishing were required.
This rerun uses the [measured 20-second perception budget](operations.md#grounding-deadlines);
the older readiness-timeout bundle has been removed, not relabeled as a successful run.

## One command

```bash
bash scripts/verify.sh
```

The gate checks documentation, formatting, types, evidence integrity, target business rules,
both frontend builds, and unit/Chromium tests. Unit tests enforce 90% branch-aware domain coverage
with the checked-in adapter exclusions. Browser tests run separately from coverage tracing so
instrumentation does not distort OCR deadlines. Builds and browser tests run sequentially.

For focused checks after dependencies and frontends have been built:

```bash
uv run pytest backend/tests/unit -q
PLAYWRIGHT_BROWSERS_PATH=/tmp/replayforge-playwright-browsers \
uv run pytest backend/tests/integration -q
uv run python scripts/verify_evidence_bundles.py evidence --require-submission
uv run python scripts/check_docs.py
```

## Verified baseline

The full backend/browser regression below was recorded against code revision `4739d33`.
Frontend build results were recorded against `f4d3d62`; the later revision left frontend source
unchanged. Regression tests use no model calls.

| Gate | Recorded result |
|---|---|
| Unit/API regression | 875 passed; 90.90% branch-aware domain coverage |
| Real Chromium regression | 83 passed: handoff, target correctness, changed inputs, and viewport reuse |
| Python quality | Formatting/lint passed; 179-file type check passed |
| Frontends | Both production builds and type checks passed; four demo test files passed |
| Tooling | 14 tests passed |
| Evidence integrity | 49 verified bundles: 17 discovery and 32 current replay |
| Focused evidence/contract recheck | 274 capability, evidence, and journal tests passed |
| Credential review | No configured credentials or recognized secret patterns found in tracked files and reachable history |

The evidence recheck also resolved every recording commit, validated all seven published artifact
versions, and inspected the retained diagnostic archives. Negative-test credentials and identity strings
are synthetic fixtures. The README viewer image contains synthetic records and no access token.

These results identify tested revisions and environments. The current 32-case rerun passes with
the [measured perception budget](operations.md#grounding-deadlines).

## Control-transfer evidence

The [real-browser obstruction test](../backend/tests/integration/test_playwright_surface.py)
checks manual clearance, fresh target resolution, same-step continuation, and verified success
using the unchanged published payoff artifact. Its latest run passed. Superseded replay bundles
have been removed; current application-failure screenshots are in the matrix below.

The additional discovery-console regression did not complete: resume was rejected because manual
input had not been applied. That console check needs follow-up; it is separate from the 32 passing
automated replay cases. No unverified console changes are included.

Unit tests cover uncertain-dispatch handling, effect-verification requirements, preserved retry
budgets, fresh output reads, ownership conflicts, and denied-location rejection. Discovery handoff
uses the same live-session boundary; unattended validation still gates capability publication.

## Coverage boundaries

The scenario matrix below records genuine outcomes and recoveries for the three workflows.
Application-login expiry, learned reauthentication, and role-denial discovery are the next
target-specific coverage extensions. Member restriction and control-lease expiry are tested
separately because they represent different conditions.

The card-lock artifact's descriptive risk wording differs from its executable classification;
[artifact interpretation](../evidence/README.md#artifact-interpretation) explains which fields
govern execution. Existing bundle bytes and hashes remain unchanged.

## What is proved where

| Claim | Executable check | Boundary |
|---|---|---|
| Real model discovery | Primary and scenario bundles below | Provider-backed primary and scenario recordings across three task families |
| Task-independent compilation | [Generic compiler tests](../backend/tests/unit/discovery/test_generic_compiler.py) | New task means goal and input contract, not a task adapter |
| Model-free reuse | [Replay matrix](../backend/tests/integration/test_visual_portability.py) | Both tenants, changed member/inputs, 1440×900; provider credentials unset |
| Single deployed UI | [Route tests](../backend/tests/integration/test_demo_routes.py) | Registered workstation entry resolves correctly; unsupported routes are rejected |
| Bank correctness | [Workstation tests](../backend/tests/integration/test_servicing_workstation.py), [interaction tests](../backend/tests/integration/test_servicing_interactions.py), target unit tests | Deterministic application regression tests |
| Same-session handoff | [Session test](../backend/tests/integration/test_playwright_surface.py), [console test](../backend/tests/integration/test_operator_console.py) | Real-browser tests with injected sensitive policy and a visible obstruction over the unchanged published payoff artifact |
| Blocked discovery handoff | [Discovery console test](../backend/tests/integration/test_discovery_handoff.py), [continuation tests](../backend/tests/unit/discovery/test_continuation.py) | Controlled provider fixtures with real browser control, same-session resume, re-pause, termination, and shutdown |
| Live replay/history | [Console test](../backend/tests/integration/test_operator_console.py), [managed replay matrix](../backend/tests/integration/test_visual_portability.py) | Actual PNGs before completion; history remains read-only across resume; refresh reconnects without another run |
| Viewer isolation | [Viewer unit tests](../backend/tests/unit/runs/test_viewing.py), [HTTP tests](../backend/tests/unit/api/test_viewing_api.py) | Token authorization, bounded frame/event retention, expiry, no-cache responses |
| Error semantics | [Replay engine tests](../backend/tests/unit/replay/test_engine.py) | Declared outcomes, recoveries, ambiguous targets, safe retries, and failures |
| Privacy | Artifact, journal, evidence, and provider unit suites | Classification/known-value guards for structured data; raw screenshot retention is explicitly declared, not PII sanitization |
| Empty-registry onboarding | [Runtime tests](../backend/tests/unit/runtime/test_composition.py) | No application-specific seed capability required |

The handoff fixture is explicitly marked `injected-handoff-test`, not `openai`. It adds a
sensitive step and an observable resume condition to a temporary artifact. The operator then
acts in the real retained browser and replay completes in that same session. This tests control
transfer without falsely publishing a special model-discovered handoff capability.

All three tasks have genuine successful-flow discovery evidence. Payoff `1.0.2` additionally
declares two negative outcomes, two application failures, and one recovery, with genuine discovery
and fresh replay on both tenants. Card lock `1.0.2` adds three business outcomes, two application
failures, and a notice recovery. Transaction `1.0.3` adds two negative outcomes from a newly
discovered path that explicitly binds member, account, and reference selection.
Unknown states fail closed; engine tests alone do not prove application-specific branches.

The evidence gate also requires independently exported successful and failed model-free replay
logs; merely finding valid discovery manifests is insufficient. The failure must include a richer
attachment. An empty evidence directory fails verification.

## Scenario matrix

| Original genuine discovery bundle | Original published version | Proof |
|---|---|---|
| [Transaction investigation](../evidence/discovery-servicing-transaction/manifest.json) | `member.transaction_investigation/1.0.1` | Six fields; transaction and account identity comparisons; Harbor/Summit validation |
| [Loan payoff](../evidence/discovery-servicing-loan-payoff/manifest.json) | `member.servicing_loan_payoff_quote/1.0.1` | Issued quote and receipt; returned date matches requested date; Harbor/Summit validation |
| [Temporary card lock](../evidence/discovery-servicing-card-lock/manifest.json) | `member.temporary_card_lock/1.0.1` | Reversible mutation, fresh pre/post card identity checks, exact final locked status, receipt; Harbor/Summit validation |

Transaction [version 1.0.3](../capabilities/member.transaction_investigation/1.0.3.yaml) extends
a [fresh parameterized primary discovery](../evidence/discovery-transaction-parameterized/manifest.json),
published as `1.0.2`. It searches by member, explicitly selects the supplied account, then filters
by transaction reference. The original `1.0.1` recording is retained as provenance, not used as
proof of these stronger selection semantics.

| Transaction case | Discovery | Exact result | Harbor replay | Summit replay |
|---|---|---|---|---|
| Normal task | [Trace](../evidence/discovery-transaction-parameterized/manifest.json) | `success` | [Evidence](../evidence/replay-raw-transaction-harbor-primary/manifest.json) | [Evidence](../evidence/replay-raw-transaction-summit-primary/manifest.json) |
| Missing member | [Trace](../evidence/discovery-transaction-member-not-found/manifest.json) | `business_outcome:member_not_found` | [Evidence](../evidence/replay-raw-transaction-harbor-member-not-found/manifest.json) | [Evidence](../evidence/replay-raw-transaction-summit-member-not-found/manifest.json) |
| Reference belongs to another account | [Trace](../evidence/discovery-transaction-not-found/manifest.json) | `business_outcome:transaction_not_found` | [Evidence](../evidence/replay-raw-transaction-harbor-transaction-not-found/manifest.json) | [Evidence](../evidence/replay-raw-transaction-summit-transaction-not-found/manifest.json) |

The final case deliberately uses a reference that exists in the default account but not the
requested account. It proves that the lookup does not silently accept the default. No recovery
is expected in these read-only cases. Primary discovery ran through the live execution API with
the checked-in goal and inputs; the manifest's capture command reproduces the same contract.
Scenario discovery used the capture CLI. Neither used scripted browser navigation.

The following genuine **scenario discovery** bundles extend the original payoff trace.
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
| Normal task | `success` | [Evidence](../evidence/replay-raw-payoff-harbor-primary/manifest.json) | [Evidence](../evidence/replay-raw-payoff-summit-primary/manifest.json) |
| Missing member | `business_outcome:member_not_found` | [Evidence](../evidence/replay-raw-payoff-harbor-member-not-found/manifest.json) | [Evidence](../evidence/replay-raw-payoff-summit-member-not-found/manifest.json) |
| Unavailable date | `business_outcome:quote_date_unavailable` | [Evidence](../evidence/replay-raw-payoff-harbor-quote-date-unavailable/manifest.json) | [Evidence](../evidence/replay-raw-payoff-summit-quote-date-unavailable/manifest.json) |
| Invalid calendar date | `failure:invalid_payoff_date` | [Evidence](../evidence/replay-raw-payoff-harbor-invalid-payoff-date/manifest.json) | [Evidence](../evidence/replay-raw-payoff-summit-invalid-payoff-date/manifest.json) |
| Restricted member | `failure:member_restricted` | [Evidence](../evidence/replay-raw-payoff-harbor-member-restricted/manifest.json) | [Evidence](../evidence/replay-raw-payoff-summit-member-restricted/manifest.json) |
| Member notice | `success` + named `recovery_completed` | [Evidence](../evidence/replay-raw-payoff-harbor-acknowledge-member-notice/manifest.json) | [Evidence](../evidence/replay-raw-payoff-summit-acknowledge-member-notice/manifest.json) |

Card lock [version 1.0.2](../capabilities/member.temporary_card_lock/1.0.2.yaml) passed the following
matrix with model credentials disabled. Each branch has a separate genuine discovery trace:

| Card case | Discovery | Exact result | Harbor replay | Summit replay |
|---|---|---|---|---|
| Normal task | [Original](../evidence/discovery-servicing-card-lock/manifest.json) | `success` | [Evidence](../evidence/replay-raw-card-harbor-primary/manifest.json) | [Evidence](../evidence/replay-raw-card-summit-primary/manifest.json) |
| Missing member | [Trace](../evidence/discovery-card-member-not-found/manifest.json) | `business_outcome:member_not_found` | [Evidence](../evidence/replay-raw-card-harbor-member-not-found/manifest.json) | [Evidence](../evidence/replay-raw-card-summit-member-not-found/manifest.json) |
| Already locked | [Trace](../evidence/discovery-card-card-already-locked/manifest.json) | `business_outcome:card_already_locked` | [Evidence](../evidence/replay-raw-card-harbor-card-already-locked/manifest.json) | [Evidence](../evidence/replay-raw-card-summit-card-already-locked/manifest.json) |
| Expired card | [Trace](../evidence/discovery-card-card-expired/manifest.json) | `business_outcome:card_expired` | [Evidence](../evidence/replay-raw-card-harbor-card-expired/manifest.json) | [Evidence](../evidence/replay-raw-card-summit-card-expired/manifest.json) |
| Invalid reason | [Trace](../evidence/discovery-card-invalid-maintenance-reason/manifest.json) | `failure:invalid_maintenance_reason` | [Evidence](../evidence/replay-raw-card-harbor-invalid-maintenance-reason/manifest.json) | [Evidence](../evidence/replay-raw-card-summit-invalid-maintenance-reason/manifest.json) |
| Restricted member | [Trace](../evidence/discovery-card-member-restricted/manifest.json) | `failure:member_restricted` | [Evidence](../evidence/replay-raw-card-harbor-member-restricted/manifest.json) | [Evidence](../evidence/replay-raw-card-summit-member-restricted/manifest.json) |
| Member notice | [Trace](../evidence/discovery-card-acknowledge-member-notice/manifest.json) | `success` + named `recovery_completed` | [Evidence](../evidence/replay-raw-card-harbor-acknowledge-member-notice/manifest.json) | [Evidence](../evidence/replay-raw-card-summit-acknowledge-member-notice/manifest.json) |

Recovery acknowledges the notice, scrolls to restore the learned target, and rejoins the original
program. Replay then verifies card identity, applies the lock, re-reads identity/status, and checks
the confirmation. Normal and terminal cases execute no recovery. An earlier acknowledgement-only
trace failed at the next target and is not part of this proof.

Each retained replay manifest records the actual run ID, revision `6c5a304`, reproduction command,
published artifact identity, and hashes. The 32 current replays replace older replay bundles.
Discovery records retain their original recording revisions and pre-publication artifact identities.
See the [reproduction instructions](../evidence/README.md) for the saved-capability invocations.

## Evidence bundle anatomy

```text
scenario/
├── artifact.yaml      Exact contract used by the recorded run
├── events.jsonl       Ordered, sanitized run events
├── result.json        Sanitized terminal result
└── manifest.json      Provenance, command, redaction metadata, closed-set hashes
```

A run requiring richer evidence may also include unredacted failure/handoff PNGs or sanitized trace attachments. Superseded masked replay bundles have been removed.
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
| Current-artifact input and scenario acceptance checks | Check only artifact syntax or historical versions | Every supplied input must participate in an action/target, and current artifacts must contain the declared case set; these are distribution tests, not task-specific runtime rules |
| Thin-border tests across scales and light/dark backgrounds | Assume a component box proves a control | Real text pixels, clipped rows, zero/one/two buttons and coarse whole-frame analysis exercise local border confirmation and fail-closed ambiguity |
| Actual Chromium pointer/keyboard tests | Mock the application for end-to-end claims | Exercise rendering, focus, scrolling, and retained sessions |
| Temporary explicit policy fixtures | Keep obsolete production demo artifacts | Isolate engine fault/handoff tests without maintaining a second UI |
| Isolated operator test ports | Reuse fixed runtime/console ports | Avoid disrupting an operator's running services |
| One heavy verification workload at a time | Unbounded parallel browsers/builds | Keep memory predictable |

## Secret audit

Before publication, scan tracked files and reachable Git history for credential patterns and
configured credential values without printing those values. Secret files, raw evidence, discovery
captures, plans, and runtime state stay ignored. No scanner can certify the absence of every
possible secret; combine scanning with diff review and classification tests.
