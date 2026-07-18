# Fixtures

All fixtures are synthetic, inspectable, and contain no private or production
data.

- `proposal.json`: a synthetic config proposal with a locked metric prediction
- `agent_runs.jsonl`: baseline and candidate outcomes that trigger rollback
- `agent_runs_keep.jsonl`: baseline and candidate outcomes that verify the change

The proposal is intentionally not represented as captured Codex or GPT-5.6
output. Build provenance is documented separately.
