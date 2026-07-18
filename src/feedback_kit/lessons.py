"""Lesson distillation, gated by a statistical threshold.

Principle B (统计门槛): a lesson is only emitted once a single attribution
recurs enough times (MIN_SAMPLES) AND dominates (MIN_CONFIDENCE). The
uncertainty zone (0.4, 0.6) produces no lesson — keep collecting.
Principle A: only machine-verifiable misses feed distillation.
Principle D: LessonStore is append-only.
"""

from __future__ import annotations

import json
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Optional

# Defaults from docs/feedback-loop-design-principles.md §B — not pulled from a hat.
MIN_SAMPLES_FOR_LESSON = 8
MIN_CONFIDENCE_FOR_LESSON = 0.6


def distill(
    ledger,
    *,
    kind: Optional[str] = None,
    min_samples: int = MIN_SAMPLES_FOR_LESSON,
    min_confidence: float = MIN_CONFIDENCE_FOR_LESSON,
) -> list[dict]:
    """Return candidate lessons from recurring, dominant miss attributions.

    Returns [] when below sample threshold or when the dominant attribution
    sits in the uncertainty zone — silence is the correct output there.
    """
    misses = [
        r for r in ledger.records().values()
        if r["outcome"] == "miss"
        and r.get("machine_verifiable")
        and r.get("attribution")
        and (kind is None or r["kind"] == kind)
    ]
    if len(misses) < min_samples:
        return []
    counts = Counter(r["attribution"] for r in misses)
    total = len(misses)
    lessons = []
    for attr, c in counts.most_common():
        share = c / total
        if share < min_confidence:
            break  # most_common is sorted desc; nothing after clears the bar
        lessons.append({
            "kind": kind,
            "attribution": attr,
            "samples": c,
            "share": round(share, 3),
            "lesson": f"miss 主因反复为 {attr}（{c}/{total}，占比 {share:.0%}）",
        })
    return lessons


class LessonStore:
    """Append-only persistence + prompt rendering for distilled lessons."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _signature(self, lesson: dict) -> str:
        return f"{lesson.get('kind')}|{lesson.get('attribution')}"

    def _seen(self) -> set[str]:
        seen = set()
        for l in self.all():
            seen.add(self._signature(l))
        return seen

    def add(self, lesson: dict, *, dedupe: bool = True) -> bool:
        """Append a lesson. With dedupe, skip if (kind, attribution) already
        recorded. Returns True if written."""
        if dedupe and self._signature(lesson) in self._seen():
            return False
        rec = dict(lesson)
        rec.setdefault("id", uuid.uuid4().hex[:12])
        rec.setdefault("ts", time.time())
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
        return True

    def all(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def active(self, limit: int = 10) -> list[dict]:
        return self.all()[-limit:]

    def render_for_prompt(self, limit: int = 10) -> str:
        """Render recent lessons as a block to inject into a project's prompt.
        Injection point stays project-specific; the kit only renders."""
        active = self.active(limit)
        if not active:
            return ""
        lines = ["# 历史教训（统计门槛达标后蒸馏）"]
        for l in active:
            lines.append(f"- {l['lesson']}")
        return "\n".join(lines)
