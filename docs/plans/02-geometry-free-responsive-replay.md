# Implementation plan 02 — geometry-free responsive visual replay

Status: approved for implementation

Primary deliverable: `member.lookup_savings_balance@3.2.0`, whose published
targets contain semantic identity but no recorded layout geometry or per-target
matching parameters.

Implementation rule: complete and commit each work package independently. Never
combine unrelated packages into one commit, never commit a failing package, and
never amend or squash the checkpoint commits while implementing this plan.

## 1. Objective and exact claim

Replace the layout calibration in schema `1.2` with deterministic grounding built
from every current screenshot.

```text
semantic target
      │
      ▼
current CSS-pixel screenshot ──► OCR + visual segmentation
                                      │
                                      ▼
                              frame-local layout graph
                                      │
                                      ▼
                              unique semantic relation
                                      │
                                      ▼
                           transient current-frame region
                                      │
                                      ▼
                              click / type / extract
```

The implementation is complete only when one immutable artifact succeeds across:

- Harbor and Summit.
- Compact card and wide table layouts.
- Multiple viewport dimensions and aspect ratios.
- Device scale factors from `1` through `2`.
- Tenant-dependent type, color, ordering, spacing, and icon placement.

The system must fail closed when a semantic or visual relationship is absent or
ambiguous.

The claim is deliberately bounded to responsive desktop browser applications whose
semantic labels and visual identities remain stable. Do not claim localization,
native desktop support, mobile support, or arbitrary application portability.

## 2. Hardcoding boundary

This boundary is mandatory.

### 2.1 Forbidden in schema `1.3` artifacts

- Absolute coordinates.
- Viewport dimensions.
- Normalized regions.
- Anchor-relative regions or offsets.
- Search rectangles.
- Recorded width or height.
- Scale ranges or scale steps.
- Per-target OCR confidence.
- Per-target similarity threshold.
- Per-target uniqueness margin.
- Row indexes, ordinal selection, or `first()` semantics.
- DOM, CSS, XPath, accessibility, label, or role locators for the canvas workflow.

### 2.2 Allowed artifact identity

- Visible semantic labels such as `Member ID`, `Search`, and `Savings`.
- Semantic match mode: `exact`, `contains`, or `regex`.
- Control category such as `text_input`.
- Content-addressed image identity: `asset_key` and `content_hash`.
- Action, output, policy, recovery, outcome, and checkpoint meaning.

### 2.3 Allowed only transiently

Runtime coordinates are unavoidable because Playwright's mouse ultimately needs a
current click point. They may exist only as the output of grounding for the current
frame. They must not be:

- Written to a capability.
- Added to discovery provenance.
- Written to a run journal.
- Exposed as a future replay input.
- Reused after the frame hash changes.
- Derived by scaling a recorded coordinate.

Discovery may use a model-proposed box to capture a signature. Discard the box as
soon as the signature is stored.

### 2.4 Universal runtime parameters

Deterministic OCR, visual similarity, ambiguity detection, and bounded work require
numeric limits. Put all such limits in one reviewed, versioned runtime policy. They
must never be configurable per capability, tenant, step, or target.

Do not hide workflow-specific values in runtime code. If a value changes only to
make the demo pass, the implementation violates this plan.

## 3. Fixed design decisions

| Decision | Options considered | Selected design | Reason |
|---|---|---|---|
| Primary replay signal | DOM, replay-time model, pixels | Pixels | Production surfaces may have poor or unavailable DOMs |
| Pixel targeting | Recorded coordinates, relative regions, current-frame reasoning | Current-frame reasoning | Recorded layout does not survive reflow |
| Current-frame reasoning | Replay-time multimodal model, deterministic graph | Deterministic graph | Reproducible, local, bounded, and inspectable |
| Text association | Fixed offset, nearest token only, layout graph | Layout graph | Supports horizontal and stacked fields without saved geometry |
| Image search | Global template, scoped ROI, semantic visual group | Semantic visual group | Avoids global repeated-icon ambiguity without a recorded ROI |
| Image scaling | Artifact scale sweep, normalized component signature | Normalized component signature | Removes per-target scale calibration |
| DPR handling | Manually rescale coordinates, CSS-pixel screenshots | CSS-pixel screenshots | Matches Playwright mouse coordinates directly |
| Compatibility | Rewrite `3.1.0`, publish `3.2.0` | Publish `3.2.0` | Capability versions are immutable |
| Runtime tuning | Target fields, code literals, reviewed policy | Reviewed policy | One auditable application-independent boundary |

