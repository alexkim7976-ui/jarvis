"""
주식_입력받고_확인_py — 실시간 시세 + 14개 기술 지표 로컬 검증
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Gemini API 없이 data_providers(KIS/Kiwoom/FMP/Yahoo) 실시간 시세와
stock_quant 로컬 연산으로 RSI·MACD·볼린저 등 14개 지표를 확인합니다.

CLI:  python 주식_입력받고_확인_py.py
코드:  from 주식_입력받고_확인_py import verify_stock
"""
from __future__ import annotations

import sys
from typing import Any

from stock_quant import (
    _resolve_ticker,
    compute_hybrid_score,
    compute_technical_snapshot,
    fetch_stock_data,
    calc_rsi,
    calc_macd,
    calc_bollinger,
    analyze_stock_full_package,
)

# 지표 표시: (표시명, 값키, 시그널키, 포맷)
INDICATOR_ROWS: list[tuple[str, str, str, str]] = [
    ("RSI", "rsi", "RSI", "num"),
    ("MACD", "macd", "MACD", "macd"),
    ("Stochastic %K", "stoch_k", "Stochastic", "num"),
    ("Bollinger 상단", "bb_upper", "Bollinger", "price"),
    ("Bollinger 하단", "bb_lower", "Bollinger", "price"),
    ("일목 전환선", "tenkan", "Ichimoku", "price"),
    ("일목 기준선", "kijun", "Ichimoku", "price"),
    ("ADX", "adx", "ADX", "num"),
    ("Parabolic SAR", "sar", "SAR", "price"),
    ("Momentum(10)", "momentum", "Momentum", "num"),
    ("Williams %R", "williams_r", "Williams%R", "num"),
    ("CCI", "cci", "CCI", "num"),
    ("OBV", "obv", "OBV", "int"),
    ("VWMA", "vwma", "VWMA", "price"),
    ("SMA20", "sma_20", "MA", "price"),
    ("SMA50", "sma_50", "MA", "price"),
    ("ATR", "atr", "ATR", "price"),
]

SIGNAL_KR = {"BUY": "매수", "SELL": "매도", "NEUTRAL": "중립"}


def _fmt_value(key: str, fmt: str, vals: dict, currency: str) -> str:
    v = vals.get(key, 0)
    sym = "₩" if currency in ("KRW", "krw", "") else "$"
    if fmt == "macd":
        return f"{vals.get('macd', 0):.2f} / sig {vals.get('macd_signal', 0):.2f}"
    if fmt == "int":
        return f"{int(v):,}"
    if fmt == "price":
        return f"{sym}{float(v):,.0f}" if sym == "₩" else f"{sym}{float(v):,.2f}"
    return f"{float(v):.2f}"


