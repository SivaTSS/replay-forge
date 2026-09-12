# Implementation plan 01 — demo realism and portability

Status: approved for implementation

Scope: address the first remaining review item after DOM-independent replay

Primary deliverable: a credible, adversarial demonstration of deterministic visual replay

## 1. Objective

Turn the current synthetic canvas demo into a controlled validation surface that demonstrates how one immutable capability behaves across tenant variation, viewport scaling, ordinary runtime exceptions, and unsafe visual ambiguity.

The completed implementation must prove that `member.lookup_savings_balance@3.1.0` can operate a canvas-only workflow with:

- No DOM or accessibility locators.
- No persisted click coordinates.
- No model calls during replay.
- Multiple account rows with identical action icons.
- Different row ordering and presentation across Harbor and Summit.
- Three viewport sizes.
- A delayed response.
- A recoverable notice.
- A legitimate member-not-found outcome.
- A declared permission failure.
- Fail-closed behavior when text or icons become unsafe to resolve.

The desired evidence shape is:

```text
                           ┌─ Harbor ─ 1024×640
one reviewed artifact ─────├─ Harbor ─ 1280×800
                           ├─ Summit ─ 1280×800
                           └─ Summit ─ 1440×900
                                      │
                                      ├─ success
                                      ├─ delayed success
                                      ├─ bounded recovery
                                      ├─ business outcome
                                      ├─ declared failure
                                      └─ fail-closed visual drift
```

## 2. Scope decisions

These decisions are fixed for this implementation. Do not reopen them unless a repository constraint makes one impossible.

| Question | Options considered | Decision | Reason |
|---|---|---|---|
| Target type | Public site, second local app, richer existing app | Build a richer local canvas application | It is deterministic, bank-relevant, DOM-hostile, safe, and controllable |
| Public-site validation | Automate a public sandbox or avoid it | Do not use a public site | External sites add network, terms, rate-limit, and uncontrolled-drift problems while usually exposing cleaner DOMs than the intended environment |
| Breadth | Add several workflows or deepen one | Deepen the savings-balance workflow | The assignment explicitly rewards depth over breadth |
| Existing visual route | Modify or preserve it | Preserve it | Artifact `3.0.0` is immutable and must remain replayable |
| Artifact strategy | Replace `3.0.0` or publish a version | Publish `3.1.0` | The caller contract stays the same while the internal replay contract becomes stronger |
| Variation generation | Random mutation or named fixtures | Use named deterministic fixtures | Every test must reproduce exactly and every failure must be diagnosable |
| Repeated row actions | Give each icon a unique design or repeat the same icon | Repeat identical icons | This forces the locator to combine semantic visual context with template matching |
| Scaling claim | Add queues and workers or prove artifact reuse | Prove reuse and graceful degradation | The assignment explicitly discourages premature scaling infrastructure |
| Stability benchmark | Add a large repeated-run harness or retain a focused matrix | Retain a focused deterministic matrix | Cross-tenant reuse is already the selected stretch direction; do not add another broad stretch goal |

## 3. Non-goals

Do not add any of the following in this step:

- A public target website.
- A second application family.
- Random UI mutation.
- PostgreSQL or object storage.
- Queues, distributed workers, or WebSockets.
- Native desktop automation.
- A general tenant-overlay system.
- Automatic compatibility or drift approval.
- Videos.
- A repeated-run stability product.
- Changes to `comments.md` or `comments_2.md`.

## 4. Preserve the current implementation

The existing route and artifact remain a regression fixture:

```text
/{tenant}/visual-terminal
member.lookup_savings_balance@3.0.0
```

Do not mutate `3.0.0.yaml`, its provenance hash, or its hashed template asset. Its Harbor and Summit browser tests must continue to pass.

Create a new route and entry point:

```text
/{tenant}/visual-workbench
entry point: visual_member_workbench
```

The new artifact will target only this entry point.

