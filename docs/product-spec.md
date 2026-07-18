# Product Spec

## Problem

Agent builders increasingly ask models to improve prompts, configs, routing, or
tools based on feedback. Without a disciplined feedback loop, those improvements
can drift: soft judgments teach the agent the wrong lesson, small samples look
like signal, and bad config changes become sticky.

## User

Developer building or operating AI agents with Codex/GPT-5.6. The first useful
domains are any workflows where an agent proposes durable changes to prompts,
configs, routing, tools, rubrics, or memory. That includes investment research
review, but the product is not investment-research-specific.

## Promise

Before an agent can learn from feedback, DriftGuard forces the loop to answer:

- What was predicted before the outcome was known?
- Was the outcome machine-verifiable?
- Is there enough sample size to learn from it?
- Did calibration improve or degrade?
- Can we roll back the change if it failed?

## Real Increment Goal

The product goal is not to make a better demo. The goal is to create a reusable
gate that can measure whether a proposed agent self-improvement produced
incremental lift on real tasks, while stating what the experiment does and does
not establish.

Real incremental lift means:

- the proposal is locked before candidate outcomes exist;
- baseline and candidate tasks are separated in the evidence log;
- agent outputs can be verified against an evalset rather than hand-labeled by
  the agent;
- outcomes are machine-verifiable or explicitly marked soft and excluded from
  calibration;
- baseline and candidate evidence both clear a configurable minimum sample gate;
- development-baseline sufficiency and final paired-holdout sufficiency can use
  separate declared thresholds when cohort sizes differ;
- the selected metric moves in the predicted direction by enough magnitude;
- unhealthy calibration, health, or anchor drift can pause learning or return
  the previous config;
- all evidence can be audited after the fact through hash-chained JSONL.

Post-hoc ingestion is not enough to establish this claim. A caller can hand
`scripts/run_rollout.py` baseline and candidate files that were both produced
before the proposal was locked. That path remains useful for analysis, but it
must not be described as proof of experimental chronology or automatic
rollback.

The authoritative real-increment path is an orchestrated experiment owned by
DriftGuard. It must:

- run the baseline through a runner adapter;
- lock the proposal and an evalset/config manifest before starting any
  candidate process;
- apply the candidate config through a config adapter and record its receipt;
- derive and record a balanced randomized control/candidate execution schedule
  after the proposal is locked;
- run a frozen-baseline control and candidate on the same task IDs only after
  the apply receipt exists, interleaving which config runs first per task;
- require paired post-lock control/candidate task identities;
- pass a minimum effect and statistical-confidence gate, not only a row-count
  gate;
- restore the baseline config for every non-keep decision and record a restore
  receipt.

For stronger evidence, the authoritative path also accepts an independent
holdout evalset. In holdout mode:

- the development evalset is used only to measure the starting point and form
  the locked hypothesis;
- development and holdout task IDs must be disjoint;
- both evalset hashes are locked before the candidate config is applied;
- post-lock frozen control and candidate run only on holdout tasks;
- the final paired effect gate uses only holdout control/candidate outcomes.
- config health compares holdout control/candidate directly and never mixes an
  unequal development cohort into a rolling window.

This prevents a candidate prompt from being credited merely for memorizing the
same task instances used to identify the baseline failure pattern. A holdout
still needs representative task design and independent labels; disjoint IDs
alone do not make a synthetic benchmark production evidence.

The experiment module is the external seam. Agent execution and durable config
storage vary behind two small interfaces: a runner adapter and a config
adapter. Tests use in-memory adapters; the runnable product uses a command
runner and an atomic JSON-file config adapter.

The captured `deepseek-v4-flash` experiment is the first real-model validation
of this seam. It demonstrates controlled prompt-policy lift on the synthetic
evalset, while production benefit remains a separate milestone requiring
representative holdout or shadow traffic.

The first private representative run advances that milestone without claiming
success. It uses completed human report-review decisions on a chronological
holdout and measures `2/12 -> 5/12` (`+0.25`, exact `p=0.125`, interval
`0.00` to `+0.50`). Because the confidence gate fails, DriftGuard restores the
baseline. This is evidence that the product's change-control behavior works on
real work products; it is not evidence that the candidate prompt has a proven
production benefit.

### Private Representative Evidence

Representative workflows can contain reports, customer tickets, source code,
or other material that must not become a public fixture. DriftGuard therefore
supports a sealed-evidence boundary:

- source documents, human labels, evalset checks, and active prompt configs stay
  under the git-ignored `private-evals/` tree;
- task IDs in evidence logs are opaque and do not encode company, author, or
  source-file names;
- the authoritative experiment still locks hashes, runs the interleaved
  control/candidate holdout, and makes a real keep/restore decision;
