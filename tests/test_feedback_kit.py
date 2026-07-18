"""Stdlib-only test runner for the bundled DriftGuard kernel.

Run: python3 tests/test_feedback_kit.py   (no pytest needed)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from feedback_kit import (  # noqa: E402
    CriticChain,
    FlagOverconfidentMiss,
    Ledger,
    LessonStore,
    NoActiveDuplicate,
    Outcome,
    ProposalLog,
    RequireFalsifiable,
    Verdict,
    brier_score,
    check_diversity,
    distill,
    drift_check,
    health_check,
    hit_rate,
    reliability,
    review_pending,
    streak,
)
_FAILURES: list[str] = []


class ExampleAdapter:
    """Small deterministic adapter used by the kernel tests."""

    name = "example"
    kinds = {"demo"}

    def __init__(self, prices: dict[str, float]):
        self._prices = prices

    def snapshot(self) -> dict:
        return {"prices": self._prices}

    def verdict(self, record: dict, snapshot: dict) -> Verdict:
        price = snapshot["prices"].get(record["id"])
        if price is None:
            return Verdict(Outcome.PENDING, machine_verifiable=True)
        if price >= record["payload"]["target"]:
            return Verdict(Outcome.HIT, machine_verifiable=True)
        return Verdict(
            Outcome.MISS,
            machine_verifiable=True,
            attribution=record["payload"].get("miss_reason", "undershoot"),
        )


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  ok  {msg}")
    else:
        _FAILURES.append(msg)
        print(f"FAIL  {msg}")


def test_ledger_append_only_and_fold(tmp: Path) -> None:
    print("\n[ledger] append-only + folded view")
    led = Ledger(tmp / "led.jsonl")
    rid = led.register("demo", {"target": 10.0}, prob=0.7)
    led.review(rid, Verdict(Outcome.HIT, machine_verifiable=True))
    led.review(rid, Verdict(Outcome.MISS, machine_verifiable=True, attribution="late"))

    events = list(led.events())
    check(len(events) == 3, "every write is its own appended event (1 register + 2 reviews)")
    rec = led.records()[rid]
    check(len(rec["reviews"]) == 2, "all reviews retained (principle D: append, not overwrite)")
    check(rec["outcome"] == "miss", "latest review wins for current outcome")


def test_hash_chain_detects_tampering(tmp: Path) -> None:
    print("\n[integrity] SHA-256 event chain detects evidence tampering")
    path = tmp / "chained.jsonl"
    led = Ledger(path)
    rid = led.register("demo", {"target": 10.0}, prob=0.7)
    led.review(rid, Verdict(Outcome.HIT, machine_verifiable=True))
    check(led.verify_integrity()["valid"], "fresh event chain verifies")

    changed = path.read_text(encoding="utf-8").replace(
        '"kind":"demo"', '"kind":"tampered"', 1
    )
    path.write_text(changed, encoding="utf-8")
    check(not led.verify_integrity()["valid"], "edited evidence breaks the chain")
    try:
        led.records()
        blocked = False
    except ValueError:
        blocked = True
    check(blocked, "tampered evidence cannot be folded into application state")


def test_review_and_calibration(tmp: Path) -> None:
    print("\n[review + calibration] machine-verifiable path")
    led = Ledger(tmp / "led2.jsonl")
    prices = {}
    # 10 hits (prob 0.8) + 5 misses (prob 0.6, attribution 'undershoot')
    for i in range(10):
        rid = led.register("demo", {"target": 10.0}, prob=0.8)
        prices[rid] = 12.0
    for i in range(5):
        rid = led.register("demo", {"target": 10.0, "miss_reason": "undershoot"}, prob=0.6)
        prices[rid] = 8.0

    resolved = review_pending(led, ExampleAdapter(prices))
    check(len(resolved) == 15, "all 15 pending records resolved by adapter verdict")
    check(len(led.pending()) == 0, "nothing left pending after review")

    hr = hit_rate(led, kind="demo")
    check(hr["hits"] == 10 and hr["n"] == 15, "hit_rate counts 10/15")
    check(hr["miss_attribution"].get("undershoot") == 5, "miss attribution breakdown correct")

    bs = brier_score(led, kind="demo")
    # hits: (0.8-1)^2=0.04 x10 ; misses: (0.6-0)^2=0.36 x5  => (0.4+1.8)/15
    # brier_score rounds to 4 dp for readability, so compare against the rounded value.
    check(bs is not None and abs(bs["brier"] - round((0.4 + 1.8) / 15, 4)) < 1e-9, "Brier computed from prob vs outcome")
    # BSS is computed from the unrounded brier, so reconstruct from the raw value (not the rounded field).
    check(abs(bs["brier_skill"] - round(1 - ((0.4 + 1.8) / 15) / 0.25, 4)) < 1e-9, "Brier Skill Score = 1 - brier/0.25")
    rel = reliability(led, kind="demo")
    check(any(b["n"] == 10 for b in rel), "reliability buckets populated")


def test_soft_verdict_excluded(tmp: Path) -> None:
    print("\n[principle A] soft verdict excluded from calibration + confidence capped")
    led = Ledger(tmp / "led3.jsonl")
    rid = led.register("review", {"x": 1}, prob=0.9)
    led.review(rid, Verdict(Outcome.HIT, machine_verifiable=False, confidence=0.95))
    # review_pending caps soft confidence, but here we wrote directly; emulate cap path:
    from feedback_kit.verdict import review_pending as rp  # noqa: F401
    bs = brier_score(led, kind="review")
    check(bs is None, "soft (LLM) verdict does not enter Brier")
    rec = led.records()[rid]
    check(rec["outcome"] == "hit", "soft verdict still recorded + reconciled")


def test_soft_confidence_cap(tmp: Path) -> None:
    print("\n[principle A] review_pending caps soft confidence to 0.5")
    led = Ledger(tmp / "led3b.jsonl")
    rid = led.register("soft", {"target": 0}, prob=0.5)

    class SoftAdapter:
        name = "soft"
        kinds = {"soft"}

        def snapshot(self):
            return {}

        def verdict(self, record, snapshot):
            return Verdict(Outcome.HIT, machine_verifiable=False, confidence=0.99)

    review_pending(led, SoftAdapter())
    rec = led.records()[rid]
    check(rec["confidence"] == 0.5, "soft verdict confidence capped at SOFT_CONFIDENCE_CAP")


def test_lesson_statistical_gate(tmp: Path) -> None:
    print("\n[principle B] lesson only emitted past sample + confidence threshold")
    led = Ledger(tmp / "led4.jsonl")
    prices = {}
    # 7 misses, same attribution -> below MIN_SAMPLES (8) -> no lesson
    for i in range(7):
        rid = led.register("demo", {"target": 10.0, "miss_reason": "undershoot"}, prob=0.5)
        prices[rid] = 8.0
    review_pending(led, ExampleAdapter(prices))
    check(distill(led, kind="demo") == [], "7 misses < 8 samples => no lesson (uncertainty respected)")

    # add 1 more (8 total) -> dominant attribution clears bar -> lesson
    rid = led.register("demo", {"target": 10.0, "miss_reason": "undershoot"}, prob=0.5)
    prices[rid] = 8.0
    review_pending(led, ExampleAdapter(prices))
    lessons = distill(led, kind="demo")
    check(len(lessons) == 1 and lessons[0]["samples"] == 8, "8th miss triggers a single dominant lesson")


def test_lesson_store_append_only(tmp: Path) -> None:
    print("\n[lesson store] append-only + dedupe + prompt render")
    store = LessonStore(tmp / "lessons.jsonl")
    l = {"kind": "demo", "attribution": "undershoot", "lesson": "miss 主因反复为 undershoot"}
    check(store.add(l) is True, "first lesson written")
    check(store.add(l) is False, "duplicate (kind, attribution) skipped")
    check(len(store.all()) == 1, "store holds one lesson")
    check("undershoot" in store.render_for_prompt(), "render_for_prompt includes the lesson text")


def test_critic_pre_gate(tmp: Path) -> None:
    print("\n[critic PRE] principle C — block predictions that aren't falsifiable")
    led = Ledger(tmp / "led5.jsonl")
    led.register("demo", {"code": "AAA", "target": 10.0}, prob=0.7)  # one pending AAA
    chain = CriticChain([RequireFalsifiable(), NoActiveDuplicate(key="code")])

    bad = {"kind": "demo", "payload": {"code": "BBB"}, "pred": {}}
    d1 = chain.run(bad, {"ledger": led}, phase="pre")
    check(not d1.allowed, "candidate without prob/direction is blocked")
    check(d1.blocks[0].name == "require_falsifiable", "block names the failing critic")

    dup = {"kind": "demo", "payload": {"code": "AAA"}, "pred": {"prob": 0.6}}
    d2 = chain.run(dup, {"ledger": led}, phase="pre")
    check(not d2.allowed, "duplicate active code=AAA is blocked")

    ok = {"kind": "demo", "payload": {"code": "CCC"}, "pred": {"prob": 0.6}}
    d3 = chain.run(ok, {"ledger": led}, phase="pre")
    check(d3.allowed, "falsifiable, non-duplicate candidate passes")


def test_critic_post_warn(tmp: Path) -> None:
    print("\n[critic POST] flag overconfident miss (advisory, non-blocking)")
    led = Ledger(tmp / "led6.jsonl")
    rid = led.register("demo", {"target": 10.0}, prob=0.9)
    led.review(rid, Verdict(Outcome.MISS, machine_verifiable=True, attribution="undershoot"))
    rec = led.records()[rid]
    chain = CriticChain([FlagOverconfidentMiss(prob_threshold=0.7)])
    d = chain.run(rec, {}, phase="post")
    check(d.allowed, "warn-severity critic does not block")
    check(len(d.warnings) == 1, "overconfident miss raises one warning")


def test_proposal_verify_and_rollback(tmp: Path) -> None:
    print("\n[proposals] principle C+F — predict→verify success, then rollback")
    plog = ProposalLog(tmp / "prop.jsonl")

    # success: predicted +0.05 hit_rate from 0.60 baseline, realized 0.64 (delta +0.04 >= 50%)
    p1 = plog.propose(change={"weight": 1.2}, metric="hit_rate", predicted_delta=0.05, baseline=0.60)
    try:
        plog.verify(p1, 0.64)
        blocked = False
    except RuntimeError:
        blocked = True
    check(blocked, "verify is blocked until apply snapshots the rollback config")
    plog.apply(p1, prev_config={"weight": 1.0})
    r1 = plog.verify(p1, 0.64)
    check(r1["status"] == "verified", "delta in predicted direction past min_fraction => verified")
    check(r1["prev_config"] is None, "verified proposal does not return rollback config")

    # failure: predicted +0.05 but realized 0.58 (got worse) => rollback returns prev_config
    p2 = plog.propose(change={"weight": 1.5}, metric="hit_rate", predicted_delta=0.05, baseline=0.60)
    plog.apply(p2, prev_config={"weight": 1.2})
    r2 = plog.verify(p2, 0.58)
    check(r2["status"] == "rolled_back", "metric moved wrong way => rolled_back")
    check(r2["prev_config"] == {"weight": 1.2}, "rollback returns the snapshotted last-good config")
    check(len(plog.active()) == 0, "no proposals left in applied/unverified state")


def test_diversity_guard(tmp: Path) -> None:
    print("\n[diversity] principle E — flag one-sided prediction collapse")
    led = Ledger(tmp / "led7.jsonl")
    for i in range(9):
        led.register("demo", {"code": f"U{i}"}, direction=1)   # all "up"
    led.register("demo", {"code": "D0"}, direction=-1)         # one "down"
    div = check_diversity(led, kind="demo", window=20, max_share=0.8)
    check(div["skewed"] and div["dominant"] == "up", "9/10 up over max_share=0.8 => skewed")
    check(div["share"] == 0.9, "share computed correctly")
    st = streak(led, kind="demo")
    check(st["direction"] == "down" and st["streak"] == 1, "streak tracks most-recent run")


def test_health_rollback_signal(tmp: Path) -> None:
    print("\n[health] principle F — hit-rate degradation recommends rollback")
    led = Ledger(tmp / "led8.jsonl")
    prices = {}
    # prior window: 4 hits (good); recent window: 4 misses (degraded)
    for i in range(4):
        rid = led.register("demo", {"target": 10.0}, prob=0.7)
        prices[rid] = 12.0
    for i in range(4):
        rid = led.register("demo", {"target": 10.0, "miss_reason": "undershoot"}, prob=0.7)
        prices[rid] = 8.0
    review_pending(led, ExampleAdapter(prices))
    rep = health_check(led, kind="demo", window=4, hit_drop_tol=0.1)
    check(rep.recommend_rollback, "hit rate 1.0 -> 0.0 across windows triggers rollback recommendation")
    check(not rep.healthy, "degraded loop reported unhealthy")


def test_drift_dual_channel(tmp: Path) -> None:
    print("\n[drift] principle A+F — anchor vs overall Brier detects LLM-judgment decay")
    led = Ledger(tmp / "led9.jsonl")
    # anchor kind: well-calibrated pure-machine signal (prob matches outcome) -> low Brier
    for i in range(5):
        rid = led.register("anchor", {"x": i}, prob=0.9)
        led.review(rid, Verdict(Outcome.HIT, machine_verifiable=True))
    # llm kind: badly calibrated (confident but wrong) -> high Brier
    for i in range(5):
        rid = led.register("llm", {"x": i}, prob=0.9)
        led.review(rid, Verdict(Outcome.MISS, machine_verifiable=True, attribution="wrong_direction"))

    d = drift_check(led, anchor_kind="anchor", gap_tol=0.05)
    check(d["status"] == "llm_judgment_may_be_decaying", "overall Brier >> anchor Brier flags decay")
    check(d["anchor_brier"] < d["overall_brier"], "anchor channel stays cleaner than overall")
    check(d["recommend"] == "pause_lesson_injection", "decay recommends pausing lesson injection")

    # remove the bad llm channel signal: a healthy loop should read stable
    led2 = Ledger(tmp / "led9b.jsonl")
    for i in range(5):
        rid = led2.register("anchor", {"x": i}, prob=0.9)
        led2.review(rid, Verdict(Outcome.HIT, machine_verifiable=True))
    for i in range(5):
        rid = led2.register("llm", {"x": i}, prob=0.9)
        led2.review(rid, Verdict(Outcome.HIT, machine_verifiable=True))
    d2 = drift_check(led2, anchor_kind="anchor", gap_tol=0.05)
    check(d2["status"] == "stable", "matched channels => no drift")


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_ledger_append_only_and_fold(tmp)
        test_hash_chain_detects_tampering(tmp)
        test_review_and_calibration(tmp)
        test_soft_verdict_excluded(tmp)
        test_soft_confidence_cap(tmp)
        test_lesson_statistical_gate(tmp)
        test_lesson_store_append_only(tmp)
        test_critic_pre_gate(tmp)
        test_critic_post_warn(tmp)
        test_proposal_verify_and_rollback(tmp)
        test_diversity_guard(tmp)
        test_health_rollback_signal(tmp)
        test_drift_dual_channel(tmp)
    print()
    if _FAILURES:
        print(f"{len(_FAILURES)} FAILED:")
        for f in _FAILURES:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
