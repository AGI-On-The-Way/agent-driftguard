"""ConfigProposal — the meta-learning spine: predict → verify → rollback.

This is what lets the system improve itself without drifting. When lessons
accumulate, a project proposes a config change AND locks in a prediction of
how much a metric should move. Later, the actual metric is checked against
that locked prediction; if it didn't deliver, the change is rolled back to
the snapshotted last-good config.

Principle C (预测→验证): predicted_delta is recorded at propose time, before
any result is seen. Principle F (回滚): apply() snapshots prev_config so a
failed proposal can be reverted to last-good. Append-only (principle D).
"""

from __future__ import annotations

import math
import time
import uuid
from pathlib import Path
from typing import Any, Iterator, Optional

from .eventlog import ChainedEventLog


class ProposalLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._log = ChainedEventLog(self.path)

    def _append(self, event: dict) -> None:
        self._log.append(event)

    def events(self) -> Iterator[dict]:
        yield from self._log.events()

    def verify_integrity(self) -> dict:
        return self._log.verify()

    # ---- lifecycle ----------------------------------------------------
    def propose(
        self,
        *,
        change: dict,
        metric: str,
        predicted_delta: float,
        baseline: float,
        description: str = "",
        id: Optional[str] = None,
    ) -> str:
        """Record a proposed config change with a LOCKED prediction.

        predicted_delta carries the expected direction+magnitude of the metric
        move (e.g. +0.05 for hit_rate, -0.02 for Brier where lower is better).
        """
        pid = id or uuid.uuid4().hex[:12]
        self._append({
            "ev": "propose", "id": pid, "ts": time.time(),
            "change": change, "metric": metric,
            "predicted_delta": predicted_delta, "baseline": baseline,
            "description": description,
        })
        return pid

    def apply(self, pid: str, *, prev_config: dict) -> None:
        """Mark a proposal applied and snapshot the config it replaced, so a
        failed verify can roll back to it (principle F)."""
        rec = self.records().get(pid)
        if rec is None:
            raise KeyError(pid)
        if rec["status"] != "proposed":
            raise RuntimeError(f"proposal {pid} must be proposed exactly once before apply")
        self._append({"ev": "apply", "id": pid, "ts": time.time(), "prev_config": prev_config})

    def verify(self, pid: str, current_value: float, *, min_fraction: float = 0.5) -> dict:
        """Compare the realized metric against the locked prediction.

        Success iff the metric moved in the predicted direction by at least
        `min_fraction` of the predicted magnitude. On failure the proposal is
        rolled back and prev_config is returned for the caller to restore.
        """
        rec = self.records().get(pid)
        if rec is None:
            raise KeyError(pid)
        if rec["status"] != "applied" or rec.get("prev_config") is None:
            raise RuntimeError(f"proposal {pid} must be applied with a rollback snapshot before verify")
        predicted = rec["predicted_delta"]
        actual = current_value - rec["baseline"]
        if predicted == 0:
            success = math.isclose(actual, 0.0, rel_tol=0.0, abs_tol=1e-12)
        else:
            ratio = actual / predicted  # >=min_fraction => same direction + enough magnitude
            success = ratio >= min_fraction
        status = "verified" if success else "rolled_back"
        event = {
            "ev": "verify", "id": pid, "ts": time.time(),
            "current_value": current_value, "actual_delta": round(actual, 4),
            "predicted_delta": predicted, "status": status,
        }
        prev_config = rec.get("prev_config")
        if not success:
            event["prev_config"] = prev_config
        self._append(event)
        return {
            "status": status,
            "baseline": rec["baseline"],
            "current_value": current_value,
            "actual_delta": round(actual, 4),
            "predicted_delta": predicted,
            "prev_config": prev_config if not success else None,
        }

    # ---- views --------------------------------------------------------
    def records(self) -> dict[str, dict]:
        integrity = self.verify_integrity()
        if not integrity["valid"]:
            raise ValueError(f"proposal log integrity check failed: {integrity['error']}")
        recs: dict[str, dict] = {}
        for ev in self.events():
            if ev["ev"] == "propose":
                recs[ev["id"]] = {**ev, "status": "proposed", "prev_config": None}
            elif ev["ev"] == "apply":
                r = recs.get(ev["id"])
                if r:
                    r["status"] = "applied"
                    r["prev_config"] = ev.get("prev_config")
            elif ev["ev"] == "verify":
                r = recs.get(ev["id"])
                if r:
                    r["status"] = ev["status"]
                    r["actual_delta"] = ev.get("actual_delta")
        return recs

    def active(self) -> list[dict]:
        """Applied but not yet verified — awaiting enough fresh outcomes."""
        return [r for r in self.records().values() if r["status"] == "applied"]