## 4. Preserve these existing contracts

Do not edit these immutable artifacts or their hashed assets:

```text
capabilities/member.lookup_savings_balance/3.0.0.yaml
capabilities/member.lookup_savings_balance/3.1.0.yaml
capabilities/_assets/4681739777aa343ae7dd2f05d753acf15681c07f88bebf045fd88bce1d291f0c.png
```

Schema `1.0`, `1.1`, and `1.2` must remain loadable. Their existing DOM, OCR,
relative-region, and multiscale-template implementations remain compatibility paths.
Do not silently route old candidates through the new algorithm.

Keep the public caller contract unchanged:

```text
input:  member_id
output: member_id, account_type, currency, available_balance, as_of
```

Leave `comments.md` and `comments_2.md` untouched and untracked.

## 5. Work package A — schema `1.3` semantic visual contract

Primary files:

```text
backend/src/replayforge/capabilities/models.py
backend/tests/unit/capabilities/test_models.py
backend/tests/unit/capabilities/test_serialization.py
schemas/capability-artifact-v1.schema.json
```

### 5.1 Add strict candidate models

Add these frozen, extra-forbidden models. Use the exact discriminator strings shown.

```python
class RenderedTextCandidate(ArtifactModel):
    strategy: Literal["rendered_text"]
    value: str = Field(min_length=1, max_length=200)
    match: MatchMode = MatchMode.EXACT


class RenderedLabeledControlCandidate(ArtifactModel):
    strategy: Literal["rendered_labeled_control"]
    label: str = Field(min_length=1, max_length=200)
    label_match: MatchMode = MatchMode.EXACT
    control_kind: Literal["text_input"]


class RenderedFieldValueCandidate(ArtifactModel):
    strategy: Literal["rendered_field_value"]
    label: str = Field(min_length=1, max_length=200)
    label_match: MatchMode = MatchMode.EXACT


class RenderedGroupImageCandidate(ArtifactModel):
    strategy: Literal["rendered_group_image"]
    group_label: str = Field(min_length=1, max_length=200)
    group_label_match: MatchMode = MatchMode.EXACT
    asset_key: str = Field(pattern=r"^asset://sha256/[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
```

Extend `VisualLocatorCandidate` with these models without modifying the fields of
the legacy models.

Add a condition with no geometry or confidence field:

```python
class RenderedTextCondition(ArtifactModel):
    kind: Literal["rendered_text"]
    value: str = Field(min_length=1, max_length=200)
    match: MatchMode = MatchMode.EXACT
```

Add it to the discriminated `Condition` union. Keep legacy `VisualTextCondition`.

### 5.2 Add schema-version enforcement

Accept schema version `1.3`. In `CapabilityArtifact.validate_semantics`, inspect all
main-step targets, recovery-step targets, nested `ElementCondition` targets, and
conditions nested through `all`, `any`, and `not`.

For schema `1.3`:

1. Every `visual_candidates` entry must be one of the four new rendered candidates.
2. Every pixel-text condition must be `RenderedTextCondition`.
3. Reject legacy `VisualTextCondition` because its defaults still represent
   per-target confidence/search policy.
4. Reject every non-empty `target.candidates` collection for this contract.
5. Reject mixed legacy and rendered visual candidates.

Implement traversal helpers rather than duplicating loops in the validator.

### 5.3 Required tests

- Each new candidate accepts its minimal valid shape.
- Extra fields, including every forbidden geometry/tuning name, are rejected.
- `RenderedGroupImageCandidate` rejects malformed asset hashes.
- `1.3` rejects legacy candidates and conditions, including nested conditions.
- `1.0` through `1.2` fixtures still validate unchanged.
- The generated JSON schema lists `1.0`, `1.1`, `1.2`, and `1.3`.
- The committed schema exactly equals `artifact_json_schema()`.

Regenerate the schema with:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run python backend/scripts/export_capability_schema.py
```

Run:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run pytest \
  backend/tests/unit/capabilities/test_models.py \
  backend/tests/unit/capabilities/test_serialization.py -q
```

Commit only after those tests pass:

```text
feat(schema): add geometry-free visual locator contract
```

## 6. Work package B — versioned vision policy

Create:

