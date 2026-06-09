"""
macro_guard.MacroGuardWatcher 단위 테스트
실행: .venv/bin/python test_macro_guard.py  (또는 pytest test_macro_guard.py)

야후 API 없이 status_fn 주입으로 매크로 등급 전환 시나리오를 시뮬레이션하여
선제 브리핑 발동/디바운스(중복 방지)/동적 텍스트/캐시 연동을 검증한다.
"""
from __future__ import annotations

from macro_guard import MacroGuardWatcher, build_macro_briefing


def _macro(level, mult=None, reasons=None):
    if mult is None:
        mult = {"안정": 1.0, "경계": 0.75, "위험": 0.5}[level]
    return {
        "success": True,
        "risk_level": level,
        "allocation_multiplier": mult,
        "reasons": reasons or [],
        "overlay_table": {"type": "table", "title": f"MACRO | {level}"},
    }


class _Seq:
    """호출될 때마다 다음 매크로 상태를 반환하는 스텁. 호출 인자(force_refresh) 기록."""
    def __init__(self, seq):
        self.seq = list(seq)
        self.i = 0
        self.calls = []

    def __call__(self, force_refresh=False):
        self.calls.append(force_refresh)
        item = self.seq[min(self.i, len(self.seq) - 1)]
        self.i += 1
        return item


def test_escalation_triggers_once():
    """안정→위험 격상 시 정확히 1회 브리핑, 위험 지속 중에는 중복 없음."""
    stub = _Seq([_macro("안정"), _macro("위험"), _macro("위험"), _macro("위험")])
    w = MacroGuardWatcher(status_fn=stub, poll_sec=5)

    assert w.evaluate_once() is None          # 안정 → 알림 없음
    assert w.is_briefed is False

    e1 = w.evaluate_once()                     # 위험 격상 → 브리핑 1회
    assert e1 is not None and e1["risk_level"] == "위험"
    assert w.is_briefed is True

    assert w.evaluate_once() is None           # 위험 지속 → 중복 차단
    assert w.evaluate_once() is None
    events = w.get_events()
    assert len(events) == 1                     # 큐에 정확히 1건만 적재
    print("✓ 격상 1회 브리핑 + 위험 지속 중 중복 차단")


def test_debounce_reset_then_retrigger():
    """위험→안정 회복 후 다시 위험으로 격상되면 재알림(디바운스 해제)."""
    stub = _Seq([_macro("위험"), _macro("안정"), _macro("위험")])
    w = MacroGuardWatcher(status_fn=stub, poll_sec=5)

    assert w.evaluate_once() is not None        # 1차 위험 → 브리핑
    assert w.evaluate_once() is None            # 안정 회복 → 디바운스 해제
    assert w.is_briefed is False
    assert w.evaluate_once() is not None        # 재격상 → 재브리핑
    assert len(w.get_events()) == 2
    print("✓ 회복 후 재격상 시 재알림(디바운스 해제)")


def test_warning_level_does_not_trigger():
    """'경계' 등급은 브리핑을 발동하지 않는다(위험만 발동)."""
    stub = _Seq([_macro("경계"), _macro("경계")])
    w = MacroGuardWatcher(status_fn=stub, poll_sec=5)
    assert w.evaluate_once() is None
    assert w.evaluate_once() is None
    assert w.get_events() == []
    print("✓ '경계' 등급은 선제 브리핑 미발동")


def test_failed_status_is_ignored():
    """수집 실패(success=False)는 무시하고 상태를 오염시키지 않는다."""
    stub = _Seq([{"success": False, "risk_level": "안정"}, _macro("위험")])
    w = MacroGuardWatcher(status_fn=stub, poll_sec=5)
    assert w.evaluate_once() is None            # 실패 → 무시
    assert w.evaluate_once() is not None        # 이후 정상 위험 → 브리핑
    print("✓ 수집 실패 응답 안전 무시")


def test_cache_friltering_uses_force_refresh_false():
    """폴링은 force_refresh=False 로만 조회해 stock_quant 5분 TTL 캐시를 소비한다."""
    stub = _Seq([_macro("안정")])
    w = MacroGuardWatcher(status_fn=stub, poll_sec=5)
    w.evaluate_once()
    assert stub.calls == [False], f"force_refresh 인자 예상과 다름: {stub.calls}"
    print("✓ 캐시 연동: force_refresh=False 로만 조회")


def test_dynamic_briefing_text():
    """위험(multiplier=0.5) 시 매수 상한 = base_cap(30)*0.5 = 15%, 사유 반영."""
    macro = _macro("위험", mult=0.5, reasons=["원달러 환율 1420원 위험", "VIX 27.5 위험"])
    text = build_macro_briefing(macro, base_cap=30)
    assert "15%" in text, text
    assert "환율" in text and "VIX" in text, text
    assert "위험 신호" in text
    print("✓ 동적 브리핑 텍스트:", text)


def test_event_payload_for_pipeline():
    """이벤트 payload 가 메인 루프(emergency_voice_briefing/overlay) 연동 필드를 포함."""
    stub = _Seq([_macro("위험", reasons=["VIX 급등"])])
    w = MacroGuardWatcher(status_fn=stub, poll_sec=5)
    evt = w.evaluate_once()
    for key in ("message", "overlay_table", "risk_level", "alert_text"):
        assert key in evt, f"누락 필드: {key}"
    assert isinstance(evt["message"], str) and evt["message"]
    print("✓ 이벤트 payload 파이프라인 연동 필드 완비")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        t()
        passed += 1
    print(f"\n✅ ALL {passed} TESTS PASSED")
