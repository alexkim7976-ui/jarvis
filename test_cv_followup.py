"""
cv_followup — 주식 2단계 분석(예비 답변 → 백그라운드 교차검증 + 멀티AI 정밀) 단위 테스트.
Gemini 0회 · 네트워크 0회 (refresh_local_analysis_report / cross_validation 모킹).
"""
from __future__ import annotations

import os
import sys
import time
import types
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv_followup
from speech_style import USER_DISPLAY_NAME


def _fake_rep(narrative="", price=None, sources=None, name=None):
    return types.SimpleNamespace(
        narrative=narrative,
        consensus_price=price,
        sources_ok=sources or [],
        name=name,
    )


def test_two_phase_enabled_env():
    saved = os.environ.get("JARVIS_STOCK_TWO_PHASE")
    try:
        os.environ["JARVIS_STOCK_TWO_PHASE"] = "1"
        assert cv_followup.two_phase_enabled() is True
        os.environ["JARVIS_STOCK_TWO_PHASE"] = "0"
        assert cv_followup.two_phase_enabled() is False
    finally:
        if saved is None:
            os.environ.pop("JARVIS_STOCK_TWO_PHASE", None)
        else:
            os.environ["JARVIS_STOCK_TWO_PHASE"] = saved
    print("✓ two_phase_enabled env 토글")


def test_event_queue_contract():
    q = cv_followup.CrossValFollowup()
    assert q.get_events() == []
    q._enqueue({"message": "A"})
    q._enqueue({"message": "B"})
    evs = q.get_events()
    assert [e["message"] for e in evs] == ["A", "B"]
    assert q.get_events() == []                       # 한 번 꺼내면 비워짐
    # requeue: 앞쪽 복원 + 순서 유지 (price_alert 계약과 동일)
    q._enqueue({"message": "C"})
    restored = q.get_events()
    q.requeue(restored)
    q._enqueue({"message": "D"})
    assert [e["message"] for e in q.get_events()] == ["C", "D"]
    print("✓ get_events/requeue 계약")


def test_build_followup_confirmation_tone():
    """예비가와 합의가 편차가 작으면(<1%) '확정' 톤 + 멀티AI 내러티브 포함."""
    rep = _fake_rep(
        narrative="기술과 가치 지표 모두 양호한 편이야.",
        price=100.0, sources=["Yahoo Finance", "FMP", "Finnhub"], name="테스트종목",
    )
    with patch("local_intelligence.refresh_local_analysis_report", return_value=rep):
        msg = cv_followup.build_followup_briefing(
            "테스트종목", preliminary_price=100.2, currency="USD")
    assert USER_DISPLAY_NAME in msg
    assert "일치" in msg
    assert "기술과 가치 지표 모두 양호" in msg
    print(f"✓ 확정 톤: {msg}")


def test_build_followup_correction_tone():
    """예비가와 합의가 편차가 크면(≥1%) '정정' 톤 + 채널 수 언급."""
    rep = _fake_rep(
        narrative="합의가가 갱신됐어.",
        price=120.0, sources=["Yahoo Finance", "FMP"], name="테스트종목",
    )
    with patch("local_intelligence.refresh_local_analysis_report", return_value=rep):
        msg = cv_followup.build_followup_briefing(
            "테스트종목", preliminary_price=100.0, currency="USD")
    assert "정정" in msg
    assert "채널" in msg
    print(f"✓ 정정 톤: {msg}")


def test_build_followup_no_preliminary():
    """예비가 없이도 멀티AI 교차검증 완료 브리핑 생성."""
    rep = _fake_rep(narrative="종합적으로 중립이야.", price=50.0,
                    sources=["Yahoo Finance", "Alpha Vantage", "FMP", "Finnhub"], name="X")
    with patch("local_intelligence.refresh_local_analysis_report", return_value=rep):
        msg = cv_followup.build_followup_briefing("X")
    assert USER_DISPLAY_NAME in msg
    assert "종합적으로 중립" in msg
    print(f"✓ 예비가 없음: {msg}")


def test_build_followup_fallback_to_cross_validation():
    """멀티에이전트 내러티브가 비면 순수 교차검증 브리핑으로 폴백."""
    from cross_validation import ValidationReport, FieldConsensus
    fc = FieldConsensus(final=None, accepted=[], rejected=[], method="테스트")
    price_fc = FieldConsensus(final=88.0, accepted=[("Yahoo Finance", 88.0)],
                              rejected=[], method="1/1")
    report = ValidationReport(
        query="테스트", name="테스트", currency="USD",
        sources_attempted=["Yahoo Finance"], sources_succeeded=["Yahoo Finance"],
        sources_failed=[], price=price_fc, volume=fc, per=fc, pbr=fc,
        market_cap=fc, eps=fc, change_pct=fc, extras={}, raw=[],
    )
    empty_rep = _fake_rep(narrative="", price=None, sources=[])
    with patch("local_intelligence.refresh_local_analysis_report", return_value=empty_rep), \
         patch("cross_validation.fetch_all_parallel", return_value=[]), \
         patch("cross_validation.cross_validate", return_value=report), \
         patch("cross_validation.format_briefing", return_value="교차검증 폴백 결과 요약."):
        msg = cv_followup.build_followup_briefing("테스트")
    assert "교차검증 폴백 결과 요약" in msg
    print(f"✓ 멀티에이전트 실패 → 교차검증 폴백: {msg}")


def test_start_debounce_and_enqueue():
    """start() 는 백그라운드 스레드로 브리핑 생성 후 큐 적재; 즉시 재요청은 디바운스."""
    q = cv_followup.CrossValFollowup()
    with patch.object(cv_followup, "build_followup_briefing", return_value="후속 브리핑"):
        assert q.start("AAPL", preliminary_price=100.0) is True
        assert q.start("AAPL") is False              # 디바운스 창 내 중복 무시
        # 백그라운드 스레드 완료 대기 (넉넉히)
        for _ in range(50):
            if q.pending():
                break
            time.sleep(0.02)
    evs = q.get_events()
    assert any(e.get("message") == "후속 브리핑" for e in evs)
    assert any(e.get("query") == "AAPL" for e in evs)
    print("✓ start 디바운스 + 큐 적재")


def test_empty_query_ignored():
    q = cv_followup.CrossValFollowup()
    assert q.start("") is False
    assert q.start("   ") is False
    print("✓ 빈 종목명 무시")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
    print(f"\n{passed}/{len(fns)} passed")
