# Devpost Update Draft

This is the final proposed update to the July 17 submission. It has not been published.
The exact submitted copy remains in `docs/devpost-draft.md`.

## Short Description

Agent DriftGuard is an execution and rollback gate for agent self-improvement.
It runs a baseline, locks a falsifiable proposal and experiment manifest,
applies the candidate config, reruns the same tasks through an external agent
process, and keeps or restores the real config using paired statistical and
integrity gates.

## What It Does

DriftGuard owns the experiment rather than trusting two result files supplied
after the fact:

1. Snapshot the active agent config and run a machine-verifiable baseline.
2. Lock the proposal, evalset, baseline outputs, and config fingerprints.
3. Apply the candidate through a config adapter and record a receipt.
4. Start post-lock frozen-control and candidate processes only after the locked
   manifest and apply receipt exist, balancing which branch runs first.
5. Pair contemporaneous outcomes by task ID and require sample size, minimum lift, an exact
   one-sided paired-test p-value, no critical regressions, and valid evidence
   chains.
6. Keep the candidate or restore the baseline config. Candidate failures and
   partial config-apply failures also restore before returning.

The zero-dependency judge path launches separate control and candidate
processes for every task after proposal lock. On the bundled 20-task evalset it
moves from `8/20` to `20/20`: `+60 pp`, 12 improvements, zero regressions,
`10/10` balanced execution order, exact `p=0.000244`, and a 95%
paired-bootstrap interval of `+40 pp` to `+80 pp`. DriftGuard keeps the
candidate config and records health evidence, paired comparison evidence,
proposal lifecycle, and experiment receipts across four SHA-256 event chains.

The repository also includes a captured 98-call holdout replication through
the Anthropic-compatible adapter using `deepseek-v4-flash`. A 26-task
development baseline is separated from 36 disjoint holdout tasks. On the 32
measured holdout pairs, contemporaneous control scores `0/32` and candidate
scores `29/32`: `+90.62 pp`, 29 improvements, zero regressions, exact
`p=1.863e-09`, and a 95% paired-bootstrap interval of `+78.12 pp` to
`+100 pp`. Execution order is balanced `16/16`; all eight control/candidate
anchor verdicts hit; all response IDs are unique; and all four evidence chains
verify. This is replicated task-instance holdout evidence on real model
execution, not a production-domain or broad model-capability claim.

The representative private path uses 41 live model calls and completed human
Analyst Guide v8 decisions. It moves `2/12 -> 5/12` with zero regressions, but
the exact paired p-value is `0.125`. DriftGuard rejects the attractive result,
restores the baseline config, and exports only a content-free sealed aggregate.
This does not prove production lift; it demonstrates the product's central
promise on real workflow evidence: unproven improvements do not become
permanent config.

## How We Built It

The product is a Python standard-library kernel plus a zero-build local audit
viewer. The deep interface is `run_experiment(proposal, evalset, runner,
config, paths, policy)`. Runner and config adapters isolate integration-specific
behavior; DriftGuard owns chronology, machine verification, pairing, policy
execution, and receipts.

The command adapter exchanges JSON over standard input/output and deliberately
withholds eval checks and expected answers from the agent process. The JSON
config adapter writes atomically and verifies the restored baseline hash. The
result viewer is copied into every experiment directory together with the raw
logs and exact reproduction command.

## Run It

```bash
python3 scripts/run_experiment.py \
  --proposal fixtures/orchestrated_proposal.json \
  --evalset fixtures/orchestrated_evalset.jsonl \
  --runner-command "python3 fixtures/orchestrated_command_agent.py" \
  --initialize-config fixtures/orchestrated_config.json \
  --out-dir artifacts/orchestrated-run \
  --overwrite
```

Open `artifacts/orchestrated-run/index.html`. No package installation, API key,
account, network, or build step is required for this judge path.

The captured live-model evidence is inspectable at
`artifacts/deepseek-holdout-replication-run/index.html`; reproducing it requires
the configured local Messages gateway and `DEEPSEEK_API_KEY` environment
variable. The content-free representative result is at
`artifacts/analyst-review-sealed/evidence-summary.json`.

## How We Used Codex and GPT-5.6

Codex was the engineering environment for the Build Week work. It audited the
pre-existing kernel, found that the first demo wrote the proposal after the
outcomes, and rebuilt the lifecycle so raw events prove baseline, proposal
lock, config apply, post-lock comparison, and keep/restore in order. A GPT-5.6
Codex worker implemented the audit dashboard from a field-level data contract;
the main GPT-5.6 Codex task then added the external-process runner, disjoint
holdout flow, exact paired gate, failure restoration, private-evidence boundary,
and regression tests. The demo video shows the Codex workflow and explicitly
identifies the GPT-5.6 contribution.

## What's Next

Collect a fresh `n >= 20` representative holdout, run the same locked protocol
prospectively across days, publish experiment-log heads to an external
transparency store, and add signed release approvals for high-risk configs.
