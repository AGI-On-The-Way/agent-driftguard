"""Deterministic primitives for evidence-gated agent feedback loops.

The package provides an event ledger, machine-verifiable verdict protocol,
calibration metrics, statistical lesson gates, proposal verification,
diversity checks, health sensors, and rollback decisions. Applications supply
a thin adapter that maps their ground truth into the verdict protocol.
"""

from .calibration import brier_score, hit_rate, reliability
from .critic import (
    Critic,
    CriticChain,
    CriticResult,
    FlagOverconfidentMiss,
    GateDecision,
    NoActiveDuplicate,
    RequireFalsifiable,
)
from .diversity import check_diversity, streak
from .health import HealthReport, drift_check, health_check
from .ledger import Ledger
from .lessons import (
    MIN_CONFIDENCE_FOR_LESSON,
    MIN_SAMPLES_FOR_LESSON,
    LessonStore,
    distill,
)
from .proposals import ProposalLog
from .verdict import Adapter, Outcome, Verdict, review_pending

__all__ = [
    # storage + types
    "Ledger",
    "Verdict",
    "Outcome",
    "Adapter",
    "review_pending",
    # calibration (A)
    "brier_score",
    "reliability",
    "hit_rate",
    # lessons (B + D)
    "distill",
    "LessonStore",
    "MIN_SAMPLES_FOR_LESSON",
    "MIN_CONFIDENCE_FOR_LESSON",
    # critics (gates)
    "Critic",
    "CriticChain",
    "CriticResult",
    "GateDecision",
    "RequireFalsifiable",
    "NoActiveDuplicate",
    "FlagOverconfidentMiss",
    # proposals (C + F)
    "ProposalLog",
    # diversity (E)
    "check_diversity",
    "streak",
    # health (F) + dual-channel drift (A + F)
    "health_check",
    "HealthReport",
    "drift_check",
]
