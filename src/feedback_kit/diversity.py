"""Diversity guard — principle E (多样性保全).

A feedback loop that always agrees with itself collapses: an agent predicting
"强势延续" every time, a reviewer rating everything 4/5. This module watches
the recent direction distribution and flags when one direction dominates, so
the project can inject a counter-case / contrarian check. The kit detects and
recommends; the actual injection stays project-specific.
"""

from __future__ import annotations

from collections import Counter
from typing import Optional


def _direction(v) -> str:
    if isinstance(v, bool):  # guard: bool is an int subclass
        return "up" if v else "down"
    if isinstance(v, (int, float)):
        return "up" if v > 0 else "down" if v < 0 else "flat"
    return str(v)


def _recent_dirs(ledger, kind: Optional[str], window: int, field: str) -> list[str]:
    recs = sorted(ledger.records().values(), key=lambda r: r["ts"])
    if kind is not None:
        recs = [r for r in recs if r["kind"] == kind]
    dirs = []
    for r in recs[-window:] if window else recs:
        v = (r.get("pred") or {}).get(field)
        if v is not None:
            dirs.append(_direction(v))
    return dirs


def check_diversity(
    ledger,
    *,
    kind: Optional[str] = None,
    window: int = 20,
    max_share: float = 0.8,
    field: str = "direction",
) -> dict:
    """Over the last `window` predictions, flag if one direction's share
    exceeds `max_share`."""
    dirs = _recent_dirs(ledger, kind, window, field)
    if not dirs:
        return {"skewed": False, "n": 0}
    counts = Counter(dirs)
    dominant, cnt = counts.most_common(1)[0]
    share = cnt / len(dirs)
    out = {
        "skewed": share > max_share,
        "dominant": dominant,
        "share": round(share, 3),
        "n": len(dirs),
        "distribution": dict(counts),
    }
    if out["skewed"]:
        out["recommend"] = "inject_counter_case"
        out["reason"] = f"最近 {len(dirs)} 次预测 {share:.0%} 都是 {dominant}，多样性坍缩风险"
    return out


def streak(ledger, *, kind: Optional[str] = None, field: str = "direction") -> dict:
    """Length of the current consecutive same-direction run (most recent first)."""
    dirs = _recent_dirs(ledger, kind, 0, field)  # window=0 => all
    if not dirs:
        return {"streak": 0}
    last = dirs[-1]
    n = 0
    for d in reversed(dirs):
        if d == last:
            n += 1
        else:
            break
    return {"direction": last, "streak": n}
