"""
Ollama Multi-Persona CoT 오케스트레이터 단위 테스트 — Gemini 0

실행:
  .venv/bin/python test_local_multi_agent.py
  .venv/bin/python -m pytest test_local_multi_agent.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["LOCAL_INTEL_OLLAMA_ENABLED"] = "1"
os.environ["LOCAL_INTEL_MULTI_AGENT"] = "1"
os.environ["HYBRID_ROUTING_ENABLED"] = "1"


def _sample_ctx() -> dict:
    return {
        "ticker": "NVDA",
        "name": "NVIDIA",
        "consensus_price": 120.0,
        "change_pct": -1.5,
        "rsi": 42.0,
        "hybrid_score": 68,
        "bb_squeeze": True,
        "sentiment_score": 5,
        "sentiment_ma5": 3.2,
        "sentiment_ma20": 1.0,
        "sentiment_golden_cross": True,
        "sentiment_divergence_signal": "강세 다이버전스 — 주가 횡보 중 MA5 반등",
        "volume_profile": {
            "poc": 119.0, "support": 117.5, "resistance": 122.0,
            "signal": "POC 지지",
        },
        "ma_ribbon": {
            "spread_pct": 2.1, "state": "squeeze",
            "signal": "이평선 수렴 — 변동성 확대 임박",
        },
        "valuation": {"score": 62, "grade": "적정", "per": 28, "roe": 18},
        "local_matrix": {
            "local_matrix_score": 71, "grade": "LOCAL BUY",
            "layers": {
                "technical": {"score": 20},
                "fundamental": {"score": 15},
            },
        },
        "dcf_valuation": {
            "fair_price": 145.0, "margin_of_safety_pct": 17.2,
            "wacc_pct": 9.0, "signal": "저평가",
        },
        "graham_valuation": {
            "intrinsic_value": 138.0, "undervaluation_pct": 13.0,
        },
        "compact_digest": "합의 120",
    }


def test_agent_a_prompt_structure():
    from local_intelligence.multi_agent import build_agent_a_prompt

    prompt = build_agent_a_prompt(_sample_ctx())
    assert "에이전트 A" in prompt
    assert "기술적 분석가" in prompt
    assert "Chain-of-Thought" in prompt
    assert "POC=119.0" in prompt
    assert "골든크로스" in prompt
    assert "다이버전스" in prompt
    print("✓ Agent A prompt CoT + technical fields")


def test_agent_b_prompt_cross_validation():
    from local_intelligence.multi_agent import build_agent_b_prompt

    agent_a = "1) RSI 42 관측. 2) 수렴 구간. 3) 기술적 반등 임박."
    prompt = build_agent_b_prompt(_sample_ctx(), agent_a)
    assert "에이전트 B" in prompt
    assert "가치 투자자" in prompt
    assert "교차 검증" in prompt
    assert agent_a in prompt
    assert "DCF적정=$145.0" in prompt
    assert "그레이엄=$138.0" in prompt
    print("✓ Agent B prompt cross-validation + value fields")


def test_orchestrator_prompt_synthesis():
    from local_intelligence.multi_agent import build_orchestrator_prompt
    from local_intelligence.engine import build_compact_context

    ctx = _sample_ctx()
    ctx = _sample_ctx()
    compact = build_compact_context(ctx)
    prompt = build_orchestrator_prompt(ctx, compact, "A리포트", "B리포트")
    assert "오케스트레이터" in prompt
    assert "종합 투자 매트릭스" in prompt
    assert "A리포트" in prompt
    assert "B리포트" in prompt
    assert "선생님," in prompt
    assert "DCF적정주가" in compact or "DCF적정" in compact
    print("✓ Orchestrator synthesis prompt")


def test_three_stage_pipeline_mock():
    from local_intelligence.multi_agent import run_multi_agent_cot, set_ollama_stage_fn

    calls: list[str] = []

    def mock_stage(stage: str, prompt: str) -> str:
        calls.append(stage)
        if stage == "agent_a":
            return "A: 기술 수렴·POC 지지·감성 골든크로스."
        if stage == "agent_b":
            return "B: DCF MoS 17% — A와 정합, 저평가."
        return "선생님, NVDA는 기술·가치·심리 삼중 저평가 신호입니다."

    set_ollama_stage_fn(mock_stage)
    try:
        ctx = _sample_ctx()
        from local_intelligence.engine import build_compact_context
        compact = build_compact_context(ctx)
        narrative, used, trace = run_multi_agent_cot(ctx, compact)
        assert used is True
        assert narrative.startswith("선생님,")
        assert calls == ["agent_a", "agent_b", "orchestrator"]
        assert "agent_a" in trace.get("stages", {})
        assert trace.get("gemini_called") is False
    finally:
        set_ollama_stage_fn(None)
    print("✓ 3-stage CoT pipeline mock")


def test_ollama_failure_fallback_rule_based():
    from local_intelligence.engine import (
        refresh_local_analysis_report,
        build_rule_based_narrative,
    )
    from local_intelligence.multi_agent import set_ollama_stage_fn
    from local_intelligence.report_cache import set_report_path_for_tests, read_store, clear_reports

    set_ollama_stage_fn(lambda s, p: "")
    ctx = _sample_ctx()
    try:
        with patch("local_intelligence.engine.gather_fusion_context", return_value=ctx):
            with tempfile.TemporaryDirectory() as td:
                rp = Path(td) / "local_analysis_report.json"
                set_report_path_for_tests(rp)
                try:
                    rep = refresh_local_analysis_report("NVDA", force_ollama=True)
                    assert rep.ollama_used is False
                    assert rep.narrative
                    assert rep.narrative == build_rule_based_narrative(ctx)
                    assert rep.gemini_called is False
                    store = read_store()
                    assert store["reports"]["NVDA"]["narrative"]
                finally:
                    clear_reports()
                    set_report_path_for_tests(None)
    finally:
        set_ollama_stage_fn(None)
    print("✓ Ollama offline → rule-based fallback")


def test_cache_multi_agent_narrative_gemini_zero():
    from hybrid_router import try_local_tool_execution
    from local_intelligence.report_cache import set_report_path_for_tests, read_store, clear_reports
    from local_intelligence.multi_agent import set_ollama_stage_fn

    mock_ctx = _sample_ctx()
    mock_ctx.update({"query": "NVDA", "sources_ok": ["Yahoo"], "tech_signals": {},
                     "timeline_text": "", "anomaly_flag": False})

    set_ollama_stage_fn(lambda s, p: {
        "agent_a": "A기술",
        "agent_b": "B가치",
        "orchestrator": "선생님, 멀티 에이전트 합의 브리핑입니다.",
    }.get(s, ""))

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
            assert "멀티 에이전트" in result.get("briefing", "")
            report = result.get("report") or {}
            assert report.get("ollama_used") is True
            assert report.get("multi_agent_trace")
            store = read_store()
            assert "멀티 에이전트" in store["reports"]["NVDA"]["narrative"]
        finally:
            clear_reports()
            set_report_path_for_tests(None)
            set_ollama_stage_fn(None)
    print("✓ cache narrative + multi_agent_trace, gemini 0")


def test_technical_value_context_isolation():
    from local_intelligence.multi_agent import build_technical_context, build_value_context

    ctx = _sample_ctx()
    tech = build_technical_context(ctx)
    val = build_value_context(ctx)
    assert "POC=" in tech
    assert "DCF적정" not in tech
    assert "DCF적정" in val
    assert "POC=" not in val
    print("✓ persona context isolation")


def run_all():
    tests = [
        test_agent_a_prompt_structure,
        test_agent_b_prompt_cross_validation,
        test_orchestrator_prompt_synthesis,
        test_three_stage_pipeline_mock,
        test_ollama_failure_fallback_rule_based,
        test_cache_multi_agent_narrative_gemini_zero,
        test_technical_value_context_isolation,
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
