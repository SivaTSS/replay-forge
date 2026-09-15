# Evidence

## Current replay matrix

All **32 cases passed** against the unchanged published capabilities on revision `6c5a304`:
10 successful completions (including four learned recoveries), 14 declared business outcomes,
and 8 expected application failures. No model calls were made. Each failure retains a verified,
unmasked PNG; successful and business-outcome runs retain structured evidence, not a screen history.

| Capability | Version | Harbor | Summit | Invocation specification |
|---|---|---|---|---|
| Transaction investigation | `1.0.3` | 3/3 | 3/3 | [6 cases](../config/replay-transaction-validation.yaml) |
| Loan payoff quote | `1.0.2` | 6/6 | 6/6 | [12 cases](../config/replay-payoff-validation.yaml) |
| Temporary card lock | `1.0.2` | 7/7 | 7/7 | [14 cases](../config/replay-card-validation.yaml) |

Cases check the exact declared status/code; recovery cases additionally require a recorded
`recovery_completed` event and successful completion. The engine enforces the artifact's typed
outputs and checkpoints. No artifact edits, learned-step substitutions, or republishing were needed.

### Open the recordings

| Task / case | Harbor | Summit |
|---|---|---|
| transaction / primary | [manifest](replay-raw-transaction-harbor-primary/manifest.json) | [manifest](replay-raw-transaction-summit-primary/manifest.json) |
| transaction / member-not-found | [manifest](replay-raw-transaction-harbor-member-not-found/manifest.json) | [manifest](replay-raw-transaction-summit-member-not-found/manifest.json) |
| transaction / transaction-not-found | [manifest](replay-raw-transaction-harbor-transaction-not-found/manifest.json) | [manifest](replay-raw-transaction-summit-transaction-not-found/manifest.json) |
| payoff / primary | [manifest](replay-raw-payoff-harbor-primary/manifest.json) | [manifest](replay-raw-payoff-summit-primary/manifest.json) |
| payoff / member-not-found | [manifest](replay-raw-payoff-harbor-member-not-found/manifest.json) | [manifest](replay-raw-payoff-summit-member-not-found/manifest.json) |
| payoff / quote-date-unavailable | [manifest](replay-raw-payoff-harbor-quote-date-unavailable/manifest.json) | [manifest](replay-raw-payoff-summit-quote-date-unavailable/manifest.json) |
| payoff / invalid-payoff-date | [manifest](replay-raw-payoff-harbor-invalid-payoff-date/manifest.json) | [manifest](replay-raw-payoff-summit-invalid-payoff-date/manifest.json) |
| payoff / member-restricted | [manifest](replay-raw-payoff-harbor-member-restricted/manifest.json) | [manifest](replay-raw-payoff-summit-member-restricted/manifest.json) |
| payoff / acknowledge-member-notice | [manifest](replay-raw-payoff-harbor-acknowledge-member-notice/manifest.json) | [manifest](replay-raw-payoff-summit-acknowledge-member-notice/manifest.json) |
| card / primary | [manifest](replay-raw-card-harbor-primary/manifest.json) | [manifest](replay-raw-card-summit-primary/manifest.json) |
| card / member-not-found | [manifest](replay-raw-card-harbor-member-not-found/manifest.json) | [manifest](replay-raw-card-summit-member-not-found/manifest.json) |
| card / card-already-locked | [manifest](replay-raw-card-harbor-card-already-locked/manifest.json) | [manifest](replay-raw-card-summit-card-already-locked/manifest.json) |
| card / card-expired | [manifest](replay-raw-card-harbor-card-expired/manifest.json) | [manifest](replay-raw-card-summit-card-expired/manifest.json) |
| card / invalid-maintenance-reason | [manifest](replay-raw-card-harbor-invalid-maintenance-reason/manifest.json) | [manifest](replay-raw-card-summit-invalid-maintenance-reason/manifest.json) |
| card / member-restricted | [manifest](replay-raw-card-harbor-member-restricted/manifest.json) | [manifest](replay-raw-card-summit-member-restricted/manifest.json) |
| card / acknowledge-member-notice | [manifest](replay-raw-card-harbor-acknowledge-member-notice/manifest.json) | [manifest](replay-raw-card-summit-acknowledge-member-notice/manifest.json) |

### Visible failure evidence

