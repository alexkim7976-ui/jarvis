"""
주식_입력받고_확인_py — 호환 셸 (실제 구현: stock_quant.py)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
14개 기술 지표(RSI, MACD, 볼린저, 일목, ADX, SAR, 스토캐스틱 등)와
퀀트 점수 연산은 stock_quant 모듈에 통합되어 있습니다.
local_rule_engine / price_alert / stock_monitor 가 이 모듈을 import 합니다.
"""
from stock_quant import (
    compute_technical_snapshot,
    compute_hybrid_score,
    fetch_stock_data,
    analyze_stock_full_package,
    calc_rsi,
    calc_macd,
    calc_bollinger,
)

__all__ = [
    "compute_technical_snapshot",
    "compute_hybrid_score",
    "fetch_stock_data",
    "analyze_stock_full_package",
    "calc_rsi",
    "calc_macd",
    "calc_bollinger",
]