```text
backend/src/replayforge/runtime/vision_policy.py
config/vision-policy.yaml
backend/tests/unit/runtime/test_vision_policy.py
```

Edit:

```text
backend/src/replayforge/runtime/settings.py
backend/src/replayforge/runtime/composition.py
```

Follow the loading and validation pattern in `runtime/model_policy.py`:

- Frozen Pydantic models.
- `extra="forbid"`.
- `schema_version: Literal["1.0"]`.
- Maximum file size of 64 KiB.
- Safe YAML mapping only.
- Explicit lower and upper bounds for every field.
- Startup failure for missing, empty, oversized, malformed, or invalid policy.

The policy owns these categories:

```text
ocr.minimum_confidence
phrases.maximum_line_gap_in_text_heights
segmentation.minimum_component_area_ratio
segmentation.maximum_component_area_ratio
segmentation.maximum_components
image.canonical_width
image.canonical_height
image.minimum_similarity
image.uniqueness_margin
budgets.maximum_frame_pixels
budgets.maximum_grounding_milliseconds
```

Choose one checked-in value for each field using the generic unit fixture corpus
described in work package C. Do not add environment variables for individual policy
fields. Add only:

```text
vision_policy_file: Path = Path("config/vision-policy.yaml")
```

Expose the loaded policy through a private `RuntimeSettings` attribute and property,
matching `model_policy`.

Build exactly one `VisionGrounder` per runtime and inject the policy into it. Do not
allow capabilities to override it.

Required tests:

- Valid policy loads.
- Unknown fields fail.
- Missing, malformed, empty, and oversized files fail without leaking contents.
- Every numeric boundary is exercised.
- Runtime settings load the default policy.
- Existing settings behavior remains unchanged.

Run:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run pytest \
  backend/tests/unit/runtime/test_vision_policy.py \
  backend/tests/unit/runtime/test_composition.py -q
```

Commit:

```text
feat(runtime): centralize visual grounding policy
```

## 7. Work package C — frame-local layout graph

Primary implementation files:

```text
backend/src/replayforge/surfaces/vision.py
backend/src/replayforge/surfaces/models.py
backend/tests/unit/surfaces/test_vision.py
```

Splitting `vision.py` into one nearby module such as `visual_layout.py` is permitted
if it keeps OCR/segmentation graph logic testable. Do not create a framework of many
small files.

### 7.1 Internal graph types

Use immutable internal types equivalent to:

```python
VisualNodeKind = Literal["phrase", "control", "image", "container"]

@dataclass(frozen=True, slots=True)
class VisualNode:
    id: str
    kind: VisualNodeKind
    region: ScreenRegion
    text: str | None = None
    confidence: float | None = None

@dataclass(frozen=True, slots=True)
class VisualLayoutGraph:
    nodes: tuple[VisualNode, ...]
    containment: tuple[tuple[str, str], ...]