def verify_stock(query: str) -> dict[str, Any]:
    """
    종목명/티커 입력 → 실시간 시세 + 14개 지표 로컬 계산 (Gemini 미사용).
    """
    q = (query or "").strip()
    if not q:
        return {"success": False, "message": "종목명 또는 티커를 입력해 주세요."}

    resolved = _resolve_ticker(q)
    df, fundamentals, err = fetch_stock_data(resolved)
    if err:
        return {"success": False, "message": err, "ticker": resolved}

    tech = compute_technical_snapshot(df)
    scoring = compute_hybrid_score(fundamentals, tech)
    vals = tech["values"]
    signals = tech["signals"]
    currency = fundamentals.get("currency") or "KRW"
    name = fundamentals.get("name") or resolved
    price = fundamentals.get("price") or vals.get("price", 0)
    src = fundamentals.get("data_source", "YAHOO")
    chg = fundamentals.get("change_pct")

    rows = []
    for label, val_key, sig_key, fmt in INDICATOR_ROWS:
        sig_tuple = signals.get(sig_key)
        sig_label, action = sig_tuple if sig_tuple else ("—", "NEUTRAL")
        rows.append({
            "indicator": label,
            "value": _fmt_value(val_key, fmt, vals, currency),
            "signal": sig_label,
            "action": action,
            "action_kr": SIGNAL_KR.get(action, action),
        })

    buy_n = sum(1 for r in rows if r["action"] == "BUY")
    sell_n = sum(1 for r in rows if r["action"] == "SELL")
    neu_n = len(rows) - buy_n - sell_n

    csym = "₩" if currency in ("KRW", "krw") else "$"
    chg_txt = f" ({'+' if (chg or 0) >= 0 else ''}{chg:.2f}%)" if chg is not None else ""
    briefing = (
        f"{name} [{src}] 현재가 {csym}{float(price):,.0f}{chg_txt}. "
        f"14개 지표 — 매수 {buy_n}·매도 {sell_n}·중립 {neu_n}. "
        f"투자점수 {scoring['score']}/100 ({scoring['grade']}). "
        f"Gemini 미사용, 로컬 연산입니다."
    )

    overlay_table = {
        "type": "table",
        "title": f"{name} | 지표 검증 [{src}]",
        "headers": ["지표", "값", "시그널", "판단"],
        "rows": [[r["indicator"], r["value"], r["signal"], r["action_kr"]] for r in rows],
    }

    return {
        "success": True,
        "ticker": resolved,
        "name": name,
        "price": price,
        "change_pct": chg,
        "data_source": src,
        "currency": currency,
        "indicators": rows,
        "signals_summary": {"buy": buy_n, "sell": sell_n, "neutral": neu_n},
        "scoring": scoring,
        "fundamentals": {
            "per": fundamentals.get("per"),
            "pbr": fundamentals.get("pbr"),
            "roe": fundamentals.get("roe"),
        },
        "briefing": briefing,
        "overlay_table": overlay_table,
        "gemini_used": False,
    }


def format_verification_report(result: dict) -> str:
    """터미널용 표 형식 출력 문자열."""
    if not result.get("success"):
        return f"❌ {result.get('message', '오류')}"

    lines = [
        "",
        "═" * 62,
        f"  {result['name']} ({result['ticker']}) — 실시간 지표 검증",
        f"  데이터: {result.get('data_source', '?')} | Gemini: 사용 안 함",
        "═" * 62,
    ]
    csym = "₩" if result.get("currency") in ("KRW", "krw") else "$"
    chg = result.get("change_pct")
    chg_s = f"  ({'+' if (chg or 0) >= 0 else ''}{chg:.2f}%)" if chg is not None else ""
    lines.append(f"  현재가: {csym}{float(result['price']):,.0f}{chg_s}")
    sc = result.get("scoring", {})
    lines.append(f"  투자점수: {sc.get('score', '?')}/100 — {sc.get('grade', '?')}")
    sm = result.get("signals_summary", {})
    lines.append(f"  시그널: 매수 {sm.get('buy', 0)} | 매도 {sm.get('sell', 0)} | 중립 {sm.get('neutral', 0)}")
    lines.append("─" * 62)
    lines.append(f"  {'지표':<18} {'값':>14}  {'시그널':<12} {'판단'}")
    lines.append("─" * 62)
    for r in result.get("indicators", []):
        lines.append(
            f"  {r['indicator']:<18} {r['value']:>14}  {r['signal']:<12} {r['action_kr']}"
        )
    lines.append("═" * 62)
    return "\n".join(lines)


def input_and_verify() -> dict | None:
    """대화형: 종목 입력 → 검증 결과 반환."""
    try:
        q = input("\n종목명 또는 티커 (종료: q): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n종료합니다.")
        return None
    if not q or q.lower() in ("q", "quit", "exit", "종료"):
        return None
    result = verify_stock(q)
    print(format_verification_report(result))
    if result.get("success"):
        print(f"\n{result['briefing']}")
    return result


def main() -> None:
    print("주식 지표 검증 — 실시간 시세 + 14개 기술 지표 (Gemini 미사용)")
    print("data_providers(KIS/Kiwoom/FMP/Yahoo) → stock_quant 로컬 연산")
    while True:
        r = input_and_verify()
        if r is None:
            break


if __name__ == "__main__":
    main()

__all__ = [
    "verify_stock",
    "format_verification_report",
    "input_and_verify",
    "compute_technical_snapshot",
    "compute_hybrid_score",
    "fetch_stock_data",
    "analyze_stock_full_package",
    "calc_rsi",
    "calc_macd",
    "calc_bollinger",
    "INDICATOR_ROWS",
]
