# BoardBuddy — Software Requirements Specification

**Status:** Draft; local baseline reconciliation required · **Version:** 0.1 · **Date:** 2026-10-23

## 1. Scope

This SRS describes expected behavior for the current local-first Flask prototype and controlled future increments. The current product reviews component photos and guides a narrow ESP32 temperature-monitor build. Persistent workspaces, domain packs, and physical diagnostics are future work unless verified in code.

## 2. System context

- **Browser:** server-rendered HTML/Jinja and JavaScript for home, camera, and monitoring flows.
- **Flask:** authentication, sessions, request validation, routes, and orchestration.
- **Model service:** Ollama at `http://localhost:11434`; model name uses `BOARDBUDDY_MODEL`, default `gemma3:4b`.
- **Environment:** documented primarily for Windows 10/11, Python, Flask, requests, and Ollama.

The checklist is not an electrical simulator or measurement system. Some state is in memory and may be cleared on restart. LAN phone-camera use requires trusted HTTPS in common browsers; the README's private-network procedure is not production deployment guidance.

## 3. User classes

- Unauthenticated visitor: login flow only.
- Authenticated builder: protected pages and photo-review workflow.
- Maintainer: configure secrets, install dependencies, run tests, and review code/logs.

## 4. Functional requirements

Priority: P0 = initial demo, P1 = next iteration, P2 = future capability.

| ID | Priority | Requirement |
|---|---|---|
| FR-001 | P0 | Startup fails if required secrets are absent or below documented minimum lengths. |
| FR-002 | P0 | Protected pages/APIs reject unauthenticated requests appropriately. |
| FR-003 | P0 | State-changing requests validate CSRF protection where applicable. |
| FR-004 | P0 | Image analysis enforces upload limits and validates supported image signatures/types. |
| FR-005 | P0 | Current local mode sends image analysis only to local Ollama. |
| FR-006 | P0 | Model results are suggestions, not ground truth; communicate evidence and uncertainty when available. |
| FR-007 | P0 | Users can review/correct suggestions before relying on the checklist. |
| FR-008 | P0 | UI never claims physical verification solely from an image or inventory entry. |
| FR-009 | P0 | Pre-wiring and pre-power safety reminders are provided. |
| FR-010 | P0 | Setup, secrets, model prerequisites, tests, and limitations are documented. |
| FR-011 | P1 | Inventory represents quantities and distinguishes similar part variants. |
| FR-012 | P1 | Project/task/inventory state persists after a schema and migration plan are approved. |
| FR-013 | P1 | Structured circuit model records components, pins, nets, and provenance. |
| FR-014 | P1 | Design-level rules do not claim to inspect physical hardware. |
| FR-015 | P1 | Evidence records identify AI inference, user attestation, software validation, or physical test. |
| FR-016 | P2 | Import tutorials/datasheets with traceable citations. |
| FR-017 | P2 | Add voice tutoring after interaction/accessibility requirements are defined. |
| FR-018 | P2 | Future build tools use explicit scope, timeouts, logs, and a threat model. |
| FR-019 | P2 | Future cloud inference requires explicit choice and disclosure of data/provider. |

## 5. Non-functional requirements

### Security
- Never commit secrets or put them in public logs.
- Use CSRF protection and safe upload handling.
- Bind to loopback by default; do not expose Flask or Ollama through a public tunnel.
- Future subprocess execution needs allowlisted commands, scoped directories, timeouts, resource limits where available, and audit logs.
- Review and test remote access before supporting it.

### Privacy
- Local mode must not silently add external image providers.
- Document and review any new network destination.
- Do not promise perfect automatic PII removal from arbitrary photos.
- Show camera-active state and allow capture to stop.

### Reliability
- Invalid inputs and unavailable model services fail safely with useful errors.
- Test error cases as well as successful flows.

### Performance
- No latency/FPS target is accepted until measured on the target laptop.
- Record cold/warm latency, peak RAM, CPU load, image dimensions, and model/runtime versions.
- Avoid continuous inference if it makes the laptop unresponsive.

### Usability/maintainability
- Make uncertainty and required user actions visible.
- Never use success styling to imply physical verification from inference alone.
- Preserve Flask until evidence justifies a change.
- Review code, model weights, and dataset licences before redistribution.
- Label mocked tests and keep separate integration/benchmark evidence.

## 6. Candidate data concepts

These are proposals, not claims that a database schema exists: Project; Component Observation; User-confirmed Inventory Item; Task/Step; Evidence Record; Decision/Change Record. Define migrations, retention, deletion, and image storage before implementing persistence.

## 7. Interfaces

- Browser camera access on a LAN generally requires trusted HTTPS.
- Current Ollama endpoint is local `http://localhost:11434`; do not expose it publicly.
- Keep secrets, certificates, databases, uploads, and generated output out of Git unless intentionally reviewed.
- Future USB serial/I²C/ADC tests must name what is measured; a response proves only that specific test's result.

## 8. Evidence/status model

1. **Suggested/Inferred:** AI or computer vision output; not independently confirmed.
2. **User-attested:** human-entered/confirmed; not independently measured.
3. **Software-checked:** rule/test passed against a digital model/input; not proof of physical correctness.
4. **Physically tested:** defined physical test produced expected evidence; name test and limitations.
5. **Failed/Inconclusive:** test failed, evidence is missing, or result is uninterpretable.

Do not promote a wire/pin to physically tested solely because a model sees it or firmware returns plausible data.

## 9. Verification

- Unit tests: validation, authentication, CSRF, upload checks, parsing, status transitions.
- API tests: access control, valid/invalid uploads, model unavailable/timeouts, malformed outputs.
- UI checks: correction flow, uncertainty labels, safety copy, camera stop behavior.
- Integration tests: real Ollama/camera only in explicitly configured runs.
- Hardware tests: board, sensor, wiring, instrument/telemetry, tolerance, limitations.
- Performance tests: reviewed reproducible scripts outside the protected working tree.

Passing mocked tests does not prove model quality, streaming performance, or electrical behavior.

## 10. Next-release acceptance

- Setup works on a supported machine.
- Tests pass after local-tree reconciliation and side-effect review.
- No regression in authentication, CSRF, upload validation, or local-only inference.
- UI/docs distinguish inference from physical verification.
- Reference checklist and pre-power inspection guidance exist.
- Known limitations and test-coverage gaps are published.

## 11. Open issues

Reconcile local checkout; define supported Python/Windows versions and exact reference hardware; review exact licence terms; measure local inference/camera behavior; design storage/migrations before project memory.
