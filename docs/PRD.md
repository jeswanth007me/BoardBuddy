# BoardBuddy — Product Requirements Document

**Status:** Draft for review · **Version:** 0.1 · **Date:** 2026-10-23

## Product summary

BoardBuddy is an AI-guided project-building assistant intended to help people move from an idea or available components to a project they can understand, assemble, test, and improve. The first demonstrator focuses on beginner electronics and ESP32 projects. The long-term direction is a domain-extensible assistant for software, AI/ML, electronics, IoT, robotics, and other practical project work.

The current public prototype is narrower: a local-first Flask app for authenticated component-photo review using a local Ollama model and a simple ESP32 temperature-monitor checklist. Planned capabilities below are not claims about current implementation.

## Problem and users

Beginners jump between tutorials, datasheets, code examples, wiring diagrams, and troubleshooting advice. It can be difficult to know what to do next, why a step matters, whether a part was identified correctly, and what evidence shows that a project works.

Primary users: students and beginners, hackathon builders, educators/mentors, and independent makers.

## Product principles

1. Teach, do not just generate: explain what each step does and why.
2. Communicate uncertainty; distinguish suggestions from verified results.
3. Local-first by default. Any future cloud mode must be explicit and opt-in.
4. Human correction is normal; users can edit suggestions and quantities.
5. Visual recognition and design checks do not prove physical wiring is safe.
6. Validate hard assumptions on modest hardware before choosing dependencies.
7. Evolve the current Flask prototype incrementally rather than rewriting without evidence.

## Current baseline (public repository)

- Flask web app with authenticated routes.
- Local Ollama integration configured for `gemma3:4b`.
- Image upload/camera capture flow with manual upload fallback.
- AI component suggestions with evidence and uncertainty text.
- User review/correction before checklist use.
- Narrow ESP32 temperature-monitor checklist.
- Authentication, CSRF checks, upload validation, and mocked unit tests.

Known limitations include in-memory sessions/state, a narrow hardcoded project flow, no durable project workspace, no canonical electrical netlist, no physical wiring verification, and no validated pin-level visual localization. Reconcile the local checkout before treating this as a complete inventory.

## MVP goals

A user can start the app locally, authenticate using environment-supplied secrets, upload/capture a component photo, review and correct AI suggestions, compare reviewed parts with the reference checklist, follow safety reminders, and distinguish AI output from user confirmation or test evidence.

## Out of scope for the first demo

- Automatic pin/wire verification from a photo.
- Claims that AI or software checks prove a physical circuit is safe.
- Unrestricted autonomous shell/code execution.
- Public internet exposure or unreviewed remote access.
- A complete multi-domain project generator.
- Guaranteed real-time performance on every phone or laptop.

## User journey

1. Start the local app and open camera/upload.
2. Capture a clear component image.
3. Receive candidate parts with evidence and uncertainty.
4. Review, correct, and confirm inventory.
5. Compare inventory with the current checklist.
6. Follow component-specific instructions and independently check physical wiring.
7. Record what worked, failed, and what comes next.

## Requirements and acceptance evidence

| ID | Requirement | Acceptance evidence |
|---|---|---|
| PR-01 | Reproducible local startup | Clean setup walkthrough succeeds |
| PR-02 | Protected features require authentication | Route-access tests |
| PR-03 | Uploads are size/type checked | Valid and invalid upload tests |
| PR-04 | Suggestions show evidence/uncertainty | Mocked-response UI/API test |
| PR-05 | Users can correct suggestions | Editing/manual-add tests |
| PR-06 | Inference is never labelled electrical verification | Copy review and regression tests |
| PR-07 | Pre-wiring/power safety guidance is visible | Documentation and user-flow review |
| PR-08 | Shipped and planned features are distinguished | README/PRD/SRS review |
| PR-09 | External image transmission is not silently introduced | Configuration/network-path review |
| PR-10 | Demo is repeatable without exposing secrets | Setup and secret-handling review |

## Risks

- Wrong identification → uncertainty labels, user review, manual correction.
- False wiring confidence → separate visual suggestions from physical tests.
- Hardware variation → check exact board/sensor pinouts and datasheets.
- Local resource limits → benchmark models; avoid unnecessary continuous inference.
- Privacy leakage → preserve local-only behavior unless external inference is explicit and reviewed.
- Scope growth → validate one capability at a time.
- Unsupported claims → keep evidence for tests and performance numbers.

## Roadmap outline

1. Reconcile local tree with public GitHub and establish baseline.
2. Review tests/security-sensitive code before execution.
3. Fix misleading status language and improve inventory quantity/edit behavior.
4. Validate camera and local inference on the target laptop.
5. Test visual localization separately before building overlays.
6. Design data model/migrations before adding persistence.
7. Expand tutoring, documents, or diagnostics only after MVP stability.

## Open decisions

- Supported reference board/sensor and Python/Windows versions.
- Evidence standard for suggested, user-attested, software-checked, and physically tested states.
- Dependency/model/dataset licence policy.
- Measured thresholds for camera latency and model responsiveness.

This document describes intent, not proof of implementation. Do not mark requirements complete without reproducible evidence.
