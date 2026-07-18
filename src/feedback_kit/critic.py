"""Critic gates — PRE (before a prediction enters the ledger) and POST
(after a verdict resolves).

Critics are the quality control of the loop: a PRE critic can refuse to
register a prediction that isn't falsifiable (principle C at entry); a POST
critic can flag a resolved record for reflection (e.g. an overconfident miss).

The kit ships a thin framework + a few reusable built-ins. Projects add their
own critics for domain rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class CriticResult:
    name: str
    passed: bool
    reason: str = ""
    severity: str = "block"  # "block" stops the action; "warn" is advisory


@runtime_checkable
class Critic(Protocol):
    name: str
    phase: str  # "pre" or "post"

    def check(self, record: dict, context: dict) -> CriticResult:
        ...


@dataclass
class GateDecision:
    allowed: bool
    results: list = field(default_factory=list)

    @property
    def blocks(self) -> list:
        return [r for r in self.results if not r.passed and r.severity == "block"]

    @property
    def warnings(self) -> list:
        return [r for r in self.results if not r.passed and r.severity == "warn"]


class CriticChain:
    """Runs the critics registered for a phase and aggregates a decision.

    PRE phase: pass a *candidate* record dict {"kind", "payload", "pred"} and
    typically context={"ledger": ledger}; only register if `allowed`.
    POST phase: pass a folded ledger record (with `outcome`).
    """

    def __init__(self, critics=None):
        self.critics: list[Critic] = list(critics or [])

    def add(self, critic: Critic) -> "CriticChain":
        self.critics.append(critic)
        return self

    def run(self, record: dict, context: dict | None = None, *, phase: str = "pre") -> GateDecision:
        ctx = context or {}
        results = [c.check(record, ctx) for c in self.critics if c.phase == phase]
        allowed = not any(not r.passed and r.severity == "block" for r in results)
        return GateDecision(allowed=allowed, results=results)


# ---- reusable built-ins ----------------------------------------------------

class RequireFalsifiable:
    """PRE / principle C: a prediction must carry a verifiable expectation
    (a probability or a predicted direction). No falsifiable claim => no entry."""

    phase = "pre"

    def __init__(self, fields=("prob", "predicted_direction"), name="require_falsifiable"):
        self.fields = tuple(fields)
        self.name = name

    def check(self, record: dict, context: dict) -> CriticResult:
        pred = record.get("pred") or {}
        ok = any(f in pred for f in self.fields)
        return CriticResult(
            self.name, ok,
            "" if ok else f"prediction lacks any of {self.fields}; not falsifiable",
        )


class NoActiveDuplicate:
    """PRE: refuse a second still-pending prediction with the same dedup key
    (avoids the loop double-counting the same open bet)."""

    phase = "pre"

    def __init__(self, key="code", name="no_active_duplicate"):
        self.key = key
        self.name = name

    def check(self, record: dict, context: dict) -> CriticResult:
        ledger = context.get("ledger")
        if ledger is None:
            return CriticResult(self.name, True, "no ledger in context; skipped", severity="warn")
        k = (record.get("payload") or {}).get(self.key)
        if k is None:
            return CriticResult(self.name, True)
        dup = any((p.get("payload") or {}).get(self.key) == k for p in ledger.pending())
        return CriticResult(
            self.name, not dup,
            "" if not dup else f"active prediction for {self.key}={k} already pending",
        )


class FlagOverconfidentMiss:
    """POST: a miss made at high stated probability is a calibration red flag
    worth reflecting on (advisory, not blocking)."""

    phase = "post"

    def __init__(self, prob_threshold=0.7, name="flag_overconfident_miss"):
        self.prob_threshold = prob_threshold
        self.name = name

    def check(self, record: dict, context: dict) -> CriticResult:
        if record.get("outcome") != "miss":
            return CriticResult(self.name, True)
        prob = (record.get("pred") or {}).get("prob")
        if prob is None:
            return CriticResult(self.name, True)
        ok = prob < self.prob_threshold
        return CriticResult(
            self.name, ok,
            "" if ok else f"miss at prob={prob} ≥ {self.prob_threshold}: overconfident",
            severity="warn",
        )