```

Node identifiers are frame-local and must not be persisted.

### 7.2 Phrase assembly

RapidOCR may split labels such as `Available balance`. Assemble phrases by:

1. Filtering tokens below the global OCR confidence floor.
2. Grouping tokens whose vertical overlap indicates the same text line.
3. Sorting each line left to right.
4. Joining neighbouring words when their gap is within the policy's multiple of the
   current frame's median OCR height.
5. Retaining both word nodes and assembled phrase nodes only if doing so does not
   create duplicate semantic matches. Prefer longest matching phrases during lookup.

Matching must reuse the existing Unicode normalization. Regex uses `fullmatch`.

### 7.3 Visual segmentation

Decode once per frame and cache the graph by frame hash with the existing bounded
cache strategy.

Generate component candidates from grayscale/edge images with both normal and
inverted thresholding so light and dark tenants work. Find contours and classify:

- Large enclosing rectangles or whitespace-separated panels as containers.
- Elongated enclosed regions associated with labels as control candidates.
- Non-text connected components as image candidates.

Exclude components that substantially overlap OCR text before image matching.
Deduplicate nested contours that describe the same physical boundary.

Derive measurements from current frame dimensions and median text height. The only
fixed safety values must come from `VisionGroundingPolicy`.

### 7.4 Structural association

Use this deterministic lexicographic ordering, not a target-tuned weighted formula:

1. Candidate shares the smallest detected container with the anchor.
2. Candidate follows the anchor in reading order.
3. Candidate is aligned on the same row or in the immediately following field row.
4. Candidate has the smallest gap measured in current median-text-height units.

Require one best structural tier. If multiple candidates remain in the same tier,
return `target_ambiguous`. If no structurally associated candidate exists, return
`target_absent`. Do not silently use the nearest global component.

For a `rendered_field_value`, return the union of all value phrase regions belonging
to the selected field, in reading order. Existing extraction then reads OCR within
that current region.

For a `rendered_labeled_control`, return the detected control rectangle, not a box
calculated from the label offset.

### 7.5 Scale-independent image signature

Keep the old multiscale template matcher for legacy candidates only.

For `rendered_group_image`:

1. Resolve exactly one `group_label` phrase.
2. Select the smallest visual container containing that phrase and at least one
   non-text component.
3. Enumerate image components inside the container.
4. Tightly crop each component.
5. Normalize it onto the policy's canonical canvas while preserving aspect ratio.
6. Compare it with the normalized stored signature using bidirectional chamfer
   similarity and edge overlap.
7. Produce a normalized score in `[0, 1]`.
8. Require the global minimum similarity.
9. Require the best score to exceed the runner-up by the global uniqueness margin.
10. Return the original current-frame component region.

If candidate grouping is not unique, fail before image scoring. A changed icon must
not pass merely because it is the only image in the group.

Update signature capture to trim empty edge borders before storage and reject
low-information signatures. The stored asset contains image shape only, never its
screen surroundings or position.

### 7.6 Resolve and execute behavior

`VisionGrounder.resolve()` dispatches legacy candidates to legacy code and new
candidates to graph resolution. Return the same `VisualTargetData` contract.

The Playwright session must continue to call `_fresh_visual()` immediately before
every click/type/extract action. Reject a resolved target when its frame hash no
longer matches the frame used for execution.

Never fall back from rendered candidates to DOM candidates, recorded coordinates,
or model calls.

### 7.7 Generic unit fixture corpus

Build frames in tests with OpenCV and stub OCR. Do not initialize RapidOCR.
The corpus must include shapes unrelated to the demo chevron so global policy is not
calibrated exclusively for the target application.

Required positive cases:

- Horizontal label and value.
- Label above value.
- Horizontal label and text input.
- Label above text input.
- Table row group.
- Card group.
- Reordered groups.
- Multiline and multiword phrases.
- Light and dark backgrounds.
- The same glyph translated and rendered at `0.6×`, `1×`, `1.5×`, and `2×`.
- Equivalent frames rasterized at DPR `1`, `1.25`, `1.5`, and `2`.

Required failures:

- Missing label.
- Duplicate label.
- Missing control.
- Two equally associated controls.
- Missing field value.
- Two equally associated values.
- Missing group boundary.
- Multiple plausible group boundaries producing different targets.
- Changed glyph.
- Duplicate matching glyphs in one semantic group.
- Low-information signature.
- Frame/component budget exceeded.

Calibrate the checked-in global policy only when all generic positives are separated
from all generic negatives. Record the observed worst-positive and best-negative
score in a test assertion or test comment. Do not use Harbor/Summit screenshots to
choose the boundary.

Run:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run pytest \
  backend/tests/unit/surfaces/test_vision.py -q
```

Commit:

```text
feat(vision): resolve targets from frame-local layout graphs
```

## 8. Work package D — DPR-safe Playwright frames

Primary files:

```text
backend/src/replayforge/runtime/settings.py
backend/src/replayforge/runtime/composition.py
backend/src/replayforge/surfaces/playwright.py
backend/tests/unit/surfaces/test_playwright_observation.py
backend/tests/unit/runtime/test_composition.py
```

Add:

```python
browser_device_scale_factor: float = 1.0
```

Validate it in the inclusive range `1.0` through `3.0`, then construct:

```python
Viewport(width, height, device_scale)
```

Pass `device_scale_factor=self.viewport.device_scale` to
`browser.new_context(...)`.

All viewport screenshots used for provider input, grounding, sanitized evidence,
and live intervention must explicitly use:

```python
scale="css"
```

Keep `full_page=False` for grounding. The resulting image dimensions must equal the
CSS viewport dimensions at every DPR. Continue using CSS coordinates for mouse and
human pointer input.

The full-page diagnostic `screenshot(destination)` method may remain a diagnostic
capture, but make its scale explicit and test/document the choice.

