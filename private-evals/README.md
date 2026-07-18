# Private Evaluations

This directory is the local boundary for representative or confidential
evaluation material. Git ignores every child except this convention file.

Use one benchmark directory per workflow:

```text
private-evals/<benchmark>/
  source-manifest.json
  baseline-config.json
  proposal.json
  development-evalset.jsonl
  holdout-evalset.jsonl
  benchmark-manifest.json
  runs/<run-id>/
  model-adjudications/labels/<ticker>.md
```

Use a separate directory for unlabeled, blind shadow pilots:

```text
private-evals/<pilot>/
  source-manifest.json
  sources/<opaque-or-ticker-name>.docx
  baseline-config.json
  candidate-config.json
  runs/<run-id>/
    shadow-events.jsonl
    role-outputs.jsonl
    blind-mapping.json
    blind-adjudication.md
    integrity.json
```

Shadow pilots compare isolated control and candidate configs without changing
the production agent or requiring labels in advance. `blind-adjudication.md`
must identify outputs only as A/B. `blind-mapping.json`, source documents,
configs, raw outputs, and the randomization seed stay private until the human
adjudication is frozen.

Rules:

- `source-manifest.json` maps local source documents to completed human labels
  and assigns each record to `development` or `holdout`.
- Every follow-up `source-manifest.json` lists prior private
  `benchmark-manifest.json` files under
  `exclude_holdout_benchmark_manifests`. The builder rejects any reused
  source-document hash or human-label hash from the new holdout. Historical
  records may be reused only in development and are counted in the audit.
- Generated task IDs must be opaque; do not embed company, customer, author, or
  source-file names.
- Raw documents, extracted text, expected labels, configs, and full run
  artifacts stay here and must not be committed.
- Public evidence is produced only through `export_sealed_evidence.py` and must
  retain its private-source limitation.
- Never place API keys, tokens, passwords, or credentials in this directory.
- A shadow pilot is exploratory evidence, not a verified lift claim. It becomes
  human evidence only after the blinded choices are recorded; small pilots do
  not satisfy the authoritative effect gate.

`model-adjudications/` is a private fallback evidence path. It stores
independent model-authored labels when a human reviewer is not available.
These manifests must declare `adjudicator.type = "model"`; their results may
support iteration and model-adjudicated replication but must never be described
as human review or human-quality lift. Label files contain only the final rubric
decision and compact evidence provenance. Raw reports, retrieved evidence,
prompts, and model outputs remain outside public artifacts.
