# Real Increment Protocol

DriftGuard is useful only when it can test whether a durable agent change made
future behavior measurably better. The protocol below is domain-neutral: use it
for report review, coding agents, extraction agents, support triage, routing,
tool-use policies, or any other agent loop with verifiable outcomes.

## Two Evidence Paths

`scripts/run_experiment.py` is the authoritative path for a controlled pre/post
increment claim and automatic rollback. DriftGuard owns the execution order
and starts the candidate process only after the proposal and experiment
manifest are locked.

`scripts/verify_outputs.py` plus `scripts/run_rollout.py` is a post-hoc import
path. It can analyze externally produced evidence, but because both files
already exist when the command starts, it cannot prove when candidate outputs
were produced and it cannot apply or restore an external config. Reports from
that path must be interpreted as analysis-only decisions.

## Authoritative Sequence

1. Load the evalset and snapshot the active baseline config.
2. Run the baseline batch through the configured runner adapter.
3. Lock a proposal plus hashes of the evalset, baseline outputs, and baseline
   config in the experiment log.
4. Apply the proposal through the config adapter and record the apply receipt.
5. Derive and record a balanced randomized confirmation schedule from the
   locked manifest.
6. For each task, run both the frozen-baseline control and candidate after the
   receipt exists. Alternate which config runs first according to the schedule.
7. Verify initial baseline, post-lock control, and candidate outputs against the
   same locked evalset.
8. Pair post-lock control and candidate results by `task_id` and evaluate sample size, absolute lift,
   statistical confidence, health, drift, and critical-task regressions.
9. Keep the candidate only if every required gate passes. Otherwise restore
   the baseline through the config adapter and record the restore receipt.
10. Verify all hash chains and artifact fingerprints before reporting success.

This sequence matters. If candidate outcomes existed before the proposal was
locked, the result is not evidence of incremental lift.

## Independent Holdout Mode

The strongest bundled mode separates hypothesis formation from confirmation:

```bash
python3 scripts/run_experiment.py \
  --proposal path/to/proposal.json \
  --evalset path/to/development-evalset.jsonl \
  --holdout-evalset path/to/holdout-evalset.jsonl \
  --runner-command "python3 path/to/runner.py" \
  --initialize-config path/to/baseline-config.json \
  --out-dir artifacts/<experiment>-holdout-run
```

`--evalset` remains the development set. DriftGuard runs its baseline before
locking the proposal. `--holdout-evalset` is a disjoint confirmation set:
after proposal lock and config apply, each holdout task runs once against the
frozen baseline config and once against the candidate config in balanced
interleaved order. The final paired effect gate reads only those holdout
control/candidate outcomes.

DriftGuard rejects any task ID shared by the development and holdout sets. Both
normalized evalset hashes and the zero-overlap assertion are stored in the
locked manifest. Checks and expected values from either set remain inside the
verifier and are never sent to the runner.

The initial development baseline remains the baseline recorded in the locked
hypothesis and analysis ledger. The independent comparison ledger contains only
holdout control/candidate verdicts and is authoritative for the effect gate.
In holdout mode the config health signal is also recomputed from those
contemporaneous control/candidate outcomes, rather than from a rolling window
that could mix unequal development and holdout cohorts. The report identifies
all scopes explicitly so a dashboard cannot silently present development reuse
as holdout evidence.

The authoritative interface is intentionally one call:

```python
report = run_experiment(
    proposal=proposal,
    evalset=evalset,
    runner=runner_adapter,
    config=config_adapter,
    paths=experiment_paths,
    policy=experiment_policy,
)
```

The runner adapter receives one task and the active config snapshot. The
config adapter exposes snapshot, apply, and restore operations. The module
owns chronology, pairing, verification, final policy execution, and receipts.

The bundled command adapter makes this lifecycle reproducible with one CLI
invocation:

