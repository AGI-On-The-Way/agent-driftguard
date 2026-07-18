# Scripts

Run the authoritative external-process experiment. This path owns baseline
execution, proposal locking, config apply, candidate execution, paired
verification, and the final keep or restore action. By default, the effect gate
uses a post-lock frozen-control/candidate comparison with balanced randomized
execution order:

```bash
python3 scripts/run_experiment.py \
  --proposal fixtures/orchestrated_proposal.json \
  --evalset fixtures/orchestrated_evalset.jsonl \
  --runner-command "python3 fixtures/orchestrated_command_agent.py" \
  --initialize-config fixtures/orchestrated_config.json \
  --out-dir artifacts/orchestrated-run \
  --overwrite
```

`--config` gates an existing JSON config file. `--initialize-config` copies a
baseline fixture to `<out-dir>/active-config.json`. The CLI refuses to replace
existing evidence unless `--overwrite` is present and writes a self-contained
viewer into the output directory. Use `--comparison-mode sequential` only for
legacy compatibility; it has weaker protection against temporal confounding.

Add `--holdout-evalset path/to/holdout.jsonl` to separate hypothesis formation
from confirmation. Development and holdout task IDs must not overlap; the
paired effect gate then uses only post-lock holdout control/candidate results.
Use `--min-development-samples` only when the development cohort is smaller;
it changes the hypothesis-formation gate but never the final paired threshold
set by `--min-metric-samples`.

Run the optional OpenAI Responses process adapter through the same lifecycle:

```bash
python3 scripts/run_experiment.py \
  --proposal fixtures/gpt56_proposal.json \
  --evalset fixtures/orchestrated_evalset.jsonl \
  --runner-command "python3 scripts/openai_responses_runner.py" \
  --initialize-config fixtures/gpt56_config.json \
  --out-dir artifacts/gpt56-run
```

`openai_responses_runner.py` reads `OPENAI_API_KEY` from the environment. It
uses the active config for model, reasoning effort, system prompt, and maximum
output tokens. Eval checks and expected answers are retained by DriftGuard and
are not sent to the runner process.

Run an Anthropic-compatible live model gateway through the same lifecycle:

```bash
python3 scripts/run_experiment.py \
  --proposal fixtures/deepseek_holdout_replication_proposal.json \
  --evalset fixtures/deepseek_development_evalset.jsonl \
  --holdout-evalset fixtures/deepseek_holdout_replication_evalset.jsonl \
  --runner-command "python3 scripts/anthropic_messages_runner.py" \
  --initialize-config fixtures/deepseek_holdout_replication_config.json \
  --out-dir artifacts/deepseek-holdout-replication-run
```

The runner reads the key from the environment variable named by
`api_key_env`; the fixture names `DEEPSEEK_API_KEY` but never stores its value.
The checked-in `artifacts/deepseek-holdout-replication-run/` contains a captured
98-response execution, disjoint development/holdout hashes, stable control and
candidate anchors, and four verified evidence chains. The first holdout and
older shared-task executions remain as audit history.

Build a private representative report-review benchmark from an explicit source
manifest:

```bash
python3 scripts/build_private_review_evalset.py \
  --manifest private-evals/analyst-review-v1/source-manifest.json \
  --out-dir private-evals/analyst-review-v1
```

The manifest maps each local DOCX to its independently completed Markdown
scoring record and assigns it to `development` or `holdout`. Generated task IDs
are opaque. The command rejects overwrite by default and writes only inside the
git-ignored `private-evals/` area.

For every follow-up benchmark, add prior private `benchmark-manifest.json`
paths to `exclude_holdout_benchmark_manifests`. The builder refuses holdout
source or scoring-record hashes that have already appeared, even when an old
file was renamed. Historical records remain valid development material and are
reported as such in the private audit.

After an authoritative private holdout run, export a content-free aggregate:

```bash
python3 scripts/export_sealed_evidence.py \
  --run-dir private-evals/analyst-review-v1/runs/live-v1 \
  --out artifacts/analyst-review-sealed/evidence-summary.json
```

The exporter fails closed unless the report is holdout evidence with disjoint
task IDs, valid evidence chains, and a completed policy action. It never copies
raw task text, expected labels, outputs, prompts, schedules, notes, private
paths, or model credentials.

The checked-in sealed summary is intentionally a failed-gate result:
`2/12 -> 5/12`, `+25 pp`, exact `p=0.125`, followed by baseline restoration.
It demonstrates representative workflow gating without claiming a verified
candidate improvement.

Before human labels exist, run a private blind shadow pilot without mutating the
production config:

```bash
python3 scripts/run_shadow_pilot.py \
  --manifest private-evals/analyst-prep-shadow-v1/source-manifest.json \
  --baseline-config private-evals/analyst-prep-shadow-v1/baseline-config.json \
  --candidate-config private-evals/analyst-prep-shadow-v1/candidate-config.json \
  --runner-command "python3 scripts/anthropic_messages_runner.py" \
  --out-dir private-evals/analyst-prep-shadow-v1/runs/pilot-001
```

The runner locks both config hashes, balances execution order, makes two calls
per report, randomizes A/B identity separately, and writes a tamper-evident
event chain plus `blind-adjudication.md`. Keep `blind-mapping.json` sealed until
the human choices are frozen. This is pilot evidence only; it cannot trigger the
authoritative keep gate.

Run the original deterministic rollback dashboard scenario:

```bash
python3 scripts/run_demo.py
```

Run the verified-improvement branch:

```bash
python3 scripts/run_demo.py --scenario keep
```

Verify real agent outputs against a machine-checkable evalset:

```bash
python3 scripts/verify_outputs.py \
  --evalset path/to/evalset.jsonl \
  --outputs path/to/baseline-outputs.jsonl \
  --phase baseline \
  --out artifacts/real-rollout-001/baseline.jsonl
```

Analyze a post-hoc rollout from locked proposal plus baseline/candidate JSONL:

```bash
python3 scripts/run_rollout.py \
  --proposal path/to/proposal.json \
  --baseline path/to/baseline.jsonl \
  --candidate path/to/candidate.jsonl \
  --out-dir artifacts/real-rollout-001 \
  --min-metric-samples 20
```

All scripts import the bundled `src/feedback_kit` package and require only
Python's standard library. The OpenAI adapter uses the HTTPS API directly and
adds no SDK dependency; the Messages adapter uses the configured compatible
endpoint directly and bypasses system proxies for localhost.
