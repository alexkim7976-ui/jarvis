"""
로컬 DCF · 그레이엄 밸류에이션 단위 테스트 — Gemini 0

실행:
  .venv/bin/python test_local_valuation.py
  .venv/bin/python -m pytest test_local_valuation.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["LOCAL_INTEL_OLLAMA_ENABLED"] = "0"
os.environ["HYBRID_ROUTING_ENABLED"] = "1"


def test_dcf_fair_price_math():
    """가상 재무 데이터 → DCF 적정가·안전마진 수학 검증."""
    from local_rule_engine.engine import calculate_local_dcf_valuation

    # FCF 10억, g=10%, WACC=10%, 5년, 부채/현금 0, 주식 1억주
    dcf = calculate_local_dcf_valuation(
        fcf=1_000_000_000,
        total_debt=0,
        cash=0,
        shares_outstanding=100_000_000,
        current_price=80.0,
        fcf_growth=10.0,
        wacc=0.10,
        terminal_growth=0.02,
        projection_years=5,
    )

    assert dcf["fair_price"] is not None
    # PV(FCF)=5B, PV(TV)≈12.75B → EV≈17.75B → $177.5/주
    assert 170 <= dcf["fair_price"] <= 185
    assert dcf["margin_of_safety_pct"] is not None
    assert dcf["margin_of_safety_pct"] > 50
    assert dcf["undervalued"] is True
    assert dcf["wacc_pct"] == 10.0
    print(f"✓ DCF fair=${dcf['fair_price']:.2f} MoS={dcf['margin_of_safety_pct']:.1f}%")


def test_graham_intrinsic_math():
    """Graham V = EPS × (8.5 + 2g) × 4.4 / Y — Y=4.4이면 V = EPS × (8.5+2g)."""
    from local_rule_engine.engine import calculate_graham_intrinsic_value

    gr = calculate_graham_intrinsic_value(
        eps=5.0,
        growth_pct=10.0,
        treasury_yield_pct=4.4,
        current_price=100.0,
    )

    expected = 5.0 * (8.5 + 2 * 10.0)  # 142.5
    assert gr["intrinsic_value"] is not None
    assert abs(gr["intrinsic_value"] - expected) < 0.01
    expected_mos = (expected - 100) / expected * 100
    assert abs(gr["undervaluation_pct"] - expected_mos) < 0.1
    assert gr["undervalued"] is True
    print(f"✓ Graham V=${gr['intrinsic_value']:.2f} underval={gr['undervaluation_pct']:.1f}%")


def test_dcf_default_wacc_deterministic():
    """WACC 미입력 시 9% 기본값 적용."""
    from local_rule_engine.valuation_models import calculate_local_dcf_valuation

    d1 = calculate_local_dcf_valuation(
        fcf=500_000_000, shares_outstanding=50_000_000, wacc=None,
    )
    d2 = calculate_local_dcf_valuation(
        fcf=500_000_000, shares_outstanding=50_000_000, wacc=0.09,
    )
    assert d1["wacc_pct"] == 9.0
    assert d1["fair_price"] == d2["fair_price"]
    print("✓ DCF default WACC 9%")


def test_graham_treasury_from_macro():
    """get_macro_market_status ^TNX 재사용."""
    from local_rule_engine.valuation_models import get_treasury_yield_pct

    mock_macro = {
        "indicators": {"^TNX": {"value": 4.25}},
    }
    with patch("stock_quant.get_macro_market_status", return_value=mock_macro):
        y = get_treasury_yield_pct()
    assert y == 4.25
    print("✓ treasury yield from macro cache")


def test_run_local_valuation_suite():
    """fundamentals dict → DCF + Graham 일괄."""
    from local_rule_engine.valuation_models import run_local_valuation_suite

    fundamentals = {
        "price": 90.0,
        "eps": 4.0,
        "fcf": 800_000_000,
        "total_debt": 1_000_000_000,
        "cash": 500_000_000,
        "shares_outstanding": 80_000_000,
        "revenue_growth": 8.0,
        "earnings_growth": 12.0,
    }
    with patch(
        "local_rule_engine.valuation_models.fetch_valuation_inputs",
        return_value={
            "fcf": 800_000_000,
            "total_debt": 1_000_000_000,
            "cash": 500_000_000,
            "shares_outstanding": 80_000_000,
            "fcf_growth": 8.0,
            "eps": 4.0,
            "earnings_growth": 12.0,
            "price": 90.0,
        },
    ):
        suite = run_local_valuation_suite("TEST", fundamentals, current_price=90.0)

    assert suite["dcf"]["fair_price"] is not None
    assert suite["graham"]["intrinsic_value"] is not None
    print("✓ valuation suite batch")


def test_cache_includes_dcf_graham_gemini_zero():
    """refresh_local_analysis_report → local_analysis_report에 DCF/Graham, gemini_called=false."""
    from local_intelligence.engine import refresh_local_analysis_report, format_tool_response
    from local_intelligence.report_cache import set_report_path_for_tests, read_store, clear_reports
    from local_intelligence.multi_agent import set_ollama_stage_fn

    mock_ctx = {
        "ticker": "AAPL",
        "query": "AAPL",
        "name": "Apple",
        "consensus_price": 100.0,
        "change_pct": 0.5,
        "rsi": 50.0,
        "hybrid_score": 60,
        "bb_squeeze": False,
        "sentiment_score": 1,
        "compact_digest": "합의 100",
        "sources_ok": ["Yahoo"],
        "volume_profile": {},
        "ma_ribbon": {},
        "valuation": {"score": 55},
        "local_matrix": {"local_matrix_score": 65, "grade": "HOLD"},
        "local_matrix_score": 65,
        "local_matrix_grade": "HOLD",
        "dcf_valuation": {
            "fair_price": 130.0,
            "margin_of_safety_pct": 23.08,
            "signal": "저평가",
            "undervalued": True,
        },
        "graham_valuation": {
            "intrinsic_value": 125.0,
            "undervaluation_pct": 20.0,
            "signal": "저평가",
            "undervalued": True,
        },
        "tech_signals": {},
        "timeline_text": "",
        "anomaly_flag": False,
    }

    _saved = {k: os.environ.get(k) for k in ("LOCAL_INTEL_OLLAMA_ENABLED", "LOCAL_INTEL_MULTI_AGENT")}
    os.environ["LOCAL_INTEL_OLLAMA_ENABLED"] = "1"
    os.environ["LOCAL_INTEL_MULTI_AGENT"] = "1"
    set_ollama_stage_fn(lambda s, p: {
        "agent_a": "기술 요약", "agent_b": "가치 요약",
        "orchestrator": "DCF 적정주가와 그레이엄 내재가치 기준 저평가로 보여.",
    }.get(s, ""))

    with tempfile.TemporaryDirectory() as td:
        rp = Path(td) / "local_analysis_report.json"
        set_report_path_for_tests(rp)
        try:
            with patch("local_intelligence.engine.gather_fusion_context", return_value=mock_ctx):
                result = format_tool_response(
                    refresh_local_analysis_report("AAPL", force_ollama=True)
                )
            assert result is not None
            assert result.get("gemini_called") is False
            report = result.get("report") or {}
            assert report.get("dcf_valuation", {}).get("fair_price") == 130.0
            assert report.get("graham_valuation", {}).get("intrinsic_value") == 125.0
            assert report.get("gemini_called") is False
            store = read_store()
            snap = store["reports"]["AAPL"]["metrics_snapshot"]
            assert snap["dcf_valuation"]["fair_price"] == 130.0
            assert snap["graham_valuation"]["intrinsic_value"] == 125.0
            briefing = result.get("briefing", "")
            assert "DCF" in briefing or "적정" in briefing or "그레이엄" in briefing
        finally:
            clear_reports()
            set_report_path_for_tests(None)
            set_ollama_stage_fn(None)
            for _k, _v in _saved.items():
                if _v is None:
                    os.environ.pop(_k, None)
                else:
                    os.environ[_k] = _v
    print("✓ cache DCF/Graham + gemini_called false")


def test_build_compact_context_valuation_fields():
    from local_intelligence.engine import build_compact_context

    ctx = {
        "name": "Apple", "ticker": "AAPL", "consensus_price": 100,
        "change_pct": 0.0, "rsi": 50, "hybrid_score": 60, "bb_squeeze": False,
        "sentiment_score": 0,
        "dcf_valuation": {"fair_price": 120, "margin_of_safety_pct": 16.7, "wacc_pct": 9},
        "graham_valuation": {"intrinsic_value": 115, "undervaluation_pct": 13.0, "eps": 6.5},
    }
    text = build_compact_context(ctx)
    assert "DCF적정주가" in text
    assert "그레이엄내재가치" in text
    print("✓ compact context DCF/Graham lines")


def run_all():
    tests = [
        test_dcf_fair_price_math,
        test_graham_intrinsic_math,
        test_dcf_default_wacc_deterministic,
        test_graham_treasury_from_macro,
        test_run_local_valuation_suite,
        test_cache_includes_dcf_graham_gemini_zero,
        test_build_compact_context_valuation_fields,
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
