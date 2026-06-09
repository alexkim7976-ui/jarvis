"""
J.A.R.V.I.S — Local Rule-Engine First (비용 최적화 라우팅)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
stock_quant.py(14개 기술 지표)로 100% 로컬 분석 → 임계치 통과 시에만 Gemini.

  주기적 감시 ──▶ analyze_ticker_local() ──▶ detect_anomalies()
                                              │
                         평상시(횡보) ◀────────┤ gemini_calls=0
                         임계치 돌파 ──▶ fetch_gemini_brief(compact) ──▶ alert/event

바인딩: 프로젝트 루트 `stock_quant.py` (= legacy 주식_입력받고_확인_py 역할)
"""
from .config import LocalRuleConfig, get_config
from .engine import (
    LocalSnapshot, analyze_ticker_local, detect_anomalies, build_compact_summary,
)
from .quant_indicators import compute_volume_profile, compute_ma_ribbon, compute_valuation_score
from .matrix_score import compute_local_consensus_matrix
from .valuation_models import (
    calculate_local_dcf_valuation,
    calculate_graham_intrinsic_value,
    run_local_valuation_suite,
    fetch_valuation_inputs,
)
from .sentiment_momentum import analyze_sentiment_momentum
from .router import CostAwareRouter, RouteDecision
from .gemini_gate import fetch_gemini_brief, reset_gemini_call_count, get_gemini_call_count

__all__ = [
    "LocalRuleConfig", "get_config",
    "LocalSnapshot", "analyze_ticker_local", "detect_anomalies", "build_compact_summary",
    "compute_volume_profile", "compute_ma_ribbon", "compute_valuation_score",
    "compute_local_consensus_matrix",
    "calculate_local_dcf_valuation", "calculate_graham_intrinsic_value",
    "run_local_valuation_suite", "fetch_valuation_inputs",
    "analyze_sentiment_momentum",
    "CostAwareRouter", "RouteDecision",
    "fetch_gemini_brief", "reset_gemini_call_count", "get_gemini_call_count",
]