## 5. Work package A — richer canvas application

### 5.1 Files

Create:

```text
apps/demo-bank/app/[tenant]/visual-workbench/page.tsx
apps/demo-bank/app/[tenant]/visual-workbench/visual-workbench.tsx
```

Do not duplicate unrelated shell or DOM-demo code. It is acceptable for the new canvas component to be self-contained.

### 5.2 Surface contract

The page must expose one canvas and no usable DOM controls or displayed values. The canvas may retain one descriptive `aria-label`, but all workflow controls, labels, rows, dialogs, and values must be painted pixels.

Use a logical canvas size of `1280×800`. The CSS canvas must fill the viewport. The three tested viewports all use the same 16:10 aspect ratio so this step does not silently claim arbitrary aspect-ratio or device-pixel-ratio support.

### 5.3 State machine

Use an explicit union for the screen state:

```text
search
loading
results
notice
details
not-found
permission-denied
```

Required transitions:

```text
search
  ├─ normal member ───────────────► results
  ├─ delayed member ─► loading ───► results
  ├─ notice member ───────────────► notice ─► results
  ├─ restricted member ───────────► permission-denied
  └─ unknown member ──────────────► not-found

results ── click an account action ──► details for that exact account
```

Use a ref to retain the pending timer. Clear it on unmount and before starting another delayed transition. A stale timer must never overwrite a newer UI state.

### 5.4 Account data

Render three real, clickable rows:

| Account | Masked number | Displayed balance |
|---|---|---:|
| Checking | `•••• 0110` | `$842.11` |
| Savings | `•••• 0421` | `$1,420.57` |
| Auto loan | `•••• 9902` | `-$7,800.00` |

All three rows must use the same open/chevron icon and the same icon dimensions.

Clicking an icon must store the selected account and render that account's actual detail view. A wrong click must therefore extract the wrong account type and fail the capability's typed output or final checkpoint. Do not make the non-savings icons decorative.

### 5.5 Tenant variation

Harbor and Summit represent two configured versions of the same underlying vendor product.

Use these variations:

- Harbor row order: Checking, Savings, Auto loan.
- Summit row order: Auto loan, Savings, Checking.
- Different palettes.
- Different headings.
- Different horizontal offsets.
- Different safe font metrics or font families.
- The same business labels and meanings.

The Savings row must move between tenants. The capability must not depend on its ordinal row or absolute screen position.

### 5.6 Deterministic fixture records

Keep this mapping in one typed constant. Do not distribute special-case identifier checks across event handlers.

| Fixture name | Synthetic member ID | Behavior | Expected terminal result |
|---|---:|---|---|
| `normal` | `12345` | Normal member | `success` |
| `delayed` | `13579` | Results appear after approximately 900 ms | `success` |
| `notice` | `67890` | Known informational notice before results | one recovery, then `success` |
| `restricted` | `24680` | Current role cannot view the member | `failure/permission_denied` |
| `missing` | `99999` | No matching member | `business_outcome/member_not_found` |
| `duplicate_search` | `33333` | A second rendered `Search` control appears after the ID is entered | `failure/target_ambiguous` |
| `changed_icon` | `44444` | The Savings action icon changes to a structurally different symbol | `failure/target_absent` |

The duplicate Search control must be inside the artifact's bounded Search region and must have a real click hitbox. The resolver must reject it before either control is clicked.

The changed Savings icon must retain a real hitbox while no longer matching the reviewed chevron. Checking and Auto loan may retain the original chevron outside the Savings-context search region.

Persistent evidence and any matrix summary must use fixture names, not raw member identifiers. Source code and tests may contain these explicitly synthetic values.

## 6. Work package B — OCR-contextual image matching

The richer table exposes a real ambiguity: three identical chevrons are valid global template matches. Selecting the first one is unsafe. Add a contextual template locator that first identifies the Savings row from rendered text and then searches for the icon only within a region derived from that fresh anchor.

