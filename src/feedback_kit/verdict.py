"""Verdict types + the adapter protocol every project supplies.

Principle A (评估去 LLM 化): each Verdict carries `machine_verifiable`.
Soft (LLM-derived) verdicts are recorded and reconciled, but their
confidence is capped and they are excluded from lesson distillation and
calibration. There is ONE code path — the flag, not the project, decides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol, runtime_checkable

SOFT_CONFIDENCE_CAP = 0.5  # principle A: soft verdicts never exceed this


class Outcome(str, Enum):
    PENDING = "pending"          # not resolvable yet
    HIT = "hit"
    MISS = "miss"
    EXPIRED = "expired"          # verification window passed unresolved
    UNVERIFIABLE = "unverifiable"  # structurally cannot be judged


@dataclass
class Verdict:
    outcome: Outcome
    machine_verifiable: bool                 # False => excluded from lessons + calibration
    confidence: float = 1.0                  # 0..1, capped for soft verdicts
    attribution: Optional[str] = None        # classified miss reason
    attribution_machine_verifiable: bool = False
    detail: dict = field(default_factory=dict)


@runtime_checkable
class Adapter(Protocol):
    """What a project supplies — kept deliberately thin (4 things)."""

    name: str
    kinds: set[str]

    def snapshot(self) -> dict:
        """Fetch the application's machine-verifiable ground truth."""
        ...

    def verdict(self, record: dict, snapshot: dict) -> Verdict:
        """Judge a single pending record against ground truth."""
        ...


def review_pending(ledger, adapter: Adapter, snapshot: Optional[dict] = None) -> list[tuple[str, str]]:
    """Run the adapter's verdict over every pending record of its kinds.

    Returns the list of (record_id, outcome) that were resolved this pass.
    Records the verdict append-only (principle D) and caps soft confidence
    (principle A).
    """
    snap = snapshot if snapshot is not None else adapter.snapshot()
    resolved: list[tuple[str, str]] = []
    for rec in ledger.pending():
        if rec["kind"] not in adapter.kinds:
            continue
        v = adapter.verdict(rec, snap)
        if v.outcome == Outcome.PENDING:
            continue
        if not v.machine_verifiable:
            v.confidence = min(v.confidence, SOFT_CONFIDENCE_CAP)
        ledger.review(rec["id"], v)
        resolved.append((rec["id"], v.outcome.value))
    return resolved
