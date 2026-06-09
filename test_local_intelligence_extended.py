"""
Local Intelligence 확장 지표 단위 테스트 — Gemini 0 검증

실행:
  .venv/bin/python test_local_intelligence_extended.py
  .venv/bin/python -m pytest test_local_intelligence_extended.py -v
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


def _sample_df(n: int = 130, base: float = 100.0) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    closes = base + np.cumsum(rng.normal(0, 0.5, n))
    highs = closes + rng.uniform(0.2, 1.0, n)
    lows = closes - rng.uniform(0.2, 1.0, n)
    vols = rng.integers(1_000_000, 5_000_000, n)
    return pd.DataFrame({
        "Close": closes,
        "High": highs,
        "Low": lows,
        "Volume": vols,
    })


def test_volume_profile_poc():
    from local_rule_engine.quant_indicators import compute_volume_profile

    df = _sample_df()
    vp = compute_volume_profile(df)
    assert vp["poc"] is not None
    assert vp["val"] <= vp["poc"] <= vp["vah"] or vp["val"] <= vp["vah"]
    assert vp["signal"] != "데이터 없음"
    print("✓ volume profile POC/VA")


def test_ma_ribbon_squeeze_detection():
    from local_rule_engine.quant_indicators import compute_ma_ribbon

    df = _sample_df()
    rb = compute_ma_ribbon(df)
    assert rb["spread_pct"] is not None
    assert rb["state"] in (
        "squeeze", "breakout_bull", "bull_stack", "bear_stack", "wide", "neutral",
    )
    assert "sma_5" in rb["mas"]
    print("✓ MA ribbon spread/state")


def test_valuation_score_rules():
    from local_rule_engine.quant_indicators import compute_valuation_score

    good = compute_valuation_score({"per": 10, "pbr": 1.0, "roe": 22, "debt_to_equity": 50})
    bad = compute_valuation_score({"per": 60, "pbr": 5.0, "roe": -5, "debt_to_equity": 250})
    assert good["score"] > bad["score"]
    assert 0 <= good["score"] <= 100
    print("✓ valuation score rules")


def test_local_consensus_matrix_layers():
    from local_rule_engine.matrix_score import compute_local_consensus_matrix

    tech = {"signals": {"RSI": ("과매도", "BUY"), "MACD": ("상승세", "BUY")}}
    vp = {"poc": 100, "near_poc": True, "signal": "POC 부근"}
    rb = {"squeeze": True, "spread_pct": 1.5, "signal": "수렴", "breakout": False}
    val = {"score": 65, "grade": "적정"}

    with patch("stock_quant._compute_whale_layer_score", return_value=(15, "매수", [])), \
         patch("stock_quant._compute_social_layer_score", return_value=(14, "긍정", [])), \
         patch("stock_quant._compute_tech_layer_score", return_value=(18, "강세")):
        mx = compute_local_consensus_matrix(
            "NVDA", tech=tech, fundamentals={}, volume_profile=vp,
            ma_ribbon=rb, valuation=val, hybrid_score=60,
        )

    assert 0 <= mx["local_matrix_score"] <= 100
    assert "layers" in mx
    assert mx["layers"]["technical"]["score"] >= 18
    print("✓ local consensus matrix")


def test_extended_tool_execution_caches_metrics():
    from hybrid_router import try_local_tool_execution
    from local_intelligence.report_cache import set_report_path_for_tests, read_store, clear_reports

    mock_ctx = {
        "ticker": "NVDA",
        "query": "NVDA",
        "name": "NVIDIA",
        "consensus_price": 120.5,
        "change_pct": -1.2,
        "rsi": 42.0,
        "hybrid_score": 68,
        "bb_squeeze": True,
        "sentiment_score": 3,
        "compact_digest": "합의 120.5",
        "sources_ok": ["Yahoo"],
        "volume_profile": {"poc": 119.5, "support": 118.0, "signal": "지지"},
        "ma_ribbon": {"spread_pct": 2.1, "state": "squeeze", "signal": "수렴"},
        "valuation": {"score": 62, "grade": "적정", "per": 28, "roe": 18},
        "local_matrix": {"local_matrix_score": 71, "grade": "LOCAL BUY", "layers": {}},
        "local_matrix_score": 71,
        "local_matrix_grade": "LOCAL BUY",
        "tech_signals": {},
        "timeline_text": "",
        "anomaly_flag": False,
    }

    with tempfile.TemporaryDirectory() as td:
        rp = Path(td) / "local_analysis_report.json"
        set_report_path_for_tests(rp)
        try:
            with patch("local_intelligence.engine.gather_fusion_context", return_value=mock_ctx):
                result = try_local_tool_execution(
                    "analyze_stock_multi_source", {"query": "NVDA"},
                )
            assert result is not None
            assert result.get("gemini_called") is False
            assert result.get("local_intelligence") is True
            report = result.get("report") or {}
            assert report.get("volume_profile", {}).get("poc") == 119.5
            assert report.get("local_matrix_score") == 71
            assert report.get("gemini_called") is False
            assert "밸류에이션" in result.get("briefing", "") or report.get("narrative")
            store = read_store()
            cached = store["reports"]["NVDA"]
            assert cached["metrics_snapshot"]["local_matrix_score"] == 71
        finally:
            clear_reports()
            set_report_path_for_tests(None)
    print("✓ tool execution → extended cache, gemini_called false")


def test_build_compact_context_includes_extended():
    from local_intelligence.engine import build_compact_context

    ctx = {
        "name": "NVIDIA", "ticker": "NVDA", "consensus_price": 120,
        "change_pct": -1.0, "rsi": 40, "hybrid_score": 65, "bb_squeeze": True,
        "sentiment_score": 2,
        "volume_profile": {"poc": 119, "support": 118, "resistance": 122, "signal": "지지"},
        "ma_ribbon": {"spread_pct": 1.8, "state": "squeeze", "signal": "수렴"},
        "valuation": {"score": 60, "grade": "적정", "per": 30, "pbr": 8, "roe": 20},
        "local_matrix": {
            "local_matrix_score": 70, "grade": "LOCAL BUY",
            "layers": {"technical": {"score": 20}, "fundamental": {"score": 15},
                       "whale": {"score": 15}, "social": {"score": 12}},
        },
    }
    text = build_compact_context(ctx)
    assert "POC" in text
    assert "밸류에이션" in text
    assert "로컬종합매트릭스" in text
    assert "Open" not in text
    print("✓ compact context extended metrics")


def run_all():
    tests = [
        test_volume_profile_poc,
        test_ma_ribbon_squeeze_detection,
        test_valuation_score_rules,
        test_local_consensus_matrix_layers,
        test_extended_tool_execution_caches_metrics,
        test_build_compact_context_includes_extended,
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
