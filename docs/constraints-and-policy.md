# Constraints and policy

ReplayForge uses layered bounds. The caller may choose a value only inside the domain envelope;
reviewed configuration limits model and vision work; capability artifacts bound individual replay
steps; policy can only remove authority.

```text
platform ceiling
  ∩ application registration
  ∩ tenant + capability policy
  ∩ invocation policy
  → effective permission

request budget ∩ provider budget ∩ step budget
  → first exhausted boundary stops the run
```

## Execution bounds

| Boundary | Current bound | Owner | Failure controlled |
|---|---:|---|---|
| Discovery steps | `1..50`; default `20` | Domain constant; caller may narrow | Unbounded model/action loop |
| Discovery wall budget | `10..600s`; default `120s` | Domain constant; caller may narrow | Abandoned or excessively long discovery |
| Repeated observation/action | `2` each | Discovery engine | Model loop with no meaningful progress |
| Discovery confidence | `0.6` minimum | Discovery engine | Acting on an uncertain proposal |
| Model calls | Maximum `20` per run | Reviewed model policy | Unbounded cost and provider traffic |
| Model response | `1,200` output tokens; `30s` per call | Reviewed model policy | Cost growth and stuck provider calls |
| Provider frame | `1.5 MiB` | Reviewed model policy | Oversized remote payload |
| Replay step | `100ms..120s`; default `10s` | Immutable capability artifact | Permanently waiting on stale UI state |
| Retry | `1..5` attempts; backoff entries `0..30s` | Artifact schema | Retry storms |
| Recovery | At most `3` uses per declared recovery | Artifact schema | Cyclic recovery |
| Human lease | `30s`, renewed by heartbeat | Lease service | Abandoned exclusive control |

The limits are cumulative, not interchangeable. For example, a request may allow 50 discovery
steps while the configured provider stops after 20 model calls. Contract planning consumes one
model call. The first exhausted budget produces a typed stop; a larger request cannot enlarge the
reviewed provider ceiling.

Timeout enforcement is cooperative at operation boundaries. A provider or browser operation has
its own timeout, so the discovery wall budget is checked before the next iteration rather than
interrupting code mid-operation.

## Fail-closed authority

| Constraint | Rule | Override behavior |
|---|---|---|
| Origin and route | Credential-free HTTP(S) origin plus an allowlisted normalized route | Every policy layer must allow it |
| Action type | Must appear in the effective action intersection | A capability cannot add an application-forbidden action |
| Risk | Lowest layer ceiling wins; independently inferred risk may raise the declaration | Callers cannot lower observed risk |
| Irreversible action | Always denied | No override in this runtime |
| Sensitive action | Pauses for same-session human control | Automation cannot approve itself |
| Credential/secret field | Forbidden | Policy layers may forbid more classifications, never fewer |
| Retry | Error must be named, recoverable, and proven effect-absent | Artifacts cannot disable the effect-absence requirement |
| Visual match | Exactly one candidate must clear central confidence and uniqueness thresholds | Artifacts may narrow candidate intent, not visual budgets |
| Human input | Current operator, lease version, frame sequence, and viewport must agree | Stale input is rejected |

Malformed origins—including credentials, paths, query strings, fragments, and invalid ports—are
policy denials rather than runtime errors.

## Storage and payload bounds

| Object | Bound | Reason |
|---|---:|---|
| Capability YAML | `1 MB` | Bound parsing and startup work |
| Visual capability asset | `512 KB` | Assets are small canonical PNG signatures, not screenshots |
| Application registry | `1 MB` | Bound trusted startup configuration |
| Evidence event/result/manifest | `2 MB` each | Bound JSON parsing and review |
| Evidence attachment | `20 MB` | Permit masked PNG/trace evidence without arbitrary blobs |
| Run events / attachments | `10,000` / `100` | Keep manifests finite |
| Artifact validation request | `1 MB` | Match the capability publication envelope |
| Human text input | `1,000` characters | Bound an interactive control command; text is not retained |
| Human viewport | Maximum `8192×8192` envelope | Reject unreasonable payloads before matching the live frame |

The visual policy additionally caps a frame at eight million pixels, segmentation at 500,000
analysis pixels and 2,000 components, and grounding at ten seconds. The thresholds in
`config/vision-policy.yaml` are a reviewed deterministic profile validated by the viewport/DPR
matrix; they are not claimed as universal computer-vision constants.

## Decisions

| Option | Decision | Why |
|---|---|---|
| One timeout for every operation | Rejected | Provider, browser step, discovery run, and lease expiry address different failures |
| Environment variables for every number | Rejected | Safety and cost ceilings must remain reviewed, versioned inputs |
| Capability-controlled global budgets | Rejected | A discovered artifact must not enlarge model, vision, policy, or retry-safety ceilings |
| Layered domain/config/artifact bounds | **Chosen** | Keeps ownership explicit while allowing task-specific waiting and caller-side narrowing |
| Automatic adaptive retries | Rejected | Replay retries only named errors with a known absent effect |

Values should change only with a failing workload or threat-model reason and an accompanying
boundary test. Production deployment may choose different reviewed profiles; it should not remove
the invariant checks.
