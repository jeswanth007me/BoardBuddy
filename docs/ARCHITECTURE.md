# BoardBuddy — Architecture Overview

**Status:** Proposed target architecture; not all components exist today · **Date:** 2026-10-23

## Current prototype

The public prototype is a Flask app with server-rendered templates and browser JavaScript. It authenticates the user, accepts a photo/camera capture, requests analysis from local Ollama, and supports reviewing component suggestions for a simple ESP32 temperature-monitor checklist. Recheck the local working tree before treating this as a complete implementation inventory. Some state is in memory; the checklist is not a canonical electrical netlist.

## Proposed evolution

Keep a **modular Flask monolith** for the near term. Split responsibilities when interfaces are clear; avoid a framework rewrite before measured evidence requires one.

```text
Phone/Desktop Browser
  ├─ camera capture or image upload
  └─ review, correction, guided checklist
              |
              v
Flask Application
  ├─ authentication, CSRF, upload validation
  ├─ project / inventory / task services (evolving)
  ├─ tutor orchestration (future)
  ├─ evidence/status service (future)
  └─ document retrieval (future)
              |
              v
Local services/storage
  ├─ Ollama for local model inference
  ├─ SQLite for future durable projects/tasks/evidence
  ├─ curated references and user-provided documents
  └─ optional hardware interfaces after separate safety review
```

## Boundaries

- **Presentation:** displays observations/actions; it must not decide that hardware is physically verified.
- **Application services:** validate inputs, coordinate workflow, enforce permissions.
- **Domain model:** represent projects, parts, quantities, steps, pins/nets, and evidence independently of UI text.
- **Inference adapter:** applies timeouts, validates response shape, and labels model output as an untrusted suggestion.
- **Persistence:** proposed SQLite with schema, migrations, backup/recovery, and retention behavior.
- **Evidence service:** distinguishes inference, user attestation, software checks, and physical tests.
- **Hardware adapters:** future device-specific tests with explicit expected outputs; no generic response proves an unrelated connection.
- **Tool execution:** future deny-by-default allowlisted tools, scoped workspace, limits, audit logs, and explicit permissions. User approval alone is not a sandbox.

## Core data concepts

Project; component type/variant and quantity; observation with source/model version, evidence, uncertainty and review state; task/step with prerequisites and safety notes; circuit model with components/pins/nets and design-level checks; evidence record with method, inputs, timestamp, result and limitations; decision/change record. These are proposals, not claims that the entities exist today.

## Local-first deployment

- Default to loopback and local Ollama.
- Keep secrets/certificates outside the repository.
- Do not expose Ollama or the development server publicly.
- LAN phone camera access needs browser-trusted HTTPS; plain LAN HTTP is not reliable.
- Future cloud inference must be opt-in and transparent about data transmission and provider terms.

## Key decisions

1. Keep Flask until measured limitations justify change.
2. Camera recognition cannot independently prove wiring or electrical safety.
3. Build a structured circuit model before advanced design-rule checks.
4. Treat performance numbers as targets until measured on the actual laptop.
5. Validate camera transport and hole localization separately before overlays.
6. Design schema/migration/recovery before persistence.
7. Review code, model weights, datasets, and redistribution terms separately.

## Suggested future module boundaries

`auth`, `projects`, `inventory`, `vision`, `circuits`, `tutor`, `evidence`, `documents`, `hardware`, and `tool_runner`. Introduce modules incrementally alongside tests; do not create them only to match this diagram.