![Actual invalid-date replay failure](replay-raw-payoff-harbor-invalid-payoff-date/screenshots/001.png)

The screenshot shows the actual rejected date after replay reached loan servicing. Also inspect
[card-reason validation](replay-raw-card-harbor-invalid-maintenance-reason/screenshots/001.png),
[payoff restriction](replay-raw-payoff-harbor-member-restricted/screenshots/001.png), and
[card restriction](replay-raw-card-harbor-member-restricted/screenshots/001.png).
The equivalent Summit captures are linked through that tenant's manifests above.

To reproduce, start the demo and run each specification sequentially with a fresh output root:

```bash
PLAYWRIGHT_BROWSERS_PATH=/tmp/replayforge-playwright-browsers \
uv run python scripts/capture_replay_evidence.py \
  --spec config/replay-transaction-validation.yaml \
  --commit-sha "$(git rev-parse HEAD)" --output-root .local/new-replay-matrix
```

Repeat with the payoff and card specifications. Existing output names are rejected before execution.

The [perception-budget decision](../docs/operations.md#grounding-deadlines) explains why the
configurable limit is now 20 seconds. Confidence, uniqueness, policy, retry counts, and checkpoints
are unchanged. All 32 recordings use that committed configuration.


## Screenshot storage

The eight expected failures in this matrix retain actual, unmasked PNGs. They contain only
synthetic application data and were visually inspected. Each manifest records the exact hash and
`unredacted:raw-screenshot` declaration. Raw images require review before sharing; structured
logs and results still use redaction. Runtime copies live in the Git-ignored `evidence/runtime/`.
Successful and business-outcome runs do not save screen history. A failure before a page exists
cannot provide a screenshot. Superseded replay bundles were removed; Git history retains them.

## Genuine discovery recordings

These 17 recordings are the provenance for the three published tasks and their learned branches.
They are model-driven discoveries, not the model-free replays above. Each manifest links the
recording revision, exact artifact, ordered events, result, and integrity hashes.

| Recording | Manifest |
|---|---|
| card-acknowledge-member-notice | [Open](discovery-card-acknowledge-member-notice/manifest.json) |
| card-card-already-locked | [Open](discovery-card-card-already-locked/manifest.json) |
| card-card-expired | [Open](discovery-card-card-expired/manifest.json) |
| card-invalid-maintenance-reason | [Open](discovery-card-invalid-maintenance-reason/manifest.json) |
| card-member-not-found | [Open](discovery-card-member-not-found/manifest.json) |
| card-member-restricted | [Open](discovery-card-member-restricted/manifest.json) |
| payoff-acknowledge-member-notice | [Open](discovery-payoff-acknowledge-member-notice/manifest.json) |
| payoff-invalid-payoff-date | [Open](discovery-payoff-invalid-payoff-date/manifest.json) |
| payoff-member-not-found | [Open](discovery-payoff-member-not-found/manifest.json) |
| payoff-member-restricted | [Open](discovery-payoff-member-restricted/manifest.json) |
| payoff-quote-date-unavailable | [Open](discovery-payoff-quote-date-unavailable/manifest.json) |
| servicing-card-lock | [Open](discovery-servicing-card-lock/manifest.json) |
| servicing-loan-payoff | [Open](discovery-servicing-loan-payoff/manifest.json) |
| servicing-transaction | [Open](discovery-servicing-transaction/manifest.json) |
| transaction-member-not-found | [Open](discovery-transaction-member-not-found/manifest.json) |
| transaction-not-found | [Open](discovery-transaction-not-found/manifest.json) |
| transaction-parameterized | [Open](discovery-transaction-parameterized/manifest.json) |

The parameterized transaction recording supersedes its original discovery on the same workstation;
the original remains discovery provenance. No older demo application is retained.

### Artifact interpretation

The card-lock description calls the operation sensitive, while executable risk and policy classify
it as reversible. Structured fields govern execution. These recordings preserve the actual contract;
a description correction belongs in a newly validated publication, not a rewritten evidence file.

## Verify all retained evidence

```bash
uv run python scripts/verify_evidence_bundles.py evidence --require-submission
```

The folder contains 49 bundles: 17 genuine discoveries and 32 current replays. Every bundle has
`artifact.yaml`, `events.jsonl`, `result.json`, and `manifest.json`; failure bundles also carry the
PNG and diagnostic ZIP declared by their manifest.
