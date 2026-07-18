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

## Added After the Build Week Submission

- Generic rollout evaluator under `src/feedback_kit/rollout.py`.
- Real-data CLI entry point under `scripts/run_rollout.py`.
- Evalset verifier under `src/feedback_kit/evalset.py` and
  `scripts/verify_outputs.py`, so real agent outputs can be judged by machine
  checks instead of hand-written `actual_pass` rows.
- Configurable sample gate for real rollout decisions, so small apparent lifts
  pause for more evidence instead of becoming durable improvements.
- Domain-neutral real increment protocol under
  `docs/real-increment-protocol.md`.
- Generic rollout and evalset tests covering rollback, keep, CLI execution,
  chronology, verifier-generated outcome rows, and refusal to overwrite
  existing evidence.
- Authoritative `ExperimentRunner` lifecycle that owns baseline execution,
  manifest locking, config apply, candidate execution, and final policy action.
- `CommandAgentRunner` JSON process contract and durable JSON config adapter,
  including apply/keep/restore receipts and fail-safe restoration after
  candidate or partial-apply failures.
- Exact paired binary lift gate with fixed task pairing, default `n >= 20`,
  minimum lift, critical-task regression blocking, and deterministic paired
  bootstrap interval.
- Post-lock frozen-control/candidate confirmation with a manifest-derived,
  per-task balanced execution order; the effect gate no longer relies on the
  earlier hypothesis-forming baseline as its statistical control.
- A third hash-chained experiment log that proves ordering independently of the
  outcome ledger and proposal log.
- An independent comparison ledger for scheduled control/candidate verdicts,
  keeping paired-effect evidence separate from the health/calibration ledger.
- One-command external-process judge fixture and self-contained per-experiment
  dashboard showing `8/20 -> 20/20`, `+60 pp`, and `p=0.000244`.
- Optional `gpt-5.6-sol` OpenAI Responses process adapter and offline request
  contract tests. No live GPT-5.6 lift is claimed because the local environment
  did not have `OPENAI_API_KEY` configured.
- Anthropic-compatible Messages process adapter with offline leakage/contract
  tests and a captured 66-call `deepseek-v4-flash` experiment. The synthetic
  contract evalset measured `8/20 -> 20/20`, `+60 pp`, exact `p=0.000244`, and
  valid independent health, comparison, proposal, and experiment chains.
- Optional independent holdout confirmation: disjoint development/confirmation
  task IDs, both evalset hashes in the locked manifest, holdout-only paired
  effect and health scopes, and fail-closed overlap rejection.
- A first-pass 98-call `deepseek-v4-flash` holdout run using a plausible generic
  baseline rather than a known-wrong policy. On 32 measured holdout pairs it
  records `0/32 -> 30/32`, `+93.75 pp`, 30 improvements, zero regressions,
  exact `p=9.313e-10`, and two retained candidate errors.
- Artifact QA exposed that the first holdout's control prompt did not define
  the ping anchor. The run was retained rather than replaced. A fresh
  replication holdout fixed only the shared anchor contract and preserved the
  measured-task candidate policy and config hash.
- The 98-call replication records `0/32 -> 29/32`, `+90.62 pp`, 29
  improvements, zero regressions, exact `p=1.863e-09`, a `+78.12 pp` to
  `+100 pp` interval, three retained candidate errors, and 8/8 stable
  control/candidate anchor verdicts.
- Responsive browser verification of the authoritative result viewer at
  desktop and 390 px widths, including overflow and console-error checks.
- Separate development and paired-confirmation sample thresholds, so a smaller
  private development cohort cannot silently weaken the final effect gate.
- Standard-library private DOCX plus human-label benchmark builder with opaque
  task IDs, duplicate-source rejection, and chronological cohort support.
- Whitelist-only sealed evidence export that excludes source text, labels,
  prompts, task outputs, notes, schedules, private paths, and credentials.
- A 41-call representative Analyst Guide v8 holdout using completed human
  decisions. The candidate moves `2/12 -> 5/12` with zero regressions but fails
  confidence (`p=0.125`), causing a recorded baseline restore rather than an
  inflated improvement claim.

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
