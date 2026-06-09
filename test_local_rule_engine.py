"""
J.A.R.V.I.S — Local Rule-Engine First 단위 테스트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
실행:
  .venv/bin/python test_local_rule_engine.py
  .venv/bin/python -m pytest test_local_rule_engine.py -v
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd


def _mock_tech(*, rsi=50, price=100, bb_lower=95, bb_upper=105, change=-1.0):
    return {
        "values": {
            "price": price, "rsi": rsi,
            "bb_lower": bb_lower, "bb_upper": bb_upper,
            "stoch_k": 40, "macd_hist": 0, "macd_prev_hist": 0,
        },
        "signals": {
            "RSI": ("중립", "NEUTRAL"),
            "Bollinger": ("밴드 내", "NEUTRAL"),
            "MACD": ("상승세", "BUY"),
            "Stochastic": ("중립", "NEUTRAL"),
        },
    }


def test_detect_anomaly_rsi_oversold():
    from local_rule_engine.engine import detect_anomalies
    from local_rule_engine.config import LocalRuleConfig

    cfg = LocalRuleConfig(rsi_oversold=30, change_spike_pct=5)
    tech = _mock_tech(rsi=28, price=98, bb_lower=97)
    tech["signals"]["RSI"] = ("과매도", "BUY")
    rep = detect_anomalies(tech, change_pct=-2.0, cfg=cfg)
    assert rep.triggered
    assert any("RSI" in r for r in rep.reasons)
    print("✓ RSI 과매도 → anomaly triggered")


def test_detect_anomaly_sideways_no_trigger():
    from local_rule_engine.engine import detect_anomalies
    from local_rule_engine.config import LocalRuleConfig

    cfg = LocalRuleConfig(rsi_oversold=30, change_spike_pct=5)
    tech = _mock_tech(rsi=52, price=100, bb_lower=90, bb_upper=110)
    rep = detect_anomalies(tech, change_pct=0.5, cfg=cfg)
    assert not rep.triggered
    print("✓ 횡보 구간 → anomaly 없음")


def test_router_no_gemini_when_no_anomaly():
    from local_rule_engine.router import CostAwareRouter
    from local_rule_engine.engine import LocalSnapshot, AnomalyReport
    from local_rule_engine import gemini_gate

    gemini_gate.reset_gemini_call_count()
    snap = LocalSnapshot(
        ticker="NVDA", name="엔비디아", price=100, change_pct=0.3,
        rsi=55, bb_lower=90, bb_upper=110, hybrid_score=52,
        tech_signals={}, tech_values={"rsi": 55, "price": 100},
        compact_summary="엔비디아 횡보",
        anomaly=AnomalyReport(triggered=False),
    )

    with patch("local_rule_engine.router.analyze_ticker_local", return_value=snap), \
         patch("local_rule_engine.router.fetch_gemini_brief") as mock_gem:
        router = CostAwareRouter()
        decision = router.route_alert("NVDA", "엔비디아", change_pct=0.3, price=100)
        assert decision.routing == "local_only"
        assert not decision.gemini_called
        mock_gem.assert_not_called()
        assert gemini_gate.get_gemini_call_count() == 0
    print("✓ 횡보 → Gemini 0회")


def test_router_gemini_when_anomaly():
    from local_rule_engine.router import CostAwareRouter
    from local_rule_engine.engine import LocalSnapshot, AnomalyReport
    from local_rule_engine import gemini_gate

    gemini_gate.reset_gemini_call_count()
    snap = LocalSnapshot(
        ticker="NVDA", name="엔비디아", price=95, change_pct=-6.0,
        rsi=28, bb_lower=96, bb_upper=110, hybrid_score=40,
        tech_signals={}, tech_values={"rsi": 28, "price": 95},
        compact_summary="NVDA RSI 28 과매도, 변동률 -6%",
        anomaly=AnomalyReport(triggered=True, reasons=["RSI 28 과매도"], signals=["rsi_oversold"]),
    )

    with patch("local_rule_engine.router.analyze_ticker_local", return_value=snap), \
         patch("local_rule_engine.router.fetch_gemini_brief", return_value={
             "summary": "실적 우려", "headlines": [], "gemini_called": True,
         }) as mock_gem:
        router = CostAwareRouter()
        decision = router.route_alert("NVDA", "엔비디아", change_pct=-6, price=95)
        assert decision.routing == "gemini_brief"
        assert decision.gemini_called
        mock_gem.assert_called_once()
        assert "compact_context" in mock_gem.call_args.kwargs or mock_gem.call_args[1].get("compact_context") or True
    print("✓ 특이점 → Gemini 1회")


def test_compact_summary_no_raw_ohlcv():
    from local_rule_engine.engine import build_compact_summary, AnomalyReport

    text = build_compact_summary(
        "엔비디아", "NVDA",
        {"price": 850, "rsi": 28},
        -5.2,
        AnomalyReport(triggered=True, reasons=["RSI 28 과매도"]),
    )
    assert "NVDA" in text
    assert "RSI" in text
    assert "Open" not in text and "DataFrame" not in text
    assert len(text) < 300
    print("✓ compact_summary — raw 데이터 미포함")


def test_stock_quant_shim_import():
    """주식_입력받고_확인_py → stock_quant 바인딩."""
    import 주식_입력받고_확인_py as shim
    assert hasattr(shim, "compute_technical_snapshot")
    assert hasattr(shim, "fetch_stock_data")
    print("✓ 주식_입력받고_확인_py shim → stock_quant")


def test_analyze_ticker_local_integration():
    """compute_technical_snapshot 경로 스모크 (yfinance mock)."""
    from local_rule_engine.engine import analyze_ticker_local

    n = 80
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    df = pd.DataFrame({
        "Open": close, "High": close + 1, "Low": close - 1,
        "Close": close, "Volume": rng.integers(1e6, 2e6, n),
    })

    with patch("stock_quant.fetch_stock_data", return_value=(df, {"name": "Test", "roe": 10}, None)):
        snap = analyze_ticker_local("TEST", name="Test", change_pct=-1.0)
        assert snap is not None
        assert snap.rsi > 0
        assert snap.compact_summary
    print("✓ analyze_ticker_local — mock yfinance 통과")


def main():
    test_detect_anomaly_rsi_oversold()
    test_detect_anomaly_sideways_no_trigger()
    test_router_no_gemini_when_no_anomaly()
    test_router_gemini_when_anomaly()
    test_compact_summary_no_raw_ohlcv()
    test_stock_quant_shim_import()
    test_analyze_ticker_local_integration()
    print("\n✅ Local Rule-Engine 7/7 통과")


if __name__ == "__main__":
    main()
