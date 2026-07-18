"""Tamper-evident JSONL event storage used by DriftGuard.

Each event commits to the previous event's digest. Editing, deleting, or
reordering an existing line breaks verification from that point onward.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterator

try:
    import fcntl

    _HAVE_FCNTL = True
except ImportError:  # pragma: no cover - Windows fallback
    _HAVE_FCNTL = False


GENESIS_HASH = "0" * 64


def _canonical(event: dict) -> str:
    return json.dumps(
        event,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(event: dict) -> str:
    return hashlib.sha256(_canonical(event).encode("utf-8")).hexdigest()


class ChainedEventLog:
    """Append-only JSONL with sequence and SHA-256 chain verification."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: dict) -> dict:
        with open(self.path, "a+", encoding="utf-8") as f:
            if _HAVE_FCNTL:
                fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                last = None
                for line in f:
                    if line.strip():
                        last = json.loads(line)

                chained = {
                    **event,
                    "seq": 1 if last is None else int(last["seq"]) + 1,
                    "prev_hash": GENESIS_HASH if last is None else last["hash"],
                }
                chained["hash"] = _digest(chained)
                f.seek(0, os.SEEK_END)
                f.write(_canonical(chained) + "\n")
                f.flush()
                os.fsync(f.fileno())
                return chained
            finally:
                if _HAVE_FCNTL:
                    fcntl.flock(f, fcntl.LOCK_UN)

    def events(self) -> Iterator[dict]:
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)

    def verify(self) -> dict:
        expected_prev = GENESIS_HASH
        count = 0
        head = None
        try:
            for count, event in enumerate(self.events(), start=1):
                if event.get("seq") != count:
                    raise ValueError(f"event {count}: invalid sequence")
                if event.get("prev_hash") != expected_prev:
                    raise ValueError(f"event {count}: previous hash mismatch")
                claimed = event.get("hash")
                body = {key: value for key, value in event.items() if key != "hash"}
                if claimed != _digest(body):
                    raise ValueError(f"event {count}: content hash mismatch")
                expected_prev = claimed
                head = claimed
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            return {
                "valid": False,
                "events_verified": max(0, count - 1),
                "head": head,
                "algorithm": "sha256",
                "error": str(exc),
            }
        return {
            "valid": True,
            "events_verified": count,
            "head": head,
            "algorithm": "sha256",
            "error": None,
        }