```bash
python3 scripts/run_experiment.py \
  --proposal fixtures/orchestrated_proposal.json \
  --evalset fixtures/orchestrated_evalset.jsonl \
  --runner-command "python3 fixtures/orchestrated_command_agent.py" \
  --initialize-config fixtures/orchestrated_config.json \
  --out-dir artifacts/orchestrated-run \
  --overwrite
```

The command process receives JSON on standard input with `task_id`, `kind`,
task `input`, `phase`, and the active config. It must return a JSON object with
`output` and may include `prob`, `note`, and `metadata`. Checks and expected
values remain inside DriftGuard and are never sent to the process.

The bundled `scripts/anthropic_messages_runner.py` implements this contract for
Anthropic-compatible `/v1/messages` endpoints. Its config supplies `base_url`,
`model`, `system_prompt`, and the name of the API-key environment variable. The
key value is never copied into config, subprocess output, or evidence logs.
Localhost requests bypass system proxies.

The checked-in `artifacts/deepseek-live-run/` is a captured execution of this
adapter against `deepseek-v4-flash`. It records 66 unique response IDs and a
post-lock paired lift from `8/20` control hits to `20/20` candidate hits
(`+0.60`, exact `p=0.000244`, 95% paired-bootstrap interval `+0.40` to
`+0.80`). The evalset is synthetic and the intervention is an explicit prompt
policy correction, so this is integration and controlled-effect evidence, not
evidence of production-domain generalization.

The stronger `artifacts/deepseek-holdout-run/` uses a generic, non-adversarial
baseline prompt plus disjoint development and holdout task instances. Its 98
unique model responses produce `0/32` holdout control hits and `30/32`
candidate hits (`+0.9375`, 30 improvements, zero regressions, exact
`p=9.313e-10`, 95% paired-bootstrap interval `+0.8438` to `+1.0`). The two
candidate misses are retained. This remains synthetic policy-conformance
evidence and does not prove author blindness or production generalization.

Artifact QA found that the first holdout's generic baseline did not define its
`ping` anchor, so control anchors were not stable. That run remains unchanged.
The replication fixtures fix only the shared anchor contract, preserve the
measured-task candidate policy byte-for-byte, and use a new holdout task set.
This makes the correction visible instead of silently rerunning or replacing
the first evidence.

The captured replication under
`artifacts/deepseek-holdout-replication-run/` contains another 98 unique model
responses. Its 32 measured pairs produce `0/32 -> 29/32` (`+0.9062`, 29
improvements, zero regressions, exact `p=1.863e-09`, 95% paired-bootstrap
interval `+0.7812` to `+1.0`). All eight control/candidate anchors hit, all
four chains verify, and three candidate misses remain inspectable.

The CLI writes `active-config.json` when `--initialize-config` is used. A
passing gate leaves the candidate config in that file. Any unproven effect,
critical regression, integrity failure, candidate-process failure, or partial
config-apply failure restores the baseline snapshot before control returns.

## Interleaved Confirmation

The pre-lock baseline establishes the proposal's measured starting point. It is
not the statistical control for the final effect gate. After locking and apply,
DriftGuard reruns every measured task twice:

- `control`: the runner receives the frozen baseline config snapshot;
- `candidate`: the runner receives the applied candidate config snapshot.

Task order is shuffled deterministically from the locked manifest. Half the
tasks run control first and half run candidate first (within one task when the
count is odd). The full order and its hash are recorded before either
confirmation branch starts. The exact paired test compares only these
contemporaneous control/candidate outcomes.

Verified control/candidate rows are written to `comparison-ledger.jsonl` in
scheduled order. The original `ledger.jsonl` remains scoped to baseline and
candidate health/calibration analysis, so recomputing its metrics does not mix
in a third cohort. Both ledgers, the proposal log, and the experiment log have
independent SHA-256 chains.

This design reduces order and service-time confounding while retaining the
required baseline -> proposal lock -> apply chronology. It assumes the runner
uses the explicit config snapshot passed to it rather than reading mutable
ambient state.

Without `--holdout-evalset`, confirmation reuses the original task instances
and the report retains the shared-task-overfitting limitation. With a holdout,
that limitation is replaced by the narrower warning that one disjoint task set
does not establish production generalization or eliminate service drift.

