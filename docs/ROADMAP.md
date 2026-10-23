# BoardBuddy — Roadmap to the December 12–13, 2026 Hackathon

**Planning window:** 2026-10-23 to 2026-12-13 · **Status:** Proposed; adjust to event criteria and verified repo state.

## Goal

Deliver a reliable, repeatable demo of one end-to-end workflow with a clear problem statement, honest limitations, documentation, and a visible next-step plan. Do not attempt to build the entire universal assistant before the event.

## Phase 0 — Baseline and docs (Oct 23–30)

- Publish PRD/SRS, README links, architecture overview, and roadmap.
- Reconcile local working tree with public GitHub.
- Review tests before running them; understand mocks and side effects.
- Record setup, dependencies, model prerequisites, and known failures.
- Keep the protected original clone and backup untouched.

**Exit:** reviewers understand the product and can reproduce the baseline; implemented and planned features are separated.

## Phase 1 — Demo reliability (Oct 31–Nov 9)

- Rehearse startup → image review → checklist.
- Fix only confirmed high-impact bugs, especially misleading status language or setup failures.
- Improve errors for missing Ollama/model, invalid images, and timeouts.
- Keep a short demo script and fallback image.

**Exit:** the same demo can be repeated several times without manual code edits.

## Phase 2 — One differentiator (Nov 10–23)

Choose one based on evidence: (a) transparent component review/correction, (b) phone-to-laptop camera demo on trusted HTTPS, or (c) structured reference checklist with evidence/provenance. Do not attempt all three if time is tight. Do not claim pin-level tracking without an experiment.

**Exit:** one differentiator works in a repeatable test and improves the demo.

## Phase 3 — Polish (Nov 24–Dec 5)

- Run tests after reviewing side effects; test success and one failure path.
- Ensure secrets, local databases, certificates, and private images are not committed.
- Refine README, architecture, and demo narrative.
- Record limitations and next work.

**Exit:** clean demo environment and concise explanation of design, safety, privacy, and evidence.

## Phase 4 — Final rehearsal (Dec 6–11)

- Freeze scope; no risky feature additions.
- Run the exact demo from a fresh terminal using documented steps.
- Prepare fallback screenshots/recording only as backup; be honest about what is live.
- Practice a 3–5 minute demo and likely technical questions.
- Confirm event submission/presentation requirements.

## Weekly time budget

Aim for **4–6 focused hours per week**, increasing only when coursework permits. One pattern: two 60–90 minute build/review sessions and one short docs/demo session. During exam-heavy periods, focus on maintenance and rehearsal.

## Defer unless required

- Full multi-domain agent framework.
- Unrestricted autonomous coding/shell execution.
- Pin-level overlays without measured accuracy.
- Large model downloads without RAM/disk benchmark.
- Database migration without schema and backup plan.
- Framework rewrite, public deployment, or cloud inference integration.

## Demo narrative

1. Explain the beginner problem: scattered instructions and uncertainty.
2. Start the local app.
3. Capture/upload a component photo.
4. Show candidate parts and uncertainty.
5. Correct and confirm inventory.
6. Walk through the ESP32 temperature-monitor checklist.
7. Explain safety boundaries and what has not been physically verified.
8. Close with an experiment-driven roadmap.

## Weekly review

- What works now that did not work last week, and what proves it?
- Did we introduce a security, privacy, safety, or reproducibility regression?
- Is the demo more reliable, or did we only add complexity?
- What should be cut to protect the December deadline?
