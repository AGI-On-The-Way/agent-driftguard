"""Append-only JSONL ledger of predictions and reviews.

Principle D (不可篡改): every event is appended, never rewritten. A review
does not overwrite the prediction — it is a new event referencing the same
id. The "current" view is computed by folding the event log; all history is
retained and independently auditable.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Iterator, Optional

from .eventlog import ChainedEventLog
from .verdict import Verdict


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._log = ChainedEventLog(self.path)

    # ---- write (append-only) ------------------------------------------
    def _append(self, event: dict) -> None:
        self._log.append(event)

    def register(self, kind: str, payload: dict, *, id: Optional[str] = None, **pred: Any) -> str:
        """Register a prediction/judgment before its outcome is known.

        Extra keyword args (e.g. prob=0.7, predicted_direction=+0.05) are
        stored under `pred` for calibration and predict→verify.
        """
        rid = id or uuid.uuid4().hex[:12]
        event = {"ev": "register", "id": rid, "ts": time.time(), "kind": kind, "payload": payload}
        if pred:
            event["pred"] = pred
        self._append(event)
        return rid

    def review(self, id: str, verdict: Verdict) -> None:
        self._append({
            "ev": "review",
            "id": id,
            "ts": time.time(),
            "outcome": verdict.outcome.value,
            "machine_verifiable": verdict.machine_verifiable,
            "confidence": verdict.confidence,
            "attribution": verdict.attribution,
            "attribution_machine_verifiable": verdict.attribution_machine_verifiable,
            "detail": verdict.detail,
        })

    # ---- read (folded view) -------------------------------------------
    def events(self) -> Iterator[dict]:
        yield from self._log.events()

    def verify_integrity(self) -> dict:
        return self._log.verify()

    def records(self) -> dict[str, dict]:
        """Fold the event log into id -> current record state.

        Multiple reviews per id are all retained under `reviews`; the latest
        review wins for the convenience fields (outcome/confidence/...).
        """
        integrity = self.verify_integrity()
        if not integrity["valid"]:
            raise ValueError(f"ledger integrity check failed: {integrity['error']}")
        recs: dict[str, dict] = {}
        for ev in self.events():
            if ev["ev"] == "register":
                recs[ev["id"]] = {
                    "id": ev["id"],
                    "ts": ev["ts"],
                    "kind": ev["kind"],
                    "payload": ev["payload"],
                    "pred": ev.get("pred"),
                    "reviews": [],
                    "outcome": "pending",
                    "machine_verifiable": None,
                    "confidence": None,
                    "attribution": None,
                }
            elif ev["ev"] == "review":
                rec = recs.get(ev["id"])
                if rec is None:  # review for an id we never saw registered
                    continue
                rec["reviews"].append(ev)
                rec["outcome"] = ev["outcome"]
                rec["machine_verifiable"] = ev["machine_verifiable"]
                rec["confidence"] = ev["confidence"]
                rec["attribution"] = ev["attribution"]
        return recs

    def pending(self) -> list[dict]:
        return [r for r in self.records().values() if r["outcome"] == "pending"]