### 6.1 Schema model

Edit:

```text
backend/src/replayforge/capabilities/models.py
```

Add a strict model equivalent to:

```python
class OcrAnchor(ArtifactModel):
    value: str = Field(min_length=1, max_length=200)
    match: MatchMode = MatchMode.EXACT
    search_region: NormalizedRegion | None = None
    minimum_confidence: float = Field(default=0.85, ge=0, le=1)
```

Extend `ImageAnchorCandidate` with:

```python
context_anchor: OcrAnchor | None = None
relative_search_region: RelativeRegion | None = None
```

Validation rules:

1. `context_anchor` and `relative_search_region` must either both be present or both be absent.
2. Neither is required for existing artifacts.
3. If contextual fields are present, reject a simultaneous top-level `search_region`. This avoids two competing definitions of the template search area.
4. Keep all existing scale-range validation.
5. Add `"1.2"` to the accepted capability schema versions while retaining `"1.0"` and `"1.1"`.

### 6.2 Resolution behavior

Edit:

```text
backend/src/replayforge/surfaces/vision.py
```

For a contextual `ImageAnchorCandidate`:

1. Run OCR on the current screenshot.
2. Resolve `context_anchor.value` using its match mode, confidence floor, and optional normalized search region.
3. Require exactly one anchor.
4. Return `target_absent` when there are zero anchors.
5. Return `target_ambiguous` when there is more than one anchor.
6. Derive the template search rectangle with the existing anchor-height-relative geometry function.
7. Clip the rectangle to the viewport.
8. Run image-template matching only inside that derived rectangle.
9. Apply score and uniqueness gates normally.
10. Return only a transient region tied to the current frame hash.

The resolved target must still be refreshed immediately before the click by the existing `_fresh_visual` path.

### 6.3 Correct same-scale ambiguity detection

The existing implementation uses one `cv2.minMaxLoc` result per scale. That cannot reliably detect multiple identical icons at the same scale. Correct it as part of this work.

For each evaluated scale:

1. Calculate the template response map.
2. Extract no more than ten spatial peaks using non-maximum suppression.
3. Stop extracting peaks when the next score is lower than:

   ```text
   minimum_score - uniqueness_margin
   ```

4. Suppress an area large enough that the same physical icon is not emitted repeatedly at the same scale.
5. Merge detections from different scales when their regions refer to the same physical icon. Use a small, deterministic IoU or center-distance helper.
6. Retain the highest score for each physical detection.
7. Sort the distinct detections by score.

Then apply:

```text
best score below minimum_score
    → target_absent

best score - second distinct score below uniqueness_margin
    → target_ambiguous

otherwise
    → unique match
```

Keep the algorithm bounded by the existing finite scale range and the ten-peak limit. Do not introduce an unbounded image scan.

### 6.4 Unit tests

Extend:

```text
backend/tests/unit/capabilities/test_models.py
backend/tests/unit/surfaces/test_vision.py
```

Required cases:

- Context fields present together are valid.
- Either context field alone is invalid.
- A contextual candidate plus a top-level `search_region` is invalid.
- An old non-contextual image candidate remains valid.
- Three identical icons with a global search resolve as `target_ambiguous`.
- The same image with a unique Savings OCR anchor resolves the Savings-row icon when context is used.
- A missing context anchor returns `target_absent`.
- Duplicate context anchors return `target_ambiguous`.
- Contextual matching works at scales `0.8`, `1.0`, and approximately `1.125`.
- Peak extraction never exceeds the explicit bound.

Use generated OpenCV test frames and the existing stub OCR recognizer. Unit tests must not initialize RapidOCR.

## 7. Work package C — browser/runtime configuration

### 7.1 Playwright driver

Edit:

```text
backend/src/replayforge/surfaces/playwright.py
```

Required changes:

