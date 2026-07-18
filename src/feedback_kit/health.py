"""Health sensors — principle F (回滚 trigger) + loop-stall detection.

Watches the loop's own metrics over a rolling window vs the prior window. If
hit-rate drops or Brier rises past tolerance, recommend rolling back (pairs
with proposals.verify). A high pending ratio means the loop is stalling
(predictions registered but never reviewed) — unhealthy, but rollback won't
fix a stall, so it doesn't trigger one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .calibration import _scored_records, brier_score


@dataclass
class HealthReport:
    healthy: bool
    recommend_rollback: bool
    signals: list = field(default_factory=list)


def _split(recs: list[dict], window: int) -> tuple[list[dict], list[dict]]:
    recs = sorted(recs, key=lambda r: r["ts"])
    return recs[-2 * window:-window], recs[-window:]


def _hit_rate(recs: list[dict]) -> Optional[float]:
    if not recs:
        return None
    return sum(1 for r in recs if r["outcome"] == "hit") / len(recs)


def _brier(recs: list[dict]) -> Optional[float]:
    pairs = []
    for r in recs:
        prob = (r.get("pred") or {}).get("prob")
        if prob is None:
            continue
        pairs.append((float(prob), 1.0 if r["outcome"] == "hit" else 0.0))
    if not pairs:
        return None
    return sum((p - a) ** 2 for p, a in pairs) / len(pairs)


def health_check(
    ledger,
    *,
    kind: Optional[str] = None,
    window: int = 20,
    hit_drop_tol: float = 0.1,
    brier_rise_tol: float = 0.05,
    max_pending_ratio: float = 0.5,
) -> HealthReport:
    signals: list[dict] = []
    recommend_rollback = False

    scored = _scored_records(ledger, kind)
    prior, recent = _split(scored, window)

    p_hr, r_hr = _hit_rate(prior), _hit_rate(recent)
    if p_hr is not None and r_hr is not None:
        drop = p_hr - r_hr
        degraded = drop > hit_drop_tol
        signals.append({
            "name": "hit_rate", "prior": round(p_hr, 3), "recent": round(r_hr, 3),
            "delta": round(-drop, 3), "degraded": degraded,
        })
        recommend_rollback = recommend_rollback or degraded

    p_b, r_b = _brier(prior), _brier(recent)
    if p_b is not None and r_b is not None:
        rise = r_b - p_b
        degraded = rise > brier_rise_tol
        signals.append({
            "name": "brier", "prior": round(p_b, 3), "recent": round(r_b, 3),
            "delta": round(rise, 3), "degraded": degraded,
        })
        recommend_rollback = recommend_rollback or degraded

    # stall sensor over recent registrations (resolved or not)
    allrecs = sorted(ledger.records().values(), key=lambda r: r["ts"])
    if kind is not None:
        allrecs = [r for r in allrecs if r["kind"] == kind]
    allrecs = allrecs[-2 * window:]
    if allrecs:
        pending_ratio = sum(1 for r in allrecs if r["outcome"] == "pending") / len(allrecs)
        signals.append({
            "name": "pending_ratio", "value": round(pending_ratio, 3),
            "degraded": pending_ratio > max_pending_ratio,
        })

    healthy = not any(s.get("degraded") for s in signals)
    return HealthReport(healthy=healthy, recommend_rollback=recommend_rollback, signals=signals)


def drift_check(ledger, *, anchor_kind: str, gap_tol: float = 0.05) -> dict:
    """Dual-channel drift detection — the most important anti-drift mechanism
    (principle A + F).

    Designate one `kind` as the ANCHOR: predictions whose judging is purely
    mechanical and immune to lesson/LLM influence. Compare the anchor's Brier against the
    overall Brier. If the overall is materially worse than the anchor
    (gap > gap_tol), the LLM-driven judgments are decaying while the ground
    truth stays stable — a signal to pause lesson injection / roll back, NOT
    to keep feeding the loop its own degrading output.
    """
    overall = brier_score(ledger)
    anchor = brier_score(ledger, kind=anchor_kind)
    if overall is None or anchor is None:
        return {"status": "insufficient_data",
                "overall_n": (overall or {}).get("n", 0),
                "anchor_n": (anchor or {}).get("n", 0)}
    gap = overall["brier"] - anchor["brier"]
    decaying = gap > gap_tol
    return {
        "status": "llm_judgment_may_be_decaying" if decaying else "stable",
        "overall_brier": overall["brier"],
        "anchor_brier": anchor["brier"],
        "gap": round(gap, 4),
        "recommend": "pause_lesson_injection" if decaying else None,
        "detail": (
            f"Overall Brier {overall['brier']} is {gap:.3f} worse than the "
            f"anchor ({anchor['brier']}), above the {gap_tol} tolerance; "
            "the learned-feedback channel may be decaying" if decaying
            else f"Overall Brier {overall['brier']} matches the anchor "
            f"({anchor['brier']}); no drift detected"
        ),
    }
