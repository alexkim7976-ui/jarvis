"""
Hybrid Routing & Local Intelligence 격리 테스트

실행:
  .venv/bin/python test_hybrid_routing.py
  .venv/bin/python -m pytest test_hybrid_routing.py -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["HYBRID_ROUTING_ENABLED"] = "1"
os.environ["LOCAL_INTEL_OLLAMA_ENABLED"] = "0"  # 테스트: rule narrative


def test_light_trade_record_routes_ollama_zero_gemini():
    from hybrid_router import (
        HybridRoutingEngine, TaskTier, reset_call_counts,
        get_gemini_call_count, get_ollama_call_count,
    )

    reset_call_counts()
    ollama_fn = MagicMock(return_value="매매 기록 처리했습니다, 선생님.")
    gemini_fn = MagicMock(return_value="should not be called")

    engine = HybridRoutingEngine()
    cls = engine.classify("매매 기록 저장해 줘")
    assert cls.tier == TaskTier.LOCAL_TASK

    out = engine.ask(
        "매매 기록 저장해 줘", [], None,
        gemini_fn=gemini_fn,
        ollama_fn=ollama_fn,
        check_ollama=lambda: True,
    )

    assert ollama_fn.call_count == 1
    assert gemini_fn.call_count == 0
    assert get_ollama_call_count() == 1
    assert get_gemini_call_count() == 0
    print("✓ trade record → Ollama, Gemini 0")


def test_stock_price_analysis_routes_local():
    from hybrid_router import HybridRoutingEngine, TaskTier, reset_call_counts, get_gemini_call_count

    reset_call_counts()
    ollama_fn = MagicMock(return_value="엔비디아 합의가 120달러, RSI 45입니다.")
    gemini_fn = MagicMock()

    engine = HybridRoutingEngine()
    text = "엔비디아 주가랑 RSI 볼린저 1차 분석해 줘"
    assert engine.classify(text).tier == TaskTier.LOCAL_TASK

    engine.ask(text, [], None, gemini_fn=gemini_fn, ollama_fn=ollama_fn, check_ollama=lambda: True)
    assert ollama_fn.call_count == 1
    assert gemini_fn.call_count == 0
    assert get_gemini_call_count() == 0
    print("✓ stock 1st analysis → Ollama only")


def test_expert_monte_carlo_routes_gemini():
    from hybrid_router import HybridRoutingEngine, TaskTier, reset_call_counts, get_gemini_call_count

    reset_call_counts()
    ollama_fn = MagicMock()
    gemini_fn = MagicMock(return_value="몬테카를로 10000경로 해석입니다.")

    text = "몬테카를로 스트레스 테스트 결과 분석해 줘"
    engine = HybridRoutingEngine()
    assert engine.classify(text).tier == TaskTier.EXPERT_TASK

    out = engine.ask(text, [], None, gemini_fn=gemini_fn, ollama_fn=ollama_fn, check_ollama=lambda: True)
    assert gemini_fn.call_count == 1
    assert ollama_fn.call_count == 0
    assert get_gemini_call_count() == 1
    print("✓ monte carlo → Gemini only")


def test_expert_macro_causal_routes_gemini():
    from hybrid_router import HybridRoutingEngine, TaskTier, reset_call_counts, get_gemini_call_count

    reset_call_counts()
    gemini_fn = MagicMock(return_value="구조적 충격 인과 분석")
    ollama_fn = MagicMock()

    text = "VIX 금리 환율과 공급망 밸류체인 간 매크로 리스크 인과관계 추론해 줘"
    engine = HybridRoutingEngine()
    assert engine.classify(text).tier == TaskTier.EXPERT_TASK

    engine.ask(text, [], None, gemini_fn=gemini_fn, ollama_fn=ollama_fn, check_ollama=lambda: True)
    assert gemini_fn.call_count == 1
    assert get_gemini_call_count() == 1
    print("✓ macro causal → Gemini only")


def test_ollama_down_fallback_to_gemini():
    from hybrid_router import HybridRoutingEngine, reset_call_counts, get_gemini_call_count

    reset_call_counts()
    gemini_fn = MagicMock(return_value="Gemini 폴백")
    engine = HybridRoutingEngine()

    decision = engine.route("매매 기록 저장해 줘", check_ollama=lambda: False)
    assert decision.backend == "gemini"
    assert decision.fallback is True

    engine.ask(
        "매매 기록 저장해 줘", [], None,
        gemini_fn=gemini_fn, ollama_fn=MagicMock(), check_ollama=lambda: False,
    )
    assert gemini_fn.call_count == 1
    assert get_gemini_call_count() == 1
    print("✓ ollama offline → Gemini fallback")


def test_ollama_exception_fallback_to_gemini():
    from hybrid_router import HybridRoutingEngine, reset_call_counts

    reset_call_counts()

    def fail_ollama(*a, **k):
        raise ConnectionError("down")

    gemini_fn = MagicMock(return_value="복구됨")
    HybridRoutingEngine().ask(
        "파일 목록", [], None,
        gemini_fn=gemini_fn, ollama_fn=fail_ollama, check_ollama=lambda: True,
    )
    assert gemini_fn.call_count == 1
    print("✓ ollama exception → Gemini fallback")


def test_hybrid_disabled_legacy():
    from hybrid_router import ask_with_hybrid_routing, reset_call_counts, get_ollama_call_count

    os.environ["HYBRID_ROUTING_ENABLED"] = "0"
    reset_call_counts()
    try:
        gemini_fn = MagicMock(return_value="legacy")
        ollama_fn = MagicMock()
        out = ask_with_hybrid_routing(
            "매매 기록", [], None,
            gemini_fn=gemini_fn, ollama_fn=ollama_fn, check_ollama=lambda: True,
        )
        assert out == "legacy"
        assert gemini_fn.call_count == 1
        assert get_ollama_call_count() == 0
    finally:
        os.environ["HYBRID_ROUTING_ENABLED"] = "1"
    print("✓ HYBRID_ROUTING_ENABLED=0 legacy")


def test_tool_classification_local_vs_expert():
    from hybrid_router import classify_tool, TaskTier

    assert classify_tool("record_stock_trade").tier == TaskTier.LOCAL_TASK
    assert classify_tool("analyze_stock_multi_source").tier == TaskTier.LOCAL_TASK
    assert classify_tool("run_strategy_backtest").tier == TaskTier.EXPERT_TASK
    assert classify_tool("simulate_supply_chain_scenario").tier == TaskTier.EXPERT_TASK
    print("✓ tool tier classification")


def test_local_tool_execution_caches_report_zero_gemini():
    from hybrid_router import try_local_tool_execution, reset_call_counts, get_gemini_call_count
    from local_intelligence.report_cache import set_report_path_for_tests, read_store, clear_reports

    reset_call_counts()
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
        "compact_digest": "Yahoo/FMP 합의 120.5",
        "sources_ok": ["Yahoo Finance"],
        "tech_signals": {},
        "timeline_text": "합의가 120.5",
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
            assert "120.5" in result.get("briefing", "") or result.get("success")
            store = read_store()
            assert "NVDA" in store.get("reports", {})
            assert get_gemini_call_count() == 0
        finally:
            clear_reports()
            set_report_path_for_tests(None)
    print("✓ local tool → report cache, Gemini 0")


def run_all():
    tests = [
        test_light_trade_record_routes_ollama_zero_gemini,
        test_stock_price_analysis_routes_local,
        test_expert_monte_carlo_routes_gemini,
        test_expert_macro_causal_routes_gemini,
        test_ollama_down_fallback_to_gemini,
        test_ollama_exception_fallback_to_gemini,
        test_hybrid_disabled_legacy,
        test_tool_classification_local_vs_expert,
        test_local_tool_execution_caches_report_zero_gemini,
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
    print(f"\n{'='*50}")
    print(f"결과: {len(tests)-failed}/{len(tests)} passed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
