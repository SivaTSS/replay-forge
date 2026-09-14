# Evidence

Three primary model discoveries target the single servicing workstation:

| Bundle | Business task |
|---|---|
| [discovery-servicing-transaction](discovery-servicing-transaction/manifest.json) | Investigate a transaction and verify account/transaction identities |
| [discovery-servicing-loan-payoff](discovery-servicing-loan-payoff/manifest.json) | Issue a non-binding dated payoff quote |
| [discovery-servicing-card-lock](discovery-servicing-card-lock/manifest.json) | Temporarily lock the selected card and verify the result |

Each suite automatically validates Harbor and Summit before publication. The manifest binds the
actual recording run, source revision, command, exact run artifact, redaction metadata, and hashes.
The primary result can name its draft version; suite finalization assigns the published version
and validated tenant set.

Two separately captured model-free replays use the published payoff artifact without provider
credentials or artifact modifications:

| Bundle | Verified result |
|---|---|
| [replay-servicing-payoff](replay-servicing-payoff/manifest.json) | Success with exact amount, requested date, and confirmation checks before sanitized export |
| [replay-servicing-missing-record](replay-servicing-missing-record/manifest.json) | `target_absent` failure with a fully masked screenshot; no invented not-found business outcome |

These recordings used source revision `a462cdf`. Their synthetic invocation data and expected
results live in [the replay specification](../config/replay-evidence.yaml), not in the runtime.

Payoff also has five genuine scenario-discovery bundles and twelve model-free validation bundles
covering normal completion, two negative outcomes, two application failures, and notice recovery
on both tenants. See the [complete linked matrix](../docs/verification.md#scenario-matrix).
Discovery used `6699114`; the complete replay matrix used `7512f5e`. Those validation bundles
contain the unpublished merged candidate; successful finalization published version `1.0.2`.
Their exact artifact hashes distinguish that candidate from the original immutable source version.

```bash
uv run python scripts/verify_evidence_bundles.py evidence --require-submission
```

Bundles contain `artifact.yaml`, `events.jsonl`, `result.json`, and `manifest.json`.
Discovery screenshots are transient; these are structured evidence bundles, not retained raw
PII-bearing page captures. Private runtime records are ignored and are not submission files.

To reproduce both replays after starting the demo bank, choose a new ignored output directory
and supply the actual source commit. Existing destinations are rejected before execution:

```bash
PLAYWRIGHT_BROWSERS_PATH=/tmp/replayforge-playwright-browsers \
uv run python scripts/capture_replay_evidence.py --spec config/replay-evidence.yaml \
  --commit-sha "$(git rev-parse HEAD)" --output-root .local/replay-reproduction
```

To restore and revalidate the five saved payoff scenarios without any model calls:

```bash
PLAYWRIGHT_BROWSERS_PATH=/tmp/replayforge-playwright-browsers \
uv run python scripts/validate_scenario_evidence.py \
  --spec config/servicing-discovery.yaml --workflow servicing_loan_payoff_quote \
  --primary-version 1.0.1 \
  --scenario member_not_found=evidence/discovery-payoff-member-not-found \
  --scenario quote_date_unavailable=evidence/discovery-payoff-quote-date-unavailable \
  --scenario invalid_payoff_date=evidence/discovery-payoff-invalid-payoff-date \
  --scenario member_restricted=evidence/discovery-payoff-member-restricted \
  --scenario acknowledge_member_notice=evidence/discovery-payoff-acknowledge-member-notice
```

This performs real UI replays and publishes the next unused version only if every required case
passes; existing versions are never overwritten. Optional `--output-root`, `--evidence-prefix`,
and `--commit-sha` export those actual replay proofs to a new directory.

Old-UI runs were removed, not relabeled as workstation runs. Browser regressions for policy-driven
handoff use explicit temporary fixtures and real live control, not fabricated discovery evidence.
See [verification](../docs/verification.md) for the precise proof boundaries.