- a redacted export contains only cohort sizes, hashes, aggregate effects,
  decision receipts, response-metadata aggregates, limitations, and evidence
  chain heads;
- the export must say that raw evidence is withheld and is not independently
  reproducible from the public package alone.

For the first representative benchmark, development and holdout are split by
review chronology from completed Analyst Guide v8 short-term report reviews.
The model receives report body text only. Final human quality bands and
publication dispositions remain verifier-side labels and are never sent to the
runner. This tests agreement with an existing human-owned workflow; it does not
test factual correctness against market data and does not authorize disclosure
of the underlying reports.

Once a private holdout has been evaluated, it becomes development evidence for
future iterations. It must not be rerun after prompt tuning and described as an
independent holdout. A subsequent confirmation claim requires newly completed
human reviews or prospective shadow traffic.

Follow-up benchmark manifests must reference every prior private benchmark
manifest as a holdout exclusion source. DriftGuard compares both
source-document and human-label SHA-256 hashes before extracting confirmation
tasks. Any historical overlap in the new holdout fails before a runner or
config adapter can be invoked. Historical records may move into development,
where their overlap is recorded rather than rejected. Filenames and opaque task
IDs are not sufficient freshness checks because the same document can be
renamed.

## MVP

Two local paths:

- `scripts/run_demo.py`
- sample synthetic agent runs under `fixtures/`
- `scripts/run_rollout.py`
- `scripts/verify_outputs.py`
- `scripts/run_experiment.py`
- command-based agent runner adapter
- Anthropic Messages runner for local/compatible model gateways
- atomic JSON config adapter with apply/restore receipts
- paired-task effect and confidence gate
- optional disjoint development/holdout confirmation mode
- private DOCX plus human-label evalset builder
- sealed aggregate evidence export for non-public evalsets
- experiment manifest and hash-chained lifecycle log
- domain-neutral evalset and agent output JSONL input contract
- real baseline/candidate JSONL input contract
- append-only ledger output
- independent append-only comparison ledger
- calibration and reliability report
- drift report
- keep, pause, or rollback decision
- README path that judges can run

## Core Objects

| Object | Meaning |
|---|---|
| Proposal | A model-suggested prompt/config/tool change with predicted delta |
| Evalset task | A domain-neutral task with machine-checkable expected behavior |
| Agent output | Raw baseline or candidate output before pass/fail is computed |
| Ledger record | A registered prediction before the result is known |
| Comparison ledger | Post-lock control/candidate verdicts written in scheduled order |
| Verdict | Outcome review; machine-verifiable or soft |
| Lesson | A distilled rule that passes sample and confidence gates |
| Rollback decision | Deterministic decision to keep, pause, or revert a change |
| Experiment manifest | Locked proposal, evalset, baseline, and config fingerprints |
| Confirmation schedule | Balanced post-lock task order for frozen control and candidate |
| Runner adapter | Executes a task against the active config after the correct lifecycle event |
| Config adapter | Applies or restores durable config and returns a verifiable receipt |
| Policy receipt | Evidence that the final keep/restore action was actually executed |
| Sealed evidence export | Redacted aggregate from a private authoritative run, with source hashes and explicit reproducibility limits |

## Demo Narrative

1. Codex proposes a change to an agent rubric.
2. The proposal locks its expected improvement as a falsifiable prediction.
3. DriftGuard records the prediction in a hash-chained append-only ledger.
4. Synthetic follow-up runs produce mixed outcomes.
5. DriftGuard blocks the lesson because the evidence shows drift or insufficient confidence.
6. The report shows why the rollback/pause happened.

## Success Criteria

- Demo runs locally in under 60 seconds.
- A real rollout can be evaluated without editing Python code.
- The authoritative experiment command executes baseline and candidate runs;
  it does not accept a precomputed candidate file.
- A candidate process cannot start before the proposal and manifest are
  locked in the experiment log.
- The effect gate compares post-lock frozen-control and candidate results,
  paired by the same task IDs and evalset fingerprint.
- When a holdout evalset is supplied, development and confirmation task IDs are
  disjoint and the effect gate contains only holdout pairs.
- Control-first and candidate-first executions are balanced and the complete
  schedule is fingerprinted before confirmation starts.
- Different task mixes fail closed instead of producing `keep_change`.
- `keep_change` requires minimum samples, minimum absolute lift, and a
  configured statistical confidence level.
- In holdout mode, lowering the development threshold must not silently lower
  the paired confirmation threshold.
- Every non-keep decision restores the baseline config and records matching
  before/after hashes.
- No external secrets required.
- Kernel tests pass.
- Generic rollout tests pass.
- Report explains the decision without hand-wavy LLM scoring.
- A sealed export contains no source text, expected labels, config prompt,
  company identifier, analyst name, or private filesystem path.
- Video can show the core loop in under 3 minutes.
