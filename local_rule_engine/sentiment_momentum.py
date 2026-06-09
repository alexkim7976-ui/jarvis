"""
local_rule_engine — 뉴스 감성 모멘텀 (Golden Cross · Bullish Divergence)

cross_validation 시계열 + 주가 Close → Gemini 0.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def detect_sentiment_golden_cross(ma_frame: pd.DataFrame) -> tuple[bool, str]:
    """
    Sentiment MA5가 MA20을 상방 돌파 — 골든크로스.
    ma_frame: date index, columns score, ma5, ma20
    """
    if ma_frame is None or ma_frame.empty or len(ma_frame) < 2:
        return False, ""

    df = ma_frame.dropna(subset=["ma5", "ma20"])
    if len(df) < 2:
        return False, ""

    prev5, prev20 = float(df["ma5"].iloc[-2]), float(df["ma20"].iloc[-2])
    cur5, cur20 = float(df["ma5"].iloc[-1]), float(df["ma20"].iloc[-1])

    if prev5 <= prev20 and cur5 > cur20:
        return True, (
            f"뉴스 심리 골든크로스 — MA5({cur5:+.1f}) > MA20({cur20:+.1f})"
        )
    return False, ""


def detect_bullish_sentiment_divergence(
    price_closes: pd.Series,
    ma_frame: pd.DataFrame,
    *,
    lookback: int = 20,
    min_rise: float = 1.5,
) -> tuple[str, dict[str, Any]]:
    """
    주가 하락/횡보 + 감성 MA5 바닥 이후 급반등 → 강세 다이버전스.

    조건 (모두 충족):
      1) lookback 구간 주가 순변화 ≤ +2% (하락·횡보)
      2) 현재가가 구간 저점 부근 (저점 대비 +3% 이내)
      3) MA5가 구간 전반(60%) 최저치 대비 min_rise 이상 반등
      4) MA5 최근 5관측 기울기 > 0
    """
    meta: dict[str, Any] = {
        "price_change_pct": None,
        "ma5_rise_from_trough": None,
        "ma5_slope": None,
    }
    if price_closes is None or price_closes.empty or ma_frame is None or ma_frame.empty:
        return "", meta

    prices = pd.to_numeric(price_closes, errors="coerce").dropna()
    mf = ma_frame.dropna(subset=["ma5"]).tail(lookback)
    if len(prices) < 5 or len(mf) < 10:
        return "", meta

    # 가격 lookback (감성과 길이 맞춤)
    p = prices.tail(len(mf))
    if len(p) < 5:
        return "", meta

    p_start, p_end = float(p.iloc[0]), float(p.iloc[-1])
    if p_start <= 0:
        return "", meta
    price_chg = (p_end - p_start) / p_start * 100
    meta["price_change_pct"] = round(price_chg, 2)

    if price_chg > 2.0:
        return "", meta

    p_min = float(p.min())
    if p_end > p_min * 1.03:
        return "", meta

    ma5 = mf["ma5"].astype(float)
    split = max(3, int(len(ma5) * 0.6))
    trough = float(ma5.iloc[:split].min())
    cur_ma5 = float(ma5.iloc[-1])
    rise = cur_ma5 - trough
    meta["ma5_rise_from_trough"] = round(rise, 2)

    if rise < min_rise:
        return "", meta

    tail = ma5.tail(min(5, len(ma5)))
    if len(tail) < 3:
        return "", meta
    x = np.arange(len(tail), dtype=float)
    slope = float(np.polyfit(x, tail.values, 1)[0])
    meta["ma5_slope"] = round(slope, 3)

    if slope <= 0:
        return "", meta

    signal = (
        f"강세 다이버전스 — 주가 {price_chg:+.1f}% 횡보·하락 중 "
        f"뉴스 MA5 바닥({trough:+.1f})→{cur_ma5:+.1f} 반등"
    )
    return signal, meta


def analyze_sentiment_momentum(
    ticker: str,
    *,
    price_closes: pd.Series | None = None,
) -> dict[str, Any]:
    """
    cross_validation 시계열 + (선택) 주가 → MA · 골든크로스 · 다이버전스.
    """
    empty = {
        "sentiment_ma5": None,
        "sentiment_ma20": None,
        "sentiment_golden_cross": False,
        "sentiment_divergence_signal": "",
        "sentiment_momentum_signal": "",
        "observations": 0,
    }
    try:
        from cross_validation import (
            load_sentiment_timeseries,
            build_sentiment_ma_frame,
        )
    except ImportError:
        return empty

    raw = load_sentiment_timeseries(ticker)
    if raw.empty:
        return empty

    ma_frame = build_sentiment_ma_frame(raw)
    ma_vals = ma_frame.dropna(subset=["ma5", "ma20"])
    ma5 = float(ma_frame["ma5"].iloc[-1]) if ma_frame["ma5"].notna().any() else None
    ma20 = float(ma_frame["ma20"].iloc[-1]) if ma_frame["ma20"].notna().any() else None

    golden, golden_txt = detect_sentiment_golden_cross(ma_frame)

    if price_closes is None:
        try:
            from stock_quant import fetch_stock_data
            df, _, err = fetch_stock_data(ticker, period="2mo")
            if df is not None and not df.empty and not err:
                price_closes = df["Close"]
        except Exception:
            price_closes = None

    div_signal, div_meta = detect_bullish_sentiment_divergence(
        price_closes, ma_frame,
    )

    momentum_parts = []
    if golden_txt:
        momentum_parts.append(golden_txt)
    if div_signal:
        momentum_parts.append(div_signal)

    return {
        "sentiment_ma5": round(ma5, 2) if ma5 is not None and ma5 == ma5 else None,
        "sentiment_ma20": round(ma20, 2) if ma20 is not None and ma20 == ma20 else None,
        "sentiment_golden_cross": golden,
        "sentiment_divergence_signal": div_signal,
        "sentiment_momentum_signal": " · ".join(momentum_parts),
        "observations": len(raw),
        "divergence_meta": div_meta,
        "latest_sentiment_score": int(raw["score"].iloc[-1]) if not raw.empty else 0,
    }