- Register the `visual_member_workbench` entry point.
- Map it to `/{tenant}/visual-workbench`.
- Add a configurable `Viewport` field to `PlaywrightSurfaceDriver` with a `1280×800` default.
- Use that viewport when creating a browser context.
- Store an explicit `rendered_surface: bool` on `PlaywrightSurfaceSession`.
- Set it from the chosen entry point.
- Use the flag for OCR observation and canvas evidence masking.
- Normalize `/visual-workbench` to the existing logical route `/members/search`.

Do not add repeated string checks such as `"/visual-workbench" in page.url`. Centralize the surface classification in the session field.

### 7.2 Runtime settings

Edit:

```text
backend/src/replayforge/runtime/settings.py
backend/src/replayforge/runtime/composition.py
```

Add bounded settings:

```text
browser_viewport_width:  integer, 800–2560, default 1280
browser_viewport_height: integer, 500–1600, default 800
```

Pass the configured viewport to both replay and discovery drivers. Preserve all existing constructor behavior for callers that omit it.

The implementation must test only these 16:10 viewports in this step:

- `1024×640`
- `1280×800`
- `1440×900`

Do not claim arbitrary aspect-ratio or device-pixel-ratio support.

## 8. Work package D — capability `3.1.0`

Create:

```text
capabilities/member.lookup_savings_balance/3.1.0.yaml
```

### 8.1 Contract

Use:

```yaml
schema_version: "1.2"
capability:
  id: member.lookup_savings_balance
  version: 3.1.0
compatibility:
  supported_variants: [harbor, summit]
  entry_point: visual_member_workbench
```

Keep the current caller-facing input and five-output contract unchanged.

### 8.2 Main flow

The artifact must contain:

1. OCR-relative member-ID input.
2. OCR-text Search action.
3. OCR-contextual edge-template match for the Savings-row icon.
4. OCR-relative extraction of:
   - `member_id`
   - `account_type`
   - `currency`
   - `available_balance`
   - `as_of`
5. Output-valid postconditions.
6. The existing identity and final business checkpoint.

Every target must have visual candidates and zero semantic candidates. No locator may use coordinates.

### 8.3 Runtime states

The Search step must reference:

- `member_not_found` as a business outcome.
- `dismiss_known_notice` as a recovery.
- `permission_denied` as an application failure.

The notice recovery must:

- Trigger on rendered `Important notice` text.
- Click the rendered `Continue` control using OCR.
- Have `max_uses: 1`.
- Verify that `Member Results` appears.
- Resume at `account.open_savings`.

The permission failure must:

- Trigger on rendered `Permission denied` text.
- Be non-recoverable.
- Report `expected_state: member_results`.
- Report `observed_state: permission_denied`.

Set the Search postcondition timeout to approximately 1,500–2,000 ms so the controlled 900 ms delay is handled by existing condition polling. Do not increase unrelated global timeouts.

### 8.4 Contextual locator example

The final values must be calibrated from real screenshots; do not copy these illustrative coordinates without measurement.

```yaml
visual_candidates:
  - strategy: image_anchor
    asset_key: asset://sha256/...
    content_hash: sha256:...
    context_anchor:
      value: Savings
      match: exact
      minimum_confidence: 0.85
      search_region:
        x: 0.20
        y: 0.28
        width: 0.55
        height: 0.45
    relative_search_region:
      x: 14.0
      y: -0.8
      width: 7.0
      height: 3.0
    minimum_score: 0.75
    uniqueness_margin: 0.03
    minimum_scale: 0.8
    maximum_scale: 1.2
    scale_step: 0.025
```

Do not lower OCR or template thresholds merely to make a failing matrix cell pass. Inspect observed scores, correct region geometry first, and document any threshold change.

### 8.5 Asset and artifact integrity

Reuse the current hashed template asset only if the new chevron has identical logical geometry. Never overwrite a content-addressed file.

After authoring the artifact:

