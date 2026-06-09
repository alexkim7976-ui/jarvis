"""
J.A.R.V.I.S — FDS 동적 신뢰 엔진 테스트 (기존 62개와 독립 실행)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
실행: .venv/bin/python test_fds.py
검증:
  · 정상 행동 allow / 하드캡 즉시 block
  · 속도 폭주·위험 시퀀스·미사용 기기·비정상 시간대 시그널
  · z-score 금액 이탈 → step_up
  · 위험점수 밴드(allow<40 / 40≤step_up<70 / block≥70)
  · jarvis_link step-up 재인증 훅 연결(40~70 에서만 호출)
  · 차단 요청은 베이스라인 미학습(오염 방지) + 감사로그 적재 + 스레드 세이프
"""
import time, threading

from fds import (FDSEngine, FDSConfig, BehaviorEvent, DEFAULT_WEIGHTS)

SUB = "owner-1"


def _cfg(**over) -> FDSConfig:
    """테스트 결정성을 위해 off_hours 는 기본 비활성(명시 시만 켬)."""
    base = dict(off_hours=frozenset())
    base.update(over)
    return FDSConfig(**base)


def _ev(command="rebalance_execute", *, sub=SUB, amount=0.0, device_id="mac-1", ts=None):
    return BehaviorEvent(sub=sub, command=command, device_id=device_id,
                         scope="rebalance", amount=amount,
                         ts=ts if ts is not None else time.time())


# ── 1. 정상 행동 → allow ────────────────────────────────────────
def test_benign_allowed():
    eng = FDSEngine(_cfg())
    d = eng.evaluate(_ev(amount=1000))
    assert d.action == "allow" and d.risk_score == 0, d.reasons
    print("✓ 정상 행동 → allow (risk 0)")


# ── 2. 하드캡 초과 금액 → 즉시 block ────────────────────────────
def test_hard_threshold_blocks():
    eng = FDSEngine(_cfg(amount_hard_cap=100_000_000))
    d = eng.evaluate(_ev(command="withdraw", amount=500_000_000))
    assert d.action == "block" and d.risk_score >= 70, d.reasons
    assert any(s.name == "threshold" for s in d.signals)
    print("✓ 하드캡 초과 금액 → 즉시 block (risk≥70)")


# ── 3. 속도 폭주 → velocity 시그널 → step_up ────────────────────
def test_velocity_flood_step_up():
    eng = FDSEngine(_cfg(velocity_max=5))
    now = time.time()
    last = None
    for i in range(7):                                   # 윈도우 내 7건(>5)
        last = eng.evaluate(_ev(command="macro_run", ts=now + i * 0.1))
    assert any(s.name == "velocity" for s in last.signals), last.reasons
    assert last.action == "step_up" and 40 <= last.risk_score < 70, last.reasons
    print("✓ 단시간 명령 폭주 → velocity step_up (40≤risk<70)")


# ── 4. 위험 명령 시퀀스 → sequence 시그널 ───────────────────────
def test_dangerous_sequence():
    eng = FDSEngine(_cfg())
    eng.evaluate(_ev(command="unlock_risk_guard"))       # 위험차단 해제
    d = eng.evaluate(_ev(command="sell_all"))            # → 대량 매도
    assert any(s.name == "sequence" for s in d.signals), d.reasons
    assert d.action == "step_up", d.reasons              # 50 → step_up
    print("✓ 위험차단 해제 → 대량매도 시퀀스 → step_up")


# ── 5. 시퀀스 + 속도 결합 → block ───────────────────────────────
def test_combined_signals_block():
    eng = FDSEngine(_cfg(velocity_max=2))
    now = time.time()
    eng.evaluate(_ev(command="macro_run", ts=now))            # 윈도우 채우기(속도)
    eng.evaluate(_ev(command="unlock_risk_guard", ts=now + 0.1))  # 직전 = 위험차단 해제
    d = eng.evaluate(_ev(command="sell_all", ts=now + 0.2))  # sequence(50)+velocity(40)=90
    assert d.risk_score >= 70 and d.action == "block", d.reasons
    print("✓ 시퀀스+속도 결합(≥70) → block")


# ── 6. 미사용 기기 갑작스런 활성 → new_device 시그널 ────────────
def test_new_device_signal():
    eng = FDSEngine(_cfg())
    eng.evaluate(_ev(device_id="mac-1", amount=1000))    # 베이스라인 학습
    d = eng.evaluate(_ev(device_id="phone-unknown", amount=1000))
    assert any(s.name == "new_device" for s in d.signals), d.reasons
    print("✓ 미사용 기기 활성 → new_device 시그널")


