"""Calibration metrics — Brier score + reliability diagram.

Principle A (评估去 LLM 化): calibration is a proper scoring rule computed
from machine-verifiable, resolved (hit/miss) records only. LLM-judge scores
are never used here. Soft verdicts (machine_verifiable=False) are excluded.
"""

from __future__ import annotations

from collections import Counter
from typing import Optional


def _scored_records(ledger, kind: Optional[str]) -> list[dict]:
    out = []
    for r in ledger.records().values():
        if r["outcome"] not in ("hit", "miss"):
            continue
        if not r.get("machine_verifiable"):
            continue
        if kind is not None and r["kind"] != kind:
            continue
        out.append(r)
    return out


def brier_score(ledger, kind: Optional[str] = None) -> Optional[dict]:
    """Mean squared error between predicted probability and realized outcome,
    plus the Brier Skill Score vs an uninformed coin flip.

    Requires each record's `pred` to carry a `prob` in [0,1]. Brier: lower is
    better (0 = perfect, 0.25 = coin flip). BSS = 1 - brier/0.25: >0 beats a
    coin flip, =1 is perfect, <0 is worse than guessing.
    """
    pairs = []
    for r in _scored_records(ledger, kind):
        prob = (r.get("pred") or {}).get("prob")
        if prob is None:
            continue
        actual = 1.0 if r["outcome"] == "hit" else 0.0
        pairs.append((float(prob), actual))
    if not pairs:
        return None
    bs = sum((p - a) ** 2 for p, a in pairs) / len(pairs)
    return {"brier": round(bs, 4), "brier_skill": round(1.0 - bs / 0.25, 4), "n": len(pairs)}


def reliability(ledger, kind: Optional[str] = None, bins: int = 5) -> list[dict]:
    """Reliability diagram: per probability bucket, predicted vs realized rate."""
    buckets: list[list[tuple[float, float]]] = [[] for _ in range(bins)]
    for r in _scored_records(ledger, kind):
        prob = (r.get("pred") or {}).get("prob")
        if prob is None:
            continue
        actual = 1.0 if r["outcome"] == "hit" else 0.0
        idx = min(bins - 1, int(float(prob) * bins))
        buckets[idx].append((float(prob), actual))
    diagram = []
    for i, b in enumerate(buckets):
        if not b:
            continue
        diagram.append({
            "bucket": f"[{i / bins:.1f},{(i + 1) / bins:.1f})",
            "n": len(b),
            "mean_pred": round(sum(p for p, _ in b) / len(b), 3),
            "actual_rate": round(sum(a for _, a in b) / len(b), 3),
        })
    return diagram


def hit_rate(ledger, kind: Optional[str] = None) -> dict:
    """Raw hit rate over machine-verifiable resolved records, plus a
    by-attribution breakdown of misses (input to lesson distillation)."""
    recs = _scored_records(ledger, kind)
    hits = sum(1 for r in recs if r["outcome"] == "hit")
    n = len(recs)
    miss_attr = Counter(r["attribution"] for r in recs if r["outcome"] == "miss" and r["attribution"])
    return {
        "hit_rate": round(hits / n, 3) if n else None,
        "hits": hits,
        "n": n,
        "miss_attribution": dict(miss_attr),
    }