Required tests:

- Browser context receives viewport and device scale factor.
- CSS screenshot dimensions equal the configured viewport at DPR `1` and `2`.
- A fresh visual target clicks the same CSS-space location at both DPRs.
- Invalid device scale factors fail settings validation.
- Intervention frame/pointer contracts remain in CSS coordinates.

Run the relevant unit tests, then:

```text
feat(browser): normalize visual grounding to CSS pixels
```

## 9. Work package E — responsive canvas target

Read and follow `apps/demo-bank/AGENTS.md` before editing.

Primary file:

```text
apps/demo-bank/app/[tenant]/visual-workbench/visual-workbench.tsx
```

Keep one canvas and no usable DOM controls or displayed business values.

Replace the fixed logical `1280×800` drawing surface:

1. Observe CSS size with `ResizeObserver`.
2. Read `window.devicePixelRatio`.
3. Set canvas backing width/height to rounded CSS size multiplied by DPR.
4. Set the 2D context transform so all drawing and hit-testing use CSS coordinates.
5. Re-render after resize and DPR changes.
6. Clean up observers, timers, and listeners on unmount.

Implement two genuinely different layouts:

- Compact, below `1100` CSS pixels: stacked form controls and account cards.
- Wide, at least `1100` CSS pixels: horizontal search form and account table.

The `1100` breakpoint belongs only to the target application. It must not be
imported, copied, inferred, or referenced by ReplayForge runtime code.

In both layouts:

- Every account action has a real hitbox.
- A wrong account click opens that account's real details.
- The `Savings` label and its action glyph share a visible row/card container.
- Detail fields reflow from horizontal pairs to stacked label/value blocks.
- Harbor and Summit retain different account order, typography, palette, spacing,
  and glyph placement.
- Existing deterministic delayed, notice, missing, restricted, duplicate-search,
  and changed-icon fixtures continue to work.

Add one new deterministic `duplicate_field` fixture with an explicitly synthetic
member ID. It must render two equally plausible `Available balance` fields so the
new resolver returns `target_ambiguous` before extraction.

Run:

```bash
npx --yes pnpm@10.15.1 typecheck
npx --yes pnpm@10.15.1 --filter demo-bank build
```

Commit:

```text
feat(demo): add responsive table and card visual layouts
```

## 10. Work package F — discovery and immutable version publication

Primary files:

```text
backend/src/replayforge/providers/openai.py
backend/src/replayforge/discovery/compiler.py
backend/src/replayforge/capabilities/registry.py
backend/src/replayforge/surfaces/playwright.py
backend/tests/unit/providers/test_openai.py
backend/tests/unit/discovery/test_compiler.py
backend/tests/unit/capabilities/test_registry.py
```

### 10.1 Provider contracts

Mirror the four rendered candidate contracts in strict provider output models.
Provider prompts must request semantic labels and relationships first.

A coordinate proposal is permitted only during discovery and only when:

- The action is capturing an image signature.
- The provider also supplies a unique semantic group label.
- The box lies within the current CSS viewport.

Do not allow coordinate proposals to satisfy text, labeled-control, or field-value
targets.

### 10.2 Capture and compilation

`capture_locator()` must convert the temporary image box into a tightly cropped
signature and a `rendered_group_image` candidate. It must discard the proposal box.

The compiler must:

- Emit schema `1.3` for the geometry-free visual trace.
- Propose capability version `3.2.0`.
- Use `RenderedTextCondition` for visual conditions.
- Reject every legacy visual or semantic DOM candidate in a `1.3` visual trace.
- Reject an image candidate without a semantic group label.
- Reject labels observed more or less than once in the successful discovery frame.

### 10.3 Registry behavior

Change `publish_next` version selection to:

```text
if proposed version > latest registered version:
    use proposed version
else:
    increment the patch component of the latest version
```

Preserve immutable conflict and content-hash behavior. Required sequence:

```text
latest 3.1.0 + proposed 3.2.0 => 3.2.0
latest 3.2.0 + proposed 3.2.0 => 3.2.1
latest 3.2.4 + proposed 3.2.0 => 3.2.5
```

Run provider, compiler, and registry units. Commit:

```text
feat(discovery): publish semantic visual capabilities
```

## 11. Work package G — capability `3.2.0`

Create:

```text
capabilities/member.lookup_savings_balance/3.2.0.yaml
```

Use:

