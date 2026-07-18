# Fixtures

All fixtures are synthetic, inspectable, and contain no private or production
data.

- `proposal.json`: a synthetic config proposal with a locked metric prediction
- `agent_runs.jsonl`: baseline and candidate outcomes that trigger rollback
- `agent_runs_keep.jsonl`: baseline and candidate outcomes that verify the change
- `real_increment_evalset.jsonl`: domain-neutral machine-checkable tasks
- `real_increment_baseline_outputs.jsonl`: baseline agent outputs for that evalset
- `real_increment_candidate_outputs.jsonl`: candidate agent outputs for that evalset
- `real_increment_proposal.json`: locked proposal for the domain-neutral evalset
- `orchestrated_config.json`: baseline config for the authoritative judge path
- `orchestrated_proposal.json`: candidate config patch and locked metric claim
- `orchestrated_evalset.jsonl`: 20 measured tasks plus 2 stable anchors
- `orchestrated_command_agent.py`: deterministic external process used to test
  the command-runner boundary without leaking eval checks
- `gpt56_config.json`: baseline OpenAI Responses model and prompt config
- `gpt56_proposal.json`: measured-baseline prompt candidate for a live GPT-5.6
  experiment
- `deepseek_config.json`: baseline prompt and local Anthropic Messages gateway
  settings for a real `deepseek-v4-flash` run
- `deepseek_proposal.json`: controlled prompt-policy correction with a measured
  baseline
- `deepseek_holdout_config.json`: plausible generic operations-agent baseline;
  it is not instructed to return known-wrong answers
- `deepseek_holdout_proposal.json`: organization-policy prompt candidate with
  no holdout examples or expected values
- `deepseek_development_evalset.jsonl`: policy-development tasks used for the
  initial measured baseline
- `deepseek_holdout_evalset.jsonl`: disjoint policy-confirmation tasks used
  only for post-lock frozen-control/candidate pairing
- `deepseek_holdout_replication_config.json`: the same generic baseline with a
  stable `ping -> pong` anchor contract
- `deepseek_holdout_replication_proposal.json`: the measured-task candidate
  policy copied byte-for-byte from the first holdout proposal
- `deepseek_holdout_replication_evalset.jsonl`: a second disjoint confirmation
  set frozen after first-run artifact QA and executed once

The deterministic process improves from `8/20` to `20/20`; this is mechanism
evidence, not a claim of model quality. The proposal fixtures are intentionally
not represented as captured Codex or GPT-5.6 output. Build provenance is
documented separately.

The holdout pair is the stronger real-model fixture. Development and holdout
task IDs and examples do not overlap. Both sets are synthetic and inspectable;
their separation tests task-instance generalization, not production-domain
generalization or author blindness.

The first holdout run retained an anchor-design defect: its generic control
prompt did not define `ping`, so control anchors missed while candidate anchors
hit. The replication fixes only that anchor contract. It does not change the
candidate policy used by any measured task, and it uses new confirmation task
instances rather than rerunning the first holdout.