1. Load it through the production serializer.
2. Calculate its canonical content hash.
3. Patch `provenance.artifact_content_hash` with that value.
4. Reload it and assert the stored and calculated hashes match.
5. Regenerate `schemas/capability-artifact-v1.schema.json` with the existing export script.
6. Run the artifact-integrity integration test across every version.

## 9. Work package E — browser acceptance matrix

Create:

```text
backend/tests/integration/test_visual_portability.py
```

Move the reusable demo-bank process fixture from `test_playwright_surface.py` to:

```text
backend/tests/integration/conftest.py
```

Do not change its behavior except where needed for safe reuse. Preserve all existing integration tests.

### 9.1 Portability cases

| Tenant | Viewport | Fixture | Expected result |
|---|---:|---|---|
| Harbor | 1280×800 | `normal` | Success with the exact five outputs |
| Summit | 1280×800 | `normal` | Same outputs and artifact hash |
| Harbor | 1024×640 | `normal` | Success |
| Summit | 1440×900 | `normal` | Success |

For every success, assert:

```json
{
  "member_id": "<invocation input>",
  "account_type": "savings",
  "currency": "USD",
  "available_balance": "1420.57",
  "as_of": "2026-09-10T12:30:00Z"
}
```

### 9.2 Runtime-state cases

| Tenant | Viewport | Fixture | Required assertion |
|---|---:|---|---|
| Harbor | 1280×800 | `delayed` | Success without recovery |
| Summit | 1280×800 | `notice` | Exactly one recovery followed by success |
| Harbor | 1280×800 | `missing` | `business_outcome/member_not_found` at `search.submit` |
| Summit | 1280×800 | `restricted` | `failure/permission_denied` at `search.submit` |

### 9.3 Fail-closed cases

| Tenant | Viewport | Fixture | Required assertion |
|---|---:|---|---|
| Harbor | 1280×800 | `duplicate_search` | `failure/target_ambiguous` at `search.submit` |
| Summit | 1280×800 | `changed_icon` | `failure/target_absent` at `account.open_savings` |

For `duplicate_search`, assert that no action-intent event exists for `search.submit`; ambiguity must stop execution before a click is dispatched.

### 9.4 Structural and evidence assertions

Also assert:

- Canvas-only observation exposes zero actionable DOM controls.
- Canvas-only observation exposes zero DOM extractable fields.
- Every `3.1.0` target uses visual candidates.
- Every `3.1.0` target has an empty semantic candidate tuple.
- No `3.1.0` locator uses coordinates.
- Every terminal result references a verified evidence manifest.
- Persisted visual screenshots include `mask:rendered-canvas`.
- Notice recovery events occur exactly once.
- Delayed success has no recovery events.
- Harbor and Summit execute the exact same artifact version and hash.
- Existing `3.0.0` Harbor and Summit browser tests remain green.

## 10. Work package F — documentation

Update documentation only after the browser matrix passes. Document measured behavior, not intended behavior.

### 10.1 `README.md`

- Make `3.1.0` the stated latest/default artifact.
- Give one exact normal replay command.
- Give concise commands for notice, not-found, and permission cases.
- Give the focused portability-test command.
- State why a public target was deliberately rejected.

### 10.2 `REPORT.md`

Under `3. Determinism & error handling`:

- Add the pixel-only runtime-state matrix.
- Explain delayed waiting, bounded recovery, business outcome, declared failure, and fail-closed ambiguity.

Under `4. Heterogeneity & multi-tenant`:

- State that one artifact is tested on two tenants.
- State that account rows are reordered.
- State the three tested viewport sizes.
- Explain OCR-contextual template resolution.
- Distinguish supported variation from unsafe drift.
- Do not claim native desktop execution or hundreds of deployed tenants.

Keep the required report within its approximate 1–3 page target. Replace weaker text rather than continually appending paragraphs.

### 10.3 `docs/verification.md`

