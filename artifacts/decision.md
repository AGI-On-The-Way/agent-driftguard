# Rollback And Pause Lessons

Decision: rollback_and_pause_lessons.

Reasons:
- locked prediction failed: expected +0.15, actual -0.75
- health sensor recommends rollback
- drift sensor recommends pause_lesson_injection
- lesson gate produced no lesson because sample/confidence threshold was not met

Restored config:

```json
{
  "lesson_gate": {
    "machine_verifiable_only": false,
    "min_confidence": 0.5,
    "min_samples": 3
  },
  "prompt_rubric": "accept reviewer feedback when it looks plausible"
}
```

This decision is deterministic: it is derived from hash-chained evidence, machine-verifiable outcomes, the locked proposal prediction, and statistical gates.
