"""
시나리오 시뮬레이터 결합 + HUD 테마 연동 검증 스크립트
실행: .venv/bin/python test_scenario_hud.py

1) portfolio_stress: 예상 MDD/리스크 가중치 산출, scenario_simulator 구조적 충격 결합,
   매크로 등급별 낙폭 가중, 캐시 동작
2) screen_overlay: 매크로 등급별 테마 색 스위칭, 위험 보더, 시나리오 섹션 렌더 무결성
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from portfolio_stress import estimate_position_stress, STRESS_SCENARIOS
from screen_overlay import ScreenOverlay, THEME_BGR


def _synth_df(seed=0, vol=0.02, n=120):
    rng = np.random.default_rng(seed)
    closes = 100 * np.cumprod(1 + rng.normal(0, vol, n))
    return pd.DataFrame({"Close": closes, "Volume": rng.integers(1e5, 1e6, n)})


def test_stress_basic_shape():
    df = _synth_df()
    res = estimate_position_stress("AAPL", df=df,
                                   macro_status={"risk_level": "안정"}, score=80,
                                   force_refresh=True)
    assert res["estimated_mdd_pct"] < 0, res
    assert 0.0 <= res["risk_weight"] <= 1.0
    assert len(res["scenarios"]) == len(STRESS_SCENARIOS)
    assert res["scenario_rows"] and res["alert_text"].startswith("CRITICAL SCENARIO")
    assert "EST.MDD" in res["alert_text"]
    print("✓ MDD/리스크가중치 산출 +", res["alert_text"])


def test_structural_simulator_linkage():
    """공급망 그래프 노드(LX세미콘 108320)는 구조적 충격이 반영, 비노드(AAPL)는 0."""
    df = _synth_df()
    member = estimate_position_stress("108320.KQ", df=df,
                                      macro_status={"risk_level": "경계"}, score=60,
                                      force_refresh=True)
    nonmember = estimate_position_stress("AAPL", df=df,
                                         macro_status={"risk_level": "경계"}, score=60,
                                         force_refresh=True)
    member_struct = max(s["structural"] for s in member["scenarios"])
    nonmember_struct = max(s["structural"] for s in nonmember["scenarios"])
    assert member_struct > 0, f"그래프 노드 구조적 충격 미반영: {member['scenarios']}"
    assert nonmember_struct == 0, f"비노드인데 구조적 충격 발생: {nonmember['scenarios']}"
    print(f"✓ scenario_simulator 결합: 노드 구조적충격={member_struct} > 비노드={nonmember_struct}")


def test_macro_level_amplifies_mdd():
    """동일 종목·점수에서 매크로 '위험'의 예상 손실이 '안정'보다 크다."""
    df = _synth_df()
    stable = estimate_position_stress("AAPL", df=df,
                                      macro_status={"risk_level": "안정"}, score=70,
                                      force_refresh=True)
    danger = estimate_position_stress("AAPL", df=df,
                                      macro_status={"risk_level": "위험"}, score=70,
                                      force_refresh=True)
    assert abs(danger["estimated_mdd_pct"]) > abs(stable["estimated_mdd_pct"]), \
        (stable["estimated_mdd_pct"], danger["estimated_mdd_pct"])
    print(f"✓ 매크로 등급 연동: 안정 {stable['estimated_mdd_pct']}% → 위험 {danger['estimated_mdd_pct']}%")


def test_cache_hit():
    df = _synth_df()
    a = estimate_position_stress("MSFT", df=df, macro_status={"risk_level": "안정"}, score=75,
                                 force_refresh=True)
    b = estimate_position_stress("MSFT", df=df, macro_status={"risk_level": "안정"}, score=75)
    assert a is b, "동일 키 재호출 시 캐시(동일 객체) 반환되어야 함"
    print("✓ 5분 TTL 캐시 적중(동일 객체 반환)")


def test_hud_theme_switching():
    ov = ScreenOverlay()
    assert ov._theme_color("안정") == THEME_BGR["안정"] == (255, 240, 0)
    assert ov._theme_color("경계") == THEME_BGR["경계"] == (0, 170, 255)
    assert ov._theme_color("위험") == THEME_BGR["위험"] == (85, 0, 255)
    ov.set_theme("위험")
    assert ov._theme_level == "위험"
    print("✓ HUD 테마 색 스위칭: 안정=Cyan / 경계=Orange / 위험=Crimson")


def test_hud_danger_border_renders():
    ov = ScreenOverlay(); ov.set_theme("위험")
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    out = ov.render(frame)                       # 데이터 없이도 위험 보더는 그려져야 함
    assert out.shape == frame.shape
    # 테두리에 적색(R 채널) 픽셀이 존재해야 함
    red_on_border = int(out[0:6, :, 2].max())
    assert red_on_border > 100, f"위험 보더 적색 미검출(max R={red_on_border})"
    print(f"✓ 위험 등급 깜빡이는 크림슨 보더 렌더(max R={red_on_border})")


def test_hud_scenario_section_renders():
    ov = ScreenOverlay(); ov.set_theme("위험")
    df = _synth_df()
    stress = estimate_position_stress("108320.KQ", df=df,
                                      macro_status={"risk_level": "위험"}, score=55,
                                      force_refresh=True)
    data = {
        "type": "table",
        "title": "LX세미콘 | 58/100 관망",
        "headers": ["지표", "값", "신호"],
        "rows": [["RSI", "32.1", "▲"], ["MACD", "-0.4", "▼"]],
        "theme": "위험",
        "scenario_rows": stress["scenario_rows"],
        "scenario_alert": stress["alert_text"],
    }
    ov.set_data(data, duration=15)
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    out = ov.render(frame)
    assert out.shape == frame.shape and int(out.max()) > 0, "시나리오 표 렌더 실패"
    print("✓ HUD 시나리오 스트레스 섹션 렌더 무결성 OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"\n✅ ALL {len(tests)} TESTS PASSED")