- Add the ten-cell behavior matrix plus structural assertions.
- Link every claim to `test_visual_portability.py`.
- Keep the distinction between committed evidence and automated browser evidence explicit.

### 10.4 `docs/requirements.md`

- Update the visual-control and multi-tenant proof rows with the exact new implementation.
- Leave PostgreSQL, native desktop, drift overlays, and distributed execution at their existing status.

### 10.5 `docs/capability-and-replay.md`

- Explain contextual image anchors.
- Show why a global icon template is ambiguous in a repeated-row table.
- Add the decision: OCR anchor plus relative template region was chosen over row coordinates, ordinal selection, or first-match behavior.
- Add `3.1.0` to the committed-version table.

## 11. Implementation order

Follow this dependency order. Do not attempt the documentation first.

```text
1. Record the current targeted test baseline
2. Add schema models and validation
3. Correct template duplicate detection
4. Add contextual template resolution and unit tests
5. Add configurable browser viewport and rendered-surface flag
6. Build the visual-workbench canvas route
7. Author and hash artifact 3.1.0
8. Add the real-browser acceptance matrix
9. Calibrate regions and thresholds from observed screenshots/scores
10. Run regression and quality gates
11. Update documentation from verified results
12. Run secret/integrity checks and review the final diff
```

Recommended atomic commits after the complete implementation is green:

```text
feat(vision): add OCR-contextual template grounding
feat(demo): add adversarial visual workbench capability
test: add visual replay portability matrix
docs: document visual portability evidence
```

Do not commit intermediate broken artifact hashes.

## 12. Validation commands

Run the narrowest checks first:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run pytest \
  backend/tests/unit/capabilities/test_models.py \
  backend/tests/unit/surfaces/test_vision.py -q
```

Then static Python validation:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run ruff format --check backend scripts pyproject.toml
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run ruff check backend scripts
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run mypy backend/src backend/tests
```

Then frontend validation:

```bash
npx --yes pnpm@10.15.1 typecheck
npx --yes pnpm@10.15.1 --filter demo-bank build
```

Then real-browser validation:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run pytest \
  backend/tests/integration/test_visual_portability.py -q

UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run pytest \
  backend/tests/integration/test_playwright_surface.py -q
```

Then the repository gate:

```bash
bash scripts/verify.sh
```

The repository currently has a known environment-specific FastAPI `TestClient` hang in its installed Starlette/httpx2 combination. If it reproduces, report it explicitly and provide the completed targeted checks. Do not weaken, skip, or rewrite unrelated API tests as part of this plan.

Finally:

```bash
git diff --check
git status --short
```

Run the repository's existing secret scan before committing. Never print credential values while scanning.

## 13. Definition of done

This plan is complete only when all of the following are true:

- The original visual route and artifact `3.0.0` remain valid.
- Artifact `3.1.0` passes every positive matrix cell.
- Every negative cell returns the exact intended result code and step.
- Identical row icons are resolved using the freshly observed Savings text context.
- No ordinal row, global first match, or saved row coordinate is used.
- Template ambiguity detection recognizes multiple same-scale matches.
- Replay has no model dependency.
- Artifact `3.1.0` contains no DOM locators or coordinates.
- Its artifact and asset hashes verify.
- Evidence remains redacted and the canvas remains masked before persistence.
- Documentation separates implemented proof, designed extension points, and explicit limits.
- No unrelated infrastructure was added.
- `comments.md` and `comments_2.md` remain untouched and untracked.

## 14. Expected final handoff

The implementing model must report:

1. Files added and changed.
2. The final artifact version and canonical hash.
3. Every matrix cell and its observed terminal result.
4. Unit, typing, frontend, and browser-test results.
5. Full verification status, including the exact blocker if the known API test hang remains.
6. Any threshold changed during calibration and the measured reason.
7. Confirmation that no public site, model replay call, DOM locator, or persisted coordinate was introduced.
8. Confirmation that the two comment files were not modified or staged.
