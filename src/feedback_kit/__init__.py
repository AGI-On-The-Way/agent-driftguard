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
from .evalset import CheckResult, build_outcome_rows, verify_task_output
from .experiment import (
    AgentRunner,
    CommandAgentRunner,
    ConfigAdapter,
    ExperimentPaths,
    ExperimentPolicy,
    JsonFileConfigAdapter,
    format_p_value,
    json_hash,
    paired_effect_gate,
    run_experiment,
)
from .health import HealthReport, drift_check, health_check
from .ledger import Ledger
from .lessons import (
    MIN_CONFIDENCE_FOR_LESSON,
    MIN_SAMPLES_FOR_LESSON,
    LessonStore,
    distill,
)
from .proposals import ProposalLog
from .private_eval import (
    build_private_review_benchmark,
    extract_docx_body_text,
    normalize_disposition,
    parse_review_label,
    quality_band,
)
from .rollout import RolloutPaths, RunOutcomeAdapter, build_decision, decide, evaluate_rollout
from .sealed_evidence import (
    aggregate_response_metadata,
    build_sealed_evidence,
    sealed_evidence_hash,
    sha256_file,
)
from .shadow import ShadowPaths, run_blind_shadow_pilot
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
    # evalsets
    "CheckResult",
    "build_outcome_rows",
    "verify_task_output",
    # authoritative experiment lifecycle
    "AgentRunner",
    "ConfigAdapter",
    "CommandAgentRunner",
    "JsonFileConfigAdapter",
    "ExperimentPaths",
    "ExperimentPolicy",
    "run_experiment",
    "paired_effect_gate",
    "format_p_value",
    "json_hash",
    # private representative evidence
    "extract_docx_body_text",
    "parse_review_label",
    "quality_band",
    "normalize_disposition",
    "build_private_review_benchmark",
    "aggregate_response_metadata",
    "build_sealed_evidence",
    "sealed_evidence_hash",
    "sha256_file",
    "ShadowPaths",
    "run_blind_shadow_pilot",
    # proposals (C + F)
    "ProposalLog",
    # rollout evaluator
    "RolloutPaths",
    "RunOutcomeAdapter",
    "evaluate_rollout",
    "decide",
    "build_decision",
    # diversity (E)
    "check_diversity",
    "streak",
    # health (F) + dual-channel drift (A + F)
    "health_check",
    "HealthReport",
    "drift_check",
]
