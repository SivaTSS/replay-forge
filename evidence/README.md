# Evidence

Three genuine model discoveries target the single servicing workstation:

| Bundle | Business task |
|---|---|
| [discovery-servicing-transaction](discovery-servicing-transaction/manifest.json) | Investigate a transaction and verify account/transaction identities |
| [discovery-servicing-loan-payoff](discovery-servicing-loan-payoff/manifest.json) | Issue a non-binding dated payoff quote |
| [discovery-servicing-card-lock](discovery-servicing-card-lock/manifest.json) | Temporarily lock the selected card and verify the result |

Each suite automatically validates Harbor and Summit before publication. The manifest binds the
actual recording run, source revision, command, published artifact, redaction metadata, and hashes.
The primary result can name its draft version; suite finalization assigns the published version
and validated tenant set.

```bash
uv run python scripts/verify_evidence_bundles.py evidence
```

Bundles contain `artifact.yaml`, `events.jsonl`, `result.json`, and `manifest.json`.
Discovery screenshots are transient; these are structured evidence bundles, not retained raw
PII-bearing page captures. Private runtime records are ignored and are not submission files.

Old-UI runs were removed, not relabeled as workstation runs. Browser regressions for policy-driven
handoff use explicit temporary fixtures and real live control, not fabricated discovery evidence.
See [verification](../docs/verification.md) for the precise proof boundaries.
