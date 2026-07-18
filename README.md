# Agent DriftGuard

[![License: MIT](https://img.shields.io/badge/License-MIT-167a5b.svg)](LICENSE)

[Watch the 2:22 demo video](https://youtu.be/oUA32nraAc8)

[View the OpenAI Build Week submission](https://devpost.com/software/agent-driftguard)

**A flight recorder and rollback gate for agent self-improvement.**

Agent DriftGuard prevents an agent from turning weak feedback into permanent
prompt or configuration changes. Every proposal must lock a falsifiable metric
prediction before candidate results exist. DriftGuard then checks
machine-verifiable outcomes, calibration, health, and anchor drift before it
keeps the change, pauses learning, or restores the last-known-good config.

It governs whether a proposed agent change is allowed to survive. It is not a
runtime tool-call guardrail or a generic observability dashboard.

OpenAI Build Week category: **Developer Tools**.

![Agent DriftGuard audit dashboard](docs/assets/dashboard-desktop.jpg)

## Run the Authoritative Experiment

Requirements: Python 3.10+ and a modern desktop browser. The deterministic
judge path needs no packages, API key, account, network, or build step.

This command launches a separate agent process for every task, snapshots the
active config, runs the baseline, locks the proposal manifest, applies the
candidate config, then runs frozen-control and candidate for every task in a
balanced randomized order before executing the final config decision:

```bash
python3 scripts/run_experiment.py \
  --proposal fixtures/orchestrated_proposal.json \
  --evalset fixtures/orchestrated_evalset.jsonl \
  --runner-command "python3 fixtures/orchestrated_command_agent.py" \
  --initialize-config fixtures/orchestrated_config.json \
  --out-dir artifacts/orchestrated-run \
  --overwrite
```

The post-lock control/candidate comparison produces `8/20 -> 20/20`, a paired
lift of `+60 pp`, and an exact one-sided paired-test p-value of `0.000244`.
Measured tasks are balanced `10/10` between control-first and candidate-first.
The default `n >= 20`, minimum lift, confidence, integrity, and critical-task
gates pass, so DriftGuard keeps the candidate config. Open
`artifacts/orchestrated-run/index.html` to inspect the self-contained report
and its reproduction command.

This proves the orchestration and policy-execution mechanism against a real
external process. The process is deterministic test software, not a language
model, so the result is not presented as measured GPT lift.

For a stronger experiment, pass a disjoint confirmation set with
`--holdout-evalset path/to/holdout.jsonl`. The initial `--evalset` then forms
the hypothesis, while the final paired gate uses only post-lock holdout
control/candidate outcomes.

## Run the Synthetic Demo

The original zero-dependency Build Week story remains available:

```bash
python3 scripts/run_demo.py
```

Then open `web/index.html`. The default scenario demonstrates a failed agent
rubric change and deterministic rollback. Run the verified-improvement branch
with `python3 scripts/run_demo.py --scenario keep`.

## Run GPT-5.6

The same authoritative path includes an optional OpenAI Responses adapter.
Set `OPENAI_API_KEY` in the environment, then run:

```bash
python3 scripts/run_experiment.py \
  --proposal fixtures/gpt56_proposal.json \
  --evalset fixtures/orchestrated_evalset.jsonl \
  --runner-command "python3 scripts/openai_responses_runner.py" \
  --initialize-config fixtures/gpt56_config.json \
  --out-dir artifacts/gpt56-run
```

The adapter defaults to `gpt-5.6-sol`, low reasoning effort, and `store=false`.
DriftGuard does not expose eval checks to the model process. This live run has
not been executed in this workspace because `OPENAI_API_KEY` is not configured;
no GPT-5.6 lift is claimed in the bundled evidence.

## Run a Compatible Live Model

An Anthropic Messages adapter can run the same protocol through a local model
gateway such as `cc-router`. With `DEEPSEEK_API_KEY` available in the
environment and the gateway listening on `127.0.0.1:8484`:

```bash
python3 scripts/run_experiment.py \
  --proposal fixtures/deepseek_holdout_replication_proposal.json \
  --evalset fixtures/deepseek_development_evalset.jsonl \
  --holdout-evalset fixtures/deepseek_holdout_replication_evalset.jsonl \
  --runner-command "python3 scripts/anthropic_messages_runner.py" \
  --initialize-config fixtures/deepseek_holdout_replication_config.json \
  --out-dir artifacts/deepseek-holdout-replication-run
```

The captured `artifacts/deepseek-holdout-replication-run/` is the strongest
live evidence.
It contains 98 unique `deepseek-v4-flash` responses: a 26-task development
baseline followed by 36 disjoint holdout tasks run once against control and
once against candidate. On the 32 measured holdout pairs, control scores
`0/32` and candidate scores `29/32`: `+90.62 pp`, 29 improvements, zero
regressions, exact `p=1.863e-09`, and a 95% paired-bootstrap interval of
`+78.12 pp` to `+100 pp`. Measured execution order is balanced `16/16`; all
eight control/candidate anchor verdicts hit; and all four evidence chains
verify, so DriftGuard keeps the candidate config.

The baseline prompt is a plausible generic operations assistant; it is not
instructed to return wrong measured-task answers. The candidate adds an
organization policy without embedding holdout examples. Its measured-task
policy and resulting config hash are unchanged from the first holdout run. The
three candidate misses are retained in the evidence. This proves replicated
task-instance holdout lift on real model execution, not production-domain
generalization or author blindness. The first holdout and earlier shared-task
runs remain under `artifacts/deepseek-holdout-run/` and
`artifacts/deepseek-live-run/`.

## Representative Private Holdout

The first audience-owned workflow benchmark uses completed Analyst Guide v8
short-term report reviews. Source DOCX files, human scores, disposition labels,
and prompts remain under git-ignored `private-evals/`; the public artifact is a
whitelist-only aggregate at
`artifacts/analyst-review-sealed/evidence-summary.json`.

The captured run contains 41 unique `deepseek-v4-flash` responses. Seven
earlier reviews form the development baseline; 12 later reports form a disjoint
paired holdout, with four additional anchors. On the measured reports, control
scores `2/12` and candidate scores `5/12`: `+25 pp`, three improvements, zero
regressions, exact `p=0.125`, and a 95% paired-bootstrap interval of `0 pp` to
`+50 pp`. All eight control/candidate anchor verdicts hit and all four evidence
chains verify.

The result does **not** pass the confidence gate. DriftGuard records
`pause_for_unproven_increment`, restores the baseline config, and pauses lesson
injection. The report-level decision requires both the quality band and
publication disposition to match. These 12 reports are now spent holdout
evidence and cannot be reused to tune a candidate presented as independent
confirmation.

## Analyze Existing Outputs

For outputs produced by another system, `scripts/verify_outputs.py` and
`scripts/run_rollout.py` provide an analysis-only import flow:

```bash
python3 scripts/verify_outputs.py \
  --evalset path/to/evalset.jsonl \
  --outputs path/to/baseline-outputs.jsonl \
  --phase baseline \
  --out artifacts/real-rollout-001/baseline.jsonl

python3 scripts/verify_outputs.py \
  --evalset path/to/evalset.jsonl \
  --outputs path/to/candidate-outputs.jsonl \
  --phase candidate \
  --out artifacts/real-rollout-001/candidate.jsonl

python3 scripts/run_rollout.py \
  --proposal path/to/proposal.json \
  --baseline artifacts/real-rollout-001/baseline.jsonl \
  --candidate artifacts/real-rollout-001/candidate.jsonl \
  --out-dir artifacts/real-rollout-001 \
  --min-metric-samples 20
```

Because candidate outputs already exist when this command starts, this path can
analyze evidence but cannot prove chronology or execute an external rollback.
The contracts and evidence levels are documented in
`docs/real-increment-protocol.md`.

## What the Evidence Proves

The authoritative deterministic, shared-task live-model, synthetic holdout,
and private representative experiments prove that
DriftGuard can:

1. Run baseline tasks before the candidate config exists.
2. Lock the evalset, baseline outputs, proposal, and config fingerprints.
3. Apply a candidate through a durable config adapter before confirmation
   processes start.
4. Run post-lock frozen-control and candidate branches in a balanced randomized
   order, then pair outcomes by `task_id`.
5. Enforce sample/lift/confidence/critical-task gates and keep or restore the
   real config file.
6. Separate health/calibration evidence, paired comparison evidence, proposal
   lifecycle, and experiment receipts into four SHA-256 event chains.
7. Keep private task content and human labels out of a sealed aggregate while
   retaining hashes, effect statistics, response counts, chain heads, and the
   actual restore receipt.

The deterministic and synthetic DeepSeek runs prove the mechanism and a large
task-instance effect. The private report-review run moves to a real audience,
real work products, and completed human labels, but its `n=12` lift fails the
statistical gate. A production benefit claim therefore still requires a fresh,
larger representative holdout or shadow rollout; the current result proves
that DriftGuard refuses to promote an attractive but unverified change.

## Architecture

```text
fixtures/                 Synthetic baseline and candidate outcomes
scripts/run_demo.py       Experiment orchestration and report generation
scripts/run_experiment.py Authoritative execute/apply/keep-or-restore CLI
scripts/openai_responses_runner.py  Optional GPT-5.6 process adapter
scripts/anthropic_messages_runner.py  Anthropic-compatible process adapter
scripts/build_private_review_evalset.py  Private DOCX plus human-label builder
scripts/export_sealed_evidence.py  Whitelist-only private-run aggregate
scripts/verify_outputs.py Evalset verifier for real agent outputs
scripts/run_rollout.py    Post-hoc evaluator for baseline/candidate JSONL
src/feedback_kit/         Self-contained feedback-loop kernel
web/                      Zero-dependency audit dashboard
artifacts/                Inspectable generated evidence
private-evals/             Git-ignored representative source and full runs
tests/                    Kernel, integrity, and end-to-end checks
```

The core is a deterministic Python standard-library package. Model output can
suggest a proposal, but it cannot decide whether its own proposal passes. Soft
LLM verdicts are recorded with capped confidence and excluded from calibration
and lesson distillation.

## Verify It

```bash
python3 tests/test_feedback_kit.py
python3 tests/test_demo.py
python3 tests/test_rollout.py
python3 tests/test_evalset.py
python3 tests/test_experiment.py
python3 tests/test_openai_runner.py
python3 tests/test_anthropic_runner.py
python3 tests/test_shadow.py
```

The end-to-end tests cover keep and rollback, baseline/proposal/apply/candidate
order, paired confidence, critical regressions, candidate-process failure,
partial config-apply failure, durable config restoration, tamper detection,
eval-check isolation, self-contained viewer output, and the offline OpenAI
Responses and Anthropic Messages request contracts.

Supported platforms: macOS, Linux, and Windows with Python 3.10+. Event writes
are lock-protected on Unix; the local demo is single-writer on Windows.

## Built With Codex and GPT-5.6

Codex was used during Build Week to audit the pre-existing kernel, find a
critical chronology flaw in the first demo, design the product boundary,
implement the self-contained experiment runner and tamper-evident event log,
and build the regression tests. A GPT-5.6 Codex worker implemented the audit
dashboard from a field-level data contract; the main Codex task integrated and
verified it. The human product decisions were to keep the scope local and
deterministic, use synthetic evidence, and optimize the three-minute story
around one inspectable rollback.

The proposal fixture itself is synthetic; the project does not pretend it is a
captured model response. DriftGuard's value is the deterministic boundary
around model-suggested changes.

See `docs/build-provenance.md` for the explicit pre-Build-Week/new-work split.
The required `/feedback` Codex Session ID was generated from the main project
task and entered in the Devpost submission form.

## Build Week Scope

The general `feedback-kit` kernel existed before the submission period. It is
bundled under `src/feedback_kit/` only so this repository is independently
runnable; pre-existing work is not presented as Build Week output. The new
submission-period work is the DriftGuard product: chronological experiment
orchestration, hash-chained evidence and fail-closed integrity gating, dual
rollback/keep scenarios, the audit dashboard, end-to-end tests, and submission
materials. The external-process runner, config adapter, paired statistical gate,
and GPT-5.6 Responses adapter were added after submission and are listed
separately in `docs/build-provenance.md`.
