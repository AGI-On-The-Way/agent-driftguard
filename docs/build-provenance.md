# Build Week Provenance

This document distinguishes pre-existing work from functionality created
during the OpenAI Build Week submission period, as required by the official
rules for existing projects.

## Before the Submission Period

The shared `feedback-kit` kernel already contained the general ledger,
verdict, calibration, lesson, critic, proposal, diversity, and health
primitives. The snapshot under `src/feedback_kit/` makes this submission
self-contained. Those pre-existing primitives are supporting infrastructure,
not claimed as Build Week output.

## Added During Build Week

- A standalone Agent DriftGuard workspace and one-command experiment runner.
- Correct experimental chronology: baseline evidence, locked proposal,
  candidate evidence, then verification.
- Phase-specific baseline and candidate measurement rather than a mixed metric.
- Sequence-numbered SHA-256 hash chains for ledger and proposal evidence.
- Fail-closed integrity checks in the final decision path.
- Deterministic rollback and keep scenarios using the same gates.
- A zero-build, local audit dashboard for judges and the demo video.
- Tamper-detection, chronology, rollback, keep, and clean-run verification.
- README, Devpost copy, video script, and submission checklist.

## Codex Collaboration

Codex helped inspect the existing system, translate the judging criteria into
an implementation plan, and implement and test the Build Week product layer.
During review, a Codex agent found that the first draft wrote proposal events
after all outcome reviews; this contradicted the core "lock before results"
claim. The runner and fixtures were then changed so the raw timestamps prove
the correct order.

A GPT-5.6 Codex worker owned the dashboard HTML, CSS, and JavaScript from a
strict data contract. The main Codex task owned the Python engine, evidence
format, integration, documentation, and verification. DeepSeek V4 Pro was used
as an independent judge-style adversarial reviewer; its highest-priority
findings were used to make the repository self-contained and add a positive
keep path.

## Submission Evidence

- Public demo video: `https://youtu.be/oUA32nraAc8`
- Public MIT-licensed repository with dated commit history.
- `/feedback` Session ID entered in the Devpost form.
- Developer Tools installation and test instructions entered for judges.
- Submitted to OpenAI Build Week on July 17, 2026:
  `https://devpost.com/software/agent-driftguard`