```yaml
schema_version: "1.3"
capability:
  id: member.lookup_savings_balance
  version: 3.2.0
compatibility:
  supported_variants: [harbor, summit]
  entry_point: visual_member_workbench
```

Use these exact target forms:

```yaml
# search.enter_member_id
- strategy: rendered_labeled_control
  label: Member ID
  label_match: exact
  control_kind: text_input

# search.submit
- strategy: rendered_text
  value: Search
  match: exact

# account.open_savings
- strategy: rendered_group_image
  group_label: Savings
  group_label_match: exact
  asset_key: asset://sha256/<new tightly-cropped signature digest>
  content_hash: sha256:<same digest>

# each extraction step
- strategy: rendered_field_value
  label: <field label>
  label_match: exact
```

Use `kind: rendered_text` for all visual postconditions, outcomes, recoveries,
failures, and checkpoint text checks.

The artifact must contain no YAML key matching this recursive denylist:

```text
x, y, width, height, viewport_width, viewport_height,
search_region, relative_region, relative_search_region,
minimum_confidence, minimum_score, uniqueness_margin,
minimum_scale, maximum_scale, scale_step
```

Add a test that walks the parsed raw YAML dictionaries and enforces the denylist.
Also assert all targets have an empty `candidates` tuple and only rendered visual
candidates.

Create a new content-addressed asset; never replace the existing asset. Compute and
set the canonical artifact hash using production serialization, then verify every
committed artifact and asset.

Commit only the artifact, new asset, integrity tests, and generated schema changes
belonging to this package:

```text
feat(capability): publish geometry-free savings replay
```

## 12. Work package H — browser portability and failure matrix

Extend:

```text
backend/tests/integration/test_visual_portability.py
```

Invoke `3.2.0` explicitly. Include DPR in evidence-directory names so parallel cases
cannot collide.

### 12.1 Positive matrix

| Tenant | Viewport | DPR | Expected layout |
|---|---:|---:|---|
| Harbor | `800×600` | `1` | Compact cards |
| Summit | `900×700` | `2` | Compact cards |
| Harbor | `1024×768` | `1.25` | Compact cards |
| Summit | `1280×720` | `1` | Wide table |
| Harbor | `1440×900` | `1.5` | Wide table |
| Summit | `1920×1080` | `2` | Wide table |

Every positive case must return:

```json
{
  "member_id": "<invocation input>",
  "account_type": "savings",
  "currency": "USD",
  "available_balance": "1420.57",
  "as_of": "2026-09-10T12:30:00Z"
}
```

Assert that Harbor and Summit use the same artifact content hash.

### 12.2 Existing runtime-state cases

Retain and run:

- Delayed success with no recovery.
- Known notice with exactly one recovery.
- `member_not_found` business outcome.
- `permission_denied` declared failure.
- Duplicate Search as `target_ambiguous`, with no action-intent event.
- Changed icon as `target_absent`.

### 12.3 New failure cases

- Duplicate `Available balance` association returns `target_ambiguous` at the
  extraction step.
- Two matching glyphs inside the Savings group return `target_ambiguous`.
- Missing visual container returns `target_absent`; it does not trigger a global
  image scan.
- A DPR change does not change CSS screenshot dimensions or result coordinates.

### 12.4 Persistence assertion

Inspect all journal event payloads recursively. Reject the geometry keys listed in
the artifact denylist when they describe resolved automation targets. Screenshots
remain permitted evidence, and human-intervention pointer inputs retain their
existing audited coordinate contract.

All terminal results must have verified evidence manifests, and every retained
canvas screenshot must carry `mask:rendered-canvas`.

Run both new and legacy browser suites before committing:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run pytest \
  backend/tests/integration/test_visual_portability.py \
  backend/tests/integration/test_playwright_surface.py -q