## Paired Increment Gate

Post-lock control and candidate must contain exactly the same measured `task_id` set
from the same evalset fingerprint. The gate fails closed on missing, extra, or
duplicated tasks.

`keep_change` requires all of the following:

- at least `min_metric_samples` paired machine-verifiable tasks;
- observed hit-rate lift at least `min_absolute_delta`;
- a one-sided exact paired test p-value no greater than `alpha`;
- no regression on tasks marked `critical: true`;
- all configured health and integrity gates for the config decision pass.

Anchor drift governs the learning channel separately. A statistically supported
config may be kept while lesson injection remains paused, which prevents weak
feedback from becoming a new durable rule.

The report also records a deterministic paired-bootstrap confidence interval
for the lift. The exact paired test is the decision gate; the interval is an
audit aid.

The authoritative CLI defaults to `min_metric_samples=20`,
`min_absolute_delta=0.05`, and `alpha=0.05`. These are minimum mechanism
defaults, not a universal production sample-size recommendation.

`--min-development-samples` optionally sets a smaller hypothesis-formation
threshold without weakening the final paired gate. If omitted, it equals
`--min-metric-samples`. The report records the development threshold under
`sample_gate.minimum_per_phase` and the confirmation threshold under
`effect_gate.minimum_samples`; the two must not be presented as one number.

## Private Representative Holdout

Public synthetic fixtures are useful for reproducibility but cannot establish
benefit on a real audience's work. For confidential or proprietary tasks, keep
the authoritative run under `private-evals/<benchmark>/runs/` and publish only
a sealed aggregate:

```bash
python3 scripts/build_private_review_evalset.py \
  --manifest private-evals/analyst-review-v1/source-manifest.json \
  --out-dir private-evals/analyst-review-v1

python3 scripts/run_experiment.py \
  --proposal private-evals/analyst-review-v1/proposal.json \
  --evalset private-evals/analyst-review-v1/development-evalset.jsonl \
  --holdout-evalset private-evals/analyst-review-v1/holdout-evalset.jsonl \
  --runner-command "python3 scripts/anthropic_messages_runner.py" \
  --initialize-config private-evals/analyst-review-v1/baseline-config.json \
  --min-development-samples 7 \
  --min-metric-samples 12 \
  --out-dir private-evals/analyst-review-v1/runs/live-v1

python3 scripts/export_sealed_evidence.py \
  --run-dir private-evals/analyst-review-v1/runs/live-v1 \
  --out artifacts/analyst-review-sealed/evidence-summary.json
```

The source manifest explicitly assigns records to development or holdout.
Chronological splitting is preferred to random splitting when later work is
the intended operating distribution. The builder reads DOCX body text and
separate human scoring records, emits opaque task IDs, and places expected
quality/disposition labels only in the private verifier input. It rejects
duplicate source documents, duplicate labels, missing scores, unknown
dispositions, and source/label overlap between cohorts.

Every follow-up source manifest also lists all previously exposed private
benchmark manifests:

```json
{
  "benchmark_id": "analyst-guide-v8-review-v2",
  "exclude_holdout_benchmark_manifests": [
    "private-evals/analyst-review-v1/benchmark-manifest.json"
  ],
  "development": [],
  "holdout": []
}
```

The empty arrays above are a collection template, not a runnable benchmark.
Before building, populate development with either exposed historical records or
new records, and populate holdout only with fresh source/label pairs. The
builder hashes the new DOCX and scoring-record bytes and rejects either holdout
hash if it appeared in an excluded benchmark. Historical development overlap is
allowed but counted in `benchmark-manifest.json`. Renaming or copying an old
file therefore cannot make it fresh confirmation evidence.

