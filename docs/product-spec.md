# Product Spec

## Problem

Agent builders increasingly ask models to improve prompts, configs, routing, or
tools based on feedback. Without a disciplined feedback loop, those improvements
can drift: soft judgments teach the agent the wrong lesson, small samples look
like signal, and bad config changes become sticky.

## User

Developer building or operating AI agents with Codex/GPT-5.6.

## Promise

Before an agent can learn from feedback, DriftGuard forces the loop to answer:

- What was predicted before the outcome was known?
- Was the outcome machine-verifiable?
- Is there enough sample size to learn from it?
- Did calibration improve or degrade?
- Can we roll back the change if it failed?

## MVP

One local demo with synthetic data:

- `scripts/run_demo.py`
- sample agent runs under `fixtures/`
- append-only ledger output
- calibration and reliability report
- drift report
- rollback decision
- README path that judges can run

## Core Objects

| Object | Meaning |
|---|---|
| Proposal | A model-suggested prompt/config/tool change with predicted delta |
| Ledger record | A registered prediction before the result is known |
| Verdict | Outcome review; machine-verifiable or soft |
| Lesson | A distilled rule that passes sample and confidence gates |
| Rollback decision | Deterministic decision to keep, pause, or revert a change |

## Demo Narrative

1. Codex proposes a change to an agent rubric.
2. The proposal locks its expected improvement as a falsifiable prediction.
3. DriftGuard records the prediction in a hash-chained append-only ledger.
4. Synthetic follow-up runs produce mixed outcomes.
5. DriftGuard blocks the lesson because the evidence shows drift or insufficient confidence.
6. The report shows why the rollback/pause happened.

## Success Criteria

- Demo runs locally in under 60 seconds.
- No external secrets required.
- Kernel tests pass.
- Report explains the decision without hand-wavy LLM scoring.
- Video can show the core loop in under 3 minutes.