```

Commit:

```text
test: prove visual replay across reflow and DPR
```

## 13. Work package I — documentation from measured results

Do not update claims before the browser matrix passes.

Update only the documents affected by the implementation, including:

```text
README.md
REPORT.md
docs/architecture.md
docs/capability-and-replay.md
docs/discovery.md
docs/requirements.md
docs/verification.md
docs/README.md
```

Required explanation:

- The artifact stores meaning and image identity, not layout.
- The frame-local graph derives current relationships.
- Final mouse coordinates are ephemeral execution output.
- CSS-pixel capture makes DPR handling explicit.
- The exact tested matrix and negative cases.
- Legacy `3.0.0`/`3.1.0` behavior remains supported.
- Semantic-label or icon changes require rediscovery.
- The implementation does not prove native/mobile/arbitrary-app support.

Include a concise decision table covering DOM-first, coordinates, relative ROIs,
replay-time models, global templates, and the selected semantic graph approach.

Keep Mermaid diagrams consistent with the existing neutral connector styling and
night-mode-safe renderer-owned node colors. Do not use embedded raster screenshots
as architecture diagrams.

Commit:

```text
docs: document geometry-free visual replay
```

## 14. Complete validation sequence

At each package, run its narrow tests before committing. After all packages, run:

```bash
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run ruff format --check backend scripts pyproject.toml
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run ruff check backend scripts
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run mypy \
  backend/src backend/tests scripts/verify_evidence.py scripts/export_evidence.py \
  scripts/verify_evidence_bundles.py scripts/capture_discovery_run.py \
  scripts/capture_handoff_run.py scripts/capture_hard_failure_run.py \
  scripts/capture_recovery_run.py
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run pytest --ignore=backend/tests/integration -q
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run python scripts/verify_evidence_bundles.py evidence
npx --yes pnpm@10.15.1 typecheck
npx --yes pnpm@10.15.1 build
UV_CACHE_DIR=/tmp/replayforge-uv-cache uv run pytest backend/tests/integration -q
git diff --check
```

The environment has previously shown a FastAPI `TestClient` hang. If it still
occurs, isolate and report it exactly; do not weaken unrelated API tests or describe
the full suite as passing.

Before every commit:

```bash
git diff --check
git status --short
git diff --cached --name-only
```

Stage explicit files. Never use `git add .` or `git add -A`, because the two comment
files must remain untracked.

Before the final push, run the repository secret scan and inspect commit history for
credential patterns without printing any discovered value. Confirm that only the
intended branch and commits are pushed.

## 15. Required commit sequence

Use this order unless a package produces no changes:

```text
1. feat(schema): add geometry-free visual locator contract
2. feat(runtime): centralize visual grounding policy
3. feat(vision): resolve targets from frame-local layout graphs
4. feat(browser): normalize visual grounding to CSS pixels
5. feat(demo): add responsive table and card visual layouts
6. feat(discovery): publish semantic visual capabilities
7. feat(capability): publish geometry-free savings replay
8. test: prove visual replay across reflow and DPR
9. docs: document geometry-free visual replay
```

If a package changes code and its direct tests, commit those tests with that package.
The dedicated test commit is for the cross-system browser matrix, not for deferred
unit coverage.

After every commit:

1. Run `git status --short`.
2. Confirm only `comments.md` and `comments_2.md` remain untracked.
3. Record the commit hash in the implementation handoff notes.
4. Continue only if the just-committed narrow test set is green.

## 16. Definition of done

- Schema `1.3` cannot represent target geometry or target-specific visual tuning.
- Artifact `3.2.0` passes the recursive denylist test.
- Runtime builds its target from the current screenshot for every action.
- No current-frame target coordinate is persisted or reused.
- Table/card reflow and every tested DPR pass with identical outputs.
- Missing and ambiguous relations fail before action dispatch.
- Repeated icons are resolved within a semantic group, never through global first
  match or a saved ROI.
- New image matching has no artifact scale sweep.
- Replay performs no model call and uses no DOM locator for `3.2.0`.
- Existing `1.0`–`1.2` artifacts and tests remain green.
- Artifact, asset, and evidence hashes verify.
- Evidence masking and human-intervention behavior do not regress.
- Documentation states measured support and explicit limits accurately.
- Each important work package exists as a separate green commit.
- No secrets, comment files, caches, or generated runtime evidence are committed.

## 17. Final handoff format

The implementing model must report:

1. Each checkpoint commit hash and subject.
2. New artifact and asset hashes.
3. Exact global vision-policy values and generic calibration evidence.
4. Every positive and negative browser-matrix result.
5. Unit, type, lint, frontend, evidence, and integration results.
6. Any known failing or hanging test, with the exact command and cause.
7. Confirmation that `3.0.0` and `3.1.0` were not modified.
8. Confirmation that no artifact/runtime target geometry was persisted.
9. Confirmation that `comments.md` and `comments_2.md` were untouched and untracked.