The sealed exporter first requires a holdout-mode report, zero task-ID overlap,
a passed integrity block for every evidence chain, and a completed policy
receipt. It then removes task-level outputs, source paths, configs, prompts,
expected values, notes, and schedules. The resulting summary is useful for
auditing aggregate claims and externally anchoring chain heads. Because the raw
evalset and event logs are withheld, it must be described as private evidence,
not a public reproduction package.

For Analyst Guide v8 review, a task passes only when both the human quality
band and normalized publication disposition match. The human score is an
external label; the model does not grade its own answer. Company-fact
verification remains a separate retrieval-backed workflow and is outside this
classification benchmark.

### Captured Private Result

The first run uses seven chronological development reports and 12 later
holdout reports, plus two development and four holdout anchors. It records 41
unique `deepseek-v4-flash` responses. Measured holdout control/candidate is
`2/12 -> 5/12`: `+0.25`, three improvements, zero regressions, exact
`p=0.125`, and a 95% paired-bootstrap interval of `0.00` to `+0.50`. Execution
order is balanced `6/6`; all eight control/candidate anchors hit; all four
chains verify.

The effect gate fails on statistical confidence, so the candidate is restored.
This is a useful preliminary signal and an authentic rollback, not verified
increment. The 12 reports are now exposed holdout evidence. Prompt changes may
use their errors as development input, but the next confirmation must use new
human-reviewed reports or prospective shadow traffic.

Changing the task mix is not evidence of lift. For example, a hard baseline
cohort and an easier candidate cohort must pause with
`cohort_mismatch`, even if raw hit rate rises from 0% to 100%.

## Proposal JSON

```json
{
  "id": "rubric-tightening-v1",
  "description": "Require concrete evidence before accepting a lesson.",
  "metric": "agent_task_hit_rate",
  "baseline": "measured",
  "predicted_delta": 0.15,
  "change": {
    "prompt_rubric": "require concrete test evidence"
  },
  "previous_config": {
    "prompt_rubric": "accept plausible reviewer feedback"
  }
}
```

Supported metrics are `hit_rate` or `<kind>_hit_rate`. The authoritative path
maps `hit_rate` to `agent_task`; the post-hoc runner can override that mapping
with `--agent-kind`. With `agent_task_hit_rate`, both paths measure rows where
`kind == "agent_task"`.

Set `baseline` to a number to require an exact match with the measured baseline,
or to `"measured"` so DriftGuard records the observed rate before locking the
manifest. `previous_config` must exactly match the active baseline snapshot;
DriftGuard refuses to start when it does not. A failed proposal is restored
from the adapter snapshot, not from an untrusted model response.

## Evalset JSONL

The preferred path is to record agent outputs and let DriftGuard compute
`actual_pass`. Each evalset row defines a task and one or more machine checks.

```jsonl
{"task_id":"route-billing","kind":"agent_task","check":{"type":"json_path_equals","path":"route","value":"billing"},"miss_reason":"wrong_route","note":"support triage route must be billing"}
{"task_id":"code-json","kind":"agent_task","check":{"type":"contains","value":"json.loads"},"miss_reason":"missing_required_method","note":"coding helper answer should use the standard JSON parser"}
{"task_id":"canary-ping","kind":"anchor_task","check":{"type":"equals","value":"pong"},"miss_reason":"anchor_failed","note":"stable deterministic canary"}
```

Supported check types:

- `equals`: output must equal `value`.
- `contains`: stringified output must contain `value`; set
  `case_sensitive: false` for case-insensitive matching.
- `regex`: stringified output must match `pattern`.
- `json_path_equals`: JSON output path must equal `value`.
- `json_path_in`: JSON output path must be one of `values`.
- `number_range`: JSON output path, or the whole output when `path` is empty,
  must fall between optional `min` and `max`.

A task may use a list of checks; all checks must pass.

## Agent Output JSONL

Baseline and candidate output files use the same shape. They should not contain
`actual_pass`; that is produced by the verifier.

```jsonl
{"task_id":"route-billing","output":{"route":"billing"},"prob":0.7,"note":"baseline routed the billing request correctly"}
{"task_id":"code-json","output":"Use json.loads(payload) and catch json.JSONDecodeError.","prob":0.9,"note":"candidate used the required parser"}
```