# ── 7. z-score 금액 이탈 → step_up ──────────────────────────────
def test_amount_zscore_step_up():
    eng = FDSEngine(_cfg(velocity_max=1000, z_threshold=3.0))  # 속도 비활성
    now = time.time()
    for i, amt in enumerate([100, 120, 90, 110, 95, 105]):     # 베이스라인(평소 ~100)
        eng.evaluate(_ev(command="buy", amount=amt, ts=now + i))
    d = eng.evaluate(_ev(command="buy", amount=5_000_000, ts=now + 100))  # 급격한 이탈
    assert any(s.name == "amount_zscore" for s in d.signals), d.reasons
    assert d.action == "step_up" and 40 <= d.risk_score < 70, d.reasons
    print("✓ 평소 금액 분포 대비 z-score 이탈 → step_up")


# ── 8. 비정상 시간대 → off_hours 시그널 ─────────────────────────
def test_off_hours_signal():
    # 크래프트한 ts 의 '로컬 시'를 off_hours 로 지정(타임존 무관 결정성)
    ts = time.time()
    hour = time.localtime(ts).tm_hour
    eng = FDSEngine(_cfg(off_hours=frozenset({hour})))
    d = eng.evaluate(_ev(amount=1000, ts=ts))
    assert any(s.name == "off_hours" for s in d.signals), d.reasons
    print("✓ 비정상 시간대 활동 → off_hours 시그널")


# ── 9. jarvis_link step-up 재인증 훅 (40~70 에서만 호출) ─────────
def test_step_up_hook_invoked():
    calls = []
    def fake_link_hook(event, decision):
        # 운영 시 jarvis_link.issue_approval(...) 자리 — 일회성 승인코드 재요구
        calls.append((event.command, decision.risk_score))
        return {"challenge": "REAUTH", "code_required": True}
    eng = FDSEngine(_cfg(), step_up_hook=fake_link_hook)

    # allow 구간 → 훅 미호출
    eng.evaluate(_ev(amount=1000))
    assert calls == [], "allow 에서는 step-up 훅이 호출되면 안 됨"

    # step_up 구간(시퀀스 50) → 훅 호출 + 챌린지 부착
    eng.evaluate(_ev(command="unlock_risk_guard"))
    d = eng.evaluate(_ev(command="withdraw"))
    assert d.action == "step_up" and len(calls) == 1, d.reasons
    assert d.step_up_challenge and d.step_up_challenge["code_required"]

    # block 구간 → 훅 미호출(즉시 차단)
    big = eng.evaluate(_ev(command="withdraw", amount=10**12))
    assert big.action == "block" and len(calls) == 1, "block 에서는 훅 미호출"
    print("✓ step-up 훅: 40~70 에서만 jarvis_link 재인증 요구(allow/block 미호출)")


# ── 10. 차단 요청은 베이스라인 미학습(오염 방지) ────────────────
def test_block_does_not_poison_baseline():
    eng = FDSEngine(_cfg())
    before = eng.profile.seen(SUB)
    eng.evaluate(_ev(command="withdraw", amount=10**12))   # block
    assert eng.profile.seen(SUB) == before, "차단 요청이 베이스라인에 학습됨"
    eng.evaluate(_ev(amount=1000))                         # allow → 학습됨
    assert eng.profile.seen(SUB) == before + 1
    print("✓ 차단 요청 베이스라인 미학습 + 정상 요청만 학습")


# ── 11. 감사로그 적재 ───────────────────────────────────────────
def test_audit_log():
    eng = FDSEngine(_cfg())
    eng.evaluate(_ev(amount=1000))
    eng.evaluate(_ev(command="withdraw", amount=10**12))
    log = eng.audit_log(SUB)
    assert len(log) == 2 and {l["action"] for l in log} == {"allow", "block"}
    print("✓ 모든 판정 감사로그 적재(action/score/signals)")


# ── 12. 스레드 세이프(동시 평가) ────────────────────────────────
def test_thread_safe_concurrent():
    eng = FDSEngine(_cfg(velocity_max=10**9))
    def worker(n):
        for i in range(50):
            eng.evaluate(_ev(sub=f"u{n}", command="buy", amount=100 + i))
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert eng.collector.stats()["events"] == 8 * 50
    assert len(eng.audit_log()) == 8 * 50
    print("✓ 8스레드 동시 평가 — 카운트/감사로그 정합(RLock 세이프)")


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"\n✅ ALL {len(tests)} TESTS PASSED")


if __name__ == "__main__":
    _run()
