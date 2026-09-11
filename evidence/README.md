# Evidence Index

Every directory listed as verified below was produced through the public ReplayForge API against the synthetic target using real Chromium. `manifest.json` records the exact command, source-manifest hash, reviewed artifact hash, commit SHA, redaction directives, and hashes for `events.jsonl` and `result.json`.

## Verified scenarios

| Scenario | What it proves | Terminal status |
|---|---|---|
| [`replay-success`](replay-success/) | Model-free deterministic replay, five typed outputs, and checkpoint verification on Harbor | `success` |
| [`replay-member-not-found`](replay-member-not-found/) | Alternate UI state becomes the typed `member_not_found` business outcome | `business_outcome` |
| [`replay-recovery`](replay-recovery/) | A known interstitial triggers one bounded declared recovery before checkpoint-verified completion | `success` |
| [`replay-hard-failure`](replay-hard-failure/) | A rendered permission denial becomes a typed failure with a sanitized failure-state screenshot | `failure` |
| [`human-handoff`](human-handoff/) | Approval pause, exclusive claim, same-session input, fresh-state resume, masked before/after screenshots, and terminal checkpoint | `success` |
| [`tenant-reuse`](tenant-reuse/) | The same capability ID, version, and content hash replay successfully on Summit | `success` |

Verify every stable bundle from the repository root:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run python scripts/verify_evidence_bundles.py evidence
```

## Explicitly not yet evidenced

- Genuine paid-provider discovery: provider integration is implemented, but no runtime OpenAI credential is available in this environment.
- Playwright trace archives.

These omissions remain visible by design. Unit or integration fixtures are never represented as genuine run evidence.