Required fields:

- `task_id`: references an evalset row.
- `output`: the actual agent output, either JSON or text.

Optional fields:

- `run_id`: stable run identifier; defaults to `<phase>-<task_id>`.
- `prob`: the agent's pre-outcome confidence, from `0` to `1`.
- `note`: human-readable audit context.

Generate outcome rows:

```bash
python3 scripts/verify_outputs.py \
  --evalset evalset.jsonl \
  --outputs baseline-outputs.jsonl \
  --phase baseline \
  --out baseline.jsonl

python3 scripts/verify_outputs.py \
  --evalset evalset.jsonl \
  --outputs candidate-outputs.jsonl \
  --phase candidate \
  --out candidate.jsonl
```

## Outcome JSONL

Baseline and candidate files use the same row shape. Keep them in separate
files so the rollout chronology is explicit. This is the intermediate evidence
format consumed by `scripts/run_rollout.py`.

```jsonl
{"run_id":"task-001","kind":"agent_task","prob":0.72,"machine_verifiable":true,"actual_pass":true,"miss_reason":null,"note":"baseline task passed"}
{"run_id":"task-002","kind":"agent_task","prob":0.72,"machine_verifiable":true,"actual_pass":false,"miss_reason":"missed_required_evidence","note":"failed a deterministic rubric check"}
{"run_id":"anchor-001","kind":"anchor_task","prob":0.9,"machine_verifiable":true,"actual_pass":true,"miss_reason":null,"note":"stable canary task"}
```

Required fields:

- `run_id`: unique across baseline and candidate.
- `kind`: task group used by metrics, such as `agent_task` or `anchor_task`.
- `machine_verifiable`: `true` if an external rule, test, label, or data source
  can judge the outcome.
- `actual_pass`: `true` for hit, `false` for miss.

Optional fields:

- `prob`: the agent's pre-outcome confidence, from `0` to `1`; used for Brier
  and reliability metrics.
- `miss_reason`: attribution for misses; used for lesson gates.
- `note`: human-readable audit context.

Soft rows can be recorded with `machine_verifiable: false`, but they do not
enter calibration, hit-rate verification, or lesson distillation.

## Rollout Command

```bash
python3 scripts/run_rollout.py \
  --proposal proposal.json \
  --baseline baseline.jsonl \
  --candidate candidate.jsonl \
  --out-dir artifacts/real-rollout-001 \
  --min-metric-samples 20
```

The runner writes:

- `ledger.jsonl`: hash-chained prediction and verdict events.
- `proposal-log.jsonl`: propose, apply, verify lifecycle.
- `drift-report.json`: metrics, integrity, gates, and final action.
- `decision.md`: human-readable keep, pause, or rollback decision.
- `dashboard-data.js`: data bundle compatible with the local dashboard format.

Existing artifacts are not overwritten unless `--overwrite` is passed.
The default sample gate is 4 machine-verifiable rows per phase for the bundled
demo-scale fixtures. Use a higher `--min-metric-samples` value for real
decisions; when the gate fails, DriftGuard returns `pause_for_more_evidence`
instead of treating a small apparent lift as a durable improvement.

## Interpreting Lift

Treat `keep_change` as evidence that the candidate batch cleared the gate, not
as final proof that the agent is permanently better. Stronger proof requires
larger samples, stable task mix, and post-hoc audit of false accepts and false
rollbacks.

The authoritative sequence controls hindsight, task identity, config
execution, and within-task execution order. A single interleaved pass still
does not eliminate service drift, carryover, or shared-task overfitting. A
production causal claim should add independent holdout tasks, repeated runs,
or randomized shadow traffic under a pinned model/runtime. Reports expose this
under `experiment.limitations`.

Treat `rollback_and_pause_lessons` as real value when it prevents a proposed
prompt, config, routing, tool, rubric, or memory change from becoming durable.
That is the first practical increment: fewer bad self-improvements survive.
