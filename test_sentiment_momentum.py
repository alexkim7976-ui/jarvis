"""
로컬 뉴스 감성 모멘텀(Sentiment MA) 단위 테스트 — Gemini 0

실행:
  .venv/bin/python test_sentiment_momentum.py
  .venv/bin/python -m pytest test_sentiment_momentum.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["LOCAL_INTEL_OLLAMA_ENABLED"] = "0"
os.environ["HYBRID_ROUTING_ENABLED"] = "1"


def _make_sentiment_series(scores: list[int], start: str = "2026-01-01") -> pd.DataFrame:
    dates = pd.date_range(start, periods=len(scores), freq="D")
    return pd.DataFrame({"score": scores}, index=dates)


def test_sentiment_ma_computation():
    from cross_validation import build_sentiment_ma_frame

    scores = [-2] * 15 + [0, 2, 4, 6, 8, 10, 12, 14, 16, 18]
    df = _make_sentiment_series(scores)
    frame = build_sentiment_ma_frame(df)

    assert frame["ma5"].notna().sum() >= 1
    assert frame["ma20"].notna().sum() >= 1
    ma5 = float(frame["ma5"].dropna().iloc[-1])
    ma20 = float(frame["ma20"].dropna().iloc[-1])
    assert ma5 > ma20
    print(f"✓ Sentiment MA5={ma5:.1f} MA20={ma20:.1f}")


def test_golden_cross_detection():
    from local_rule_engine.sentiment_momentum import detect_sentiment_golden_cross

    # MA5 하향→상향 돌파 시나리오
    frame = pd.DataFrame({
        "score": [0.0] * 5,
        "ma5": [-1.0, -0.5, 0.0, 0.5, 2.0],
        "ma20": [1.0, 1.0, 1.0, 1.0, 1.0],
    })
    golden, txt = detect_sentiment_golden_cross(frame)
    assert golden is True
    assert "골든크로스" in txt
    print("✓ sentiment golden cross detected")


def test_bullish_divergence_detection():
    from local_rule_engine.sentiment_momentum import detect_bullish_sentiment_divergence
    from cross_validation import build_sentiment_ma_frame

    n = 25
    prices = pd.Series(
        np.linspace(110, 100, n),
        index=pd.date_range("2026-01-01", periods=n, freq="D"),
    )
    # 전반 저조 → 후반 급반등
    scores = [-4] * 12 + [-2] * 5 + [2, 4, 6, 8, 10, 12, 14, 16]
    sent = _make_sentiment_series(scores, start="2026-01-01")
    frame = build_sentiment_ma_frame(sent)

    signal, meta = detect_bullish_sentiment_divergence(prices, frame)
    assert signal
    assert "다이버전스" in signal
    assert meta.get("ma5_slope", 0) > 0
    print(f"✓ bullish divergence: {signal[:50]}...")


def test_record_and_load_timeseries():
    from cross_validation import (
        record_sentiment_observation,
        load_sentiment_timeseries,
        compute_sentiment_ma,
        set_sentiment_timeseries_path_for_tests,
    )

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "sentiment_timeseries.json"
        set_sentiment_timeseries_path_for_tests(path)
        try:
            for i, sc in enumerate([-2, -1, 0, 2, 4, 6, 8, 10, 12, 14,
                                    16, 18, 20, 18, 16, 14, 12, 10, 8, 6,
                                    8, 10, 12, 14, 16]):
                record_sentiment_observation("TEST", sc, timestamp=1704067200 + i * 86400)

            df = load_sentiment_timeseries("TEST")
            assert len(df) == 25
            ma = compute_sentiment_ma("TEST")
            assert ma["ma5"] is not None
            assert ma["ma20"] is not None
        finally:
            set_sentiment_timeseries_path_for_tests(None)
    print("✓ record/load/compute_sentiment_ma")


def test_analyze_sentiment_momentum_pipeline():
    from cross_validation import (
        record_sentiment_observation,
        set_sentiment_timeseries_path_for_tests,
    )
    from local_rule_engine.sentiment_momentum import analyze_sentiment_momentum

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "sentiment_timeseries.json"
        set_sentiment_timeseries_path_for_tests(path)
        try:
            scores = [-3] * 18 + [0, 2, 5, 8, 10, 12, 15, 18, 20, 22]
            for i, sc in enumerate(scores):
                record_sentiment_observation("MOMO", sc, timestamp=1704067200 + i * 86400)

            prices = pd.Series(
                np.linspace(105, 100, len(scores)),
                index=pd.date_range("2026-01-01", periods=len(scores), freq="D"),
            )
            with patch("stock_quant.fetch_stock_data", return_value=(None, None, "mock")):
                result = analyze_sentiment_momentum("MOMO", price_closes=prices)

            assert result["sentiment_ma5"] is not None
            assert result["sentiment_ma20"] is not None
            assert isinstance(result["sentiment_golden_cross"], bool)
        finally:
            set_sentiment_timeseries_path_for_tests(None)
    print("✓ analyze_sentiment_momentum pipeline")


def test_cache_sentiment_fields_gemini_zero():
    from local_intelligence.engine import refresh_local_analysis_report, format_tool_response
    from local_intelligence.report_cache import set_report_path_for_tests, read_store, clear_reports
    from local_intelligence.multi_agent import set_ollama_stage_fn

    mock_ctx = {
        "ticker": "NVDA",
        "query": "NVDA",
        "name": "NVIDIA",
        "consensus_price": 100.0,
        "change_pct": -0.5,
        "rsi": 45.0,
        "hybrid_score": 62,
        "bb_squeeze": False,
        "sentiment_score": 6,
        "sentiment_ma5": 4.2,
        "sentiment_ma20": 2.1,
        "sentiment_golden_cross": True,
        "sentiment_divergence_signal": "강세 다이버전스 — 주가 -1.2% 횡보·하락 중 뉴스 MA5 반등",
        "compact_digest": "합의 100",
        "sources_ok": ["Yahoo"],
        "volume_profile": {},
        "ma_ribbon": {},
        "valuation": {},
        "local_matrix": {},
        "dcf_valuation": {},
        "graham_valuation": {},
        "tech_signals": {},
        "timeline_text": "",
        "anomaly_flag": False,
    }

    _saved = {k: os.environ.get(k) for k in ("LOCAL_INTEL_OLLAMA_ENABLED", "LOCAL_INTEL_MULTI_AGENT")}
    os.environ["LOCAL_INTEL_OLLAMA_ENABLED"] = "1"
    os.environ["LOCAL_INTEL_MULTI_AGENT"] = "1"
    set_ollama_stage_fn(lambda s, p: {
        "agent_a": "기술 요약", "agent_b": "가치 요약",
        "orchestrator": "감성 골든크로스와 다이버전스 신호가 함께 나오고 있어.",
    }.get(s, ""))

    with tempfile.TemporaryDirectory() as td:
        rp = Path(td) / "local_analysis_report.json"
        set_report_path_for_tests(rp)
        try:
            with patch("local_intelligence.engine.gather_fusion_context", return_value=mock_ctx):
                result = format_tool_response(
                    refresh_local_analysis_report("NVDA", force_ollama=True)
                )
            assert result is not None
            assert result.get("gemini_called") is False
            report = result.get("report") or {}
            assert report.get("sentiment_golden_cross") is True
            assert "다이버전스" in report.get("sentiment_divergence_signal", "")
            assert report.get("gemini_called") is False
            store = read_store()
            snap = store["reports"]["NVDA"]["metrics_snapshot"]
            assert snap["sentiment_golden_cross"] is True
            assert snap["sentiment_ma5"] == 4.2
            briefing = result.get("briefing", "")
            assert "골든크로스" in briefing or "다이버전스" in briefing
        finally:
            clear_reports()
            set_report_path_for_tests(None)
            set_ollama_stage_fn(None)
            for _k, _v in _saved.items():
                if _v is None:
                    os.environ.pop(_k, None)
                else:
                    os.environ[_k] = _v
    print("✓ cache sentiment fields + gemini_called false")


def test_build_compact_context_sentiment_ma():
    from local_intelligence.engine import build_compact_context

    ctx = {
        "name": "NVDA", "ticker": "NVDA", "consensus_price": 100,
        "change_pct": -1.0, "rsi": 40, "hybrid_score": 60, "bb_squeeze": False,
        "sentiment_score": 5, "sentiment_ma5": 3.5, "sentiment_ma20": 1.2,
        "sentiment_golden_cross": True,
        "sentiment_divergence_signal": "강세 다이버전스",
    }
    text = build_compact_context(ctx)
    assert "감성MA5" in text
    assert "골든크로스" in text
    assert "감성다이버전스" in text
    print("✓ compact context sentiment MA lines")


def run_all():
    tests = [
        test_sentiment_ma_computation,
        test_golden_cross_detection,
        test_bullish_divergence_detection,
        test_record_and_load_timeseries,
        test_analyze_sentiment_momentum_pipeline,
        test_cache_sentiment_fields_gemini_zero,
        test_build_compact_context_sentiment_ma,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
        except Exception as e:
            failed += 1
            print(f"✗ {fn.__name__}: {e}")
            import traceback
            traceback.print_exc()
    print(f"\n{'='*50}\n결과: {len(tests)-failed}/{len(tests)} passed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
