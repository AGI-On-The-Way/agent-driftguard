# Sealed Analyst Review Evidence

This directory contains the public aggregate from a private, authoritative
Analyst Guide v8 report-review holdout.

- `evidence-summary.json` contains only cohort counts, hashes, aggregate
  metrics, response metadata, chain heads, limitations, and the final restore
  receipt.
- `evidence-summary.json.sha256` fingerprints the exact JSON file bytes.
- Raw reports, human labels, evalsets, configs, model outputs, event logs, task
  IDs, schedules, notes, and private paths are withheld.

The measured result is `2/12 -> 5/12` (`+25 pp`, three improvements, zero
regressions), but exact `p=0.125` and the confidence interval includes zero.
DriftGuard therefore restores the baseline. This is preliminary representative
evidence and a successful governance decision, not verified model lift.
