# ReplayForge

A focused implementation of a computer-use system that uses an LLM to discover a workflow on a real UI, saves that workflow as a typed and reviewable capability, and replays it deterministically without an LLM in the decision loop.

## Project status

Initial repository setup. The implementation will provide one complete vertical slice:

1. Accept a natural-language goal and target application.
2. Run an LLM-driven observe-decide-act discovery loop against the live UI.
3. Save a versioned, parameterized capability artifact.
4. Replay that artifact deterministically with typed outcomes and evidence.
5. Pause and transfer the same live session to a human operator when intervention is required.

The design prioritizes robust control targeting, explicit runtime-error handling, allowlisted actions, redaction of sensitive values, and a clean surface-adapter seam for legacy web and desktop applications.

## Setup and usage

Setup instructions, configuration, offline mode, and the exact discovery and replay demo commands will be added with the implementation.

## Deliverables

- Source code and tests
- [`REPORT.md`](REPORT.md), covering the required design decisions and trade-offs
- `evidence/`, containing a saved capability and logs from genuine discovery and replay runs
