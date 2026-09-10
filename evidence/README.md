# Evidence Index

Every directory listed as verified below was produced through the public ReplayForge API against the synthetic target using real Chromium. `manifest.json` records the exact command, source-manifest hash, reviewed artifact hash, commit SHA, redaction directives, and hashes for `events.jsonl` and `result.json`.

## Verified scenarios

| Scenario | What it proves | Terminal status |
|---|---|---|
| [`replay-success`](replay-success/) | Model-free deterministic replay, five typed outputs, and checkpoint verification on Harbor | `success` |
| [`replay-member-not-found`](replay-member-not-found/) | Alternate UI state becomes the typed `member_not_found` business outcome | `business_outcome` |
| [`tenant-reuse`](tenant-reuse/) | The same capability ID, version, and content hash replay successfully on Summit | `success` |

Verify every stable bundle from the repository root:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run python scripts/verify_evidence_bundles.py evidence
```

## Explicitly not yet evidenced

- Genuine paid-provider discovery: provider integration is implemented, but no runtime OpenAI credential is available in this environment.
- Recovery and hard-failure bundles.
- Exported same-session human-handoff evidence bundle; a real Chromium integration already verifies retained-session input, metadata-only audit events, fresh-state revalidation, deterministic continuation, terminal evidence, and teardown.
- Selected redacted screenshots and Playwright trace archives.

These omissions remain visible by design. Unit or integration fixtures are never represented as genuine run evidence.
