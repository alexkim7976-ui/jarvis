"""
local_rule_engine — 확장 로컬 퀀트 지표 (Volume Profile, MA Ribbon, Valuation)

100% Pandas/Numpy — Gemini 0.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _safe_last(series: pd.Series, default: float = 0.0) -> float:
    try:
        v = float(series.iloc[-1])
        return v if np.isfinite(v) else default
    except Exception:
        return default


def compute_volume_profile(df: pd.DataFrame, *, n_bins: int = 30, lookback: int = 60) -> dict[str, Any]:
    """
    정밀 매물대(Volume Profile) — POC·VAH·VAL·지지/저항.
    """
    empty = {
        "poc": None, "vah": None, "val": None,
        "support": None, "resistance": None,
        "poc_volume_pct": 0.0, "signal": "데이터 없음", "near_poc": False,
    }
    if df is None or df.empty or len(df) < 5:
        return empty

    sub = df.tail(min(lookback, len(df))).copy()
    close = pd.to_numeric(sub["Close"], errors="coerce").dropna()
    vol = pd.to_numeric(sub["Volume"], errors="coerce").fillna(0)
    if close.empty or vol.sum() <= 0:
        return empty

    price = float(close.iloc[-1])
    lo, hi = float(close.min()), float(close.max())
    if hi <= lo:
        return empty

    bins = np.linspace(lo, hi, n_bins + 1)
    hist = np.zeros(n_bins)
    for p, v in zip(close.values, vol.values):
        idx = min(n_bins - 1, int((p - lo) / (hi - lo + 1e-9) * n_bins))
        hist[idx] += v

    total = hist.sum() or 1.0
    poc_idx = int(np.argmax(hist))
    poc = (bins[poc_idx] + bins[poc_idx + 1]) / 2

    # Value Area (70% volume around POC)
    order = np.argsort(hist)[::-1]
    cum = 0.0
    included = set()
    for i in order:
        cum += hist[i]
        included.add(i)
        if cum / total >= 0.70:
            break
    idxs = sorted(included)
    val = (bins[idxs[0]] + bins[idxs[0] + 1]) / 2
    vah = (bins[idxs[-1]] + bins[idxs[-1] + 1]) / 2

    support = val if price > val else poc
    resistance = vah if price < vah else poc
    poc_pct = float(hist[poc_idx] / total * 100)
    near_poc = abs(price - poc) / price * 100 < 1.5 if price > 0 else False

    if price >= vah:
        signal = "POC 상단·VAH 돌파 — 매물대 저항 시험"
    elif price <= val:
        signal = "VAL 하단 — 매물대 지지 구간"
    elif near_poc:
        signal = f"POC {poc:.2f} 부근 밀집 — 방향 선택 임박"
    else:
        signal = f"POC {poc:.2f} · VA {val:.2f}~{vah:.2f} 범위"

    return {
        "poc": round(poc, 4),
        "vah": round(vah, 4),
        "val": round(val, 4),
        "support": round(support, 4),
        "resistance": round(resistance, 4),
        "poc_volume_pct": round(poc_pct, 1),
        "signal": signal,
        "near_poc": near_poc,
    }


def compute_ma_ribbon(df: pd.DataFrame, *, periods: tuple[int, ...] = (5, 20, 60, 120)) -> dict[str, Any]:
    """이동평균선 밀집도(MA Ribbon) — 수렴/발산·추세 폭발 신호."""
    empty = {
        "mas": {}, "spread_pct": None, "state": "unknown",
        "bull_stack": False, "bear_stack": False, "squeeze": False,
        "breakout": False, "signal": "데이터 없음",
    }
    if df is None or df.empty:
        return empty

    close = pd.to_numeric(df["Close"], errors="coerce").dropna()
    if len(close) < max(periods) + 2:
        return empty

    mas: dict[str, float] = {}
    for p in periods:
        mas[f"sma_{p}"] = _safe_last(close.rolling(p, min_periods=p).mean())

    price = float(close.iloc[-1])
    vals = list(mas.values())
    spread = (max(vals) - min(vals)) / price * 100 if price > 0 else 0.0

    bull = vals == sorted(vals, reverse=True)
    bear = vals == sorted(vals)
    squeeze = spread < 2.5

    prev_spread = None
    try:
        prev_vals = [float(close.rolling(p, min_periods=p).mean().iloc[-6]) for p in periods]
        prev_spread = (max(prev_vals) - min(prev_vals)) / price * 100
    except Exception:
        prev_spread = spread

    breakout = squeeze and spread > prev_spread * 1.3 and price > max(vals)

    if breakout and bull:
        state, signal = "breakout_bull", "이평선 수렴 후 상방 돌파 — 추세 폭발 신호"
    elif squeeze:
        state, signal = "squeeze", f"이평선 밀집(스프레드 {spread:.1f}%) — 변동성 확대 임박"
    elif bull:
        state, signal = "bull_stack", "정배열 — 상승 추세 유지"
    elif bear:
        state, signal = "bear_stack", "역배열 — 하락 추세"
    elif spread > 8:
        state, signal = "wide", f"이평선 발산({spread:.1f}%) — 추세 가속"
    else:
        state, signal = "neutral", f"이평선 중립(스프레드 {spread:.1f}%)"

    return {
        "mas": {k: round(v, 4) for k, v in mas.items()},
        "spread_pct": round(spread, 2),
        "state": state,
        "bull_stack": bull,
        "bear_stack": bear,
        "squeeze": squeeze,
        "breakout": breakout,
        "signal": signal,
    }


def compute_valuation_score(fundamentals: dict | None) -> dict[str, Any]:
    """
    PER/PBR/ROE/부채비율 룰 베이스 밸류에이션 점수 (0~100).
    yfinance/FMP fundamentals dict 입력.
    """
    f = fundamentals or {}
    per = f.get("per")
    pbr = f.get("pbr")
    roe = f.get("roe")
    debt = f.get("debt_to_equity")

    score = 50
    notes: list[str] = []

    try:
        per_f = float(per) if per is not None else None
    except (TypeError, ValueError):
        per_f = None
    try:
        pbr_f = float(pbr) if pbr is not None else None
    except (TypeError, ValueError):
        pbr_f = None
    try:
        roe_f = float(roe) if roe is not None else None
    except (TypeError, ValueError):
        roe_f = None
    try:
        debt_f = float(debt) if debt is not None else None
    except (TypeError, ValueError):
        debt_f = None

    if roe_f is not None:
        if roe_f >= 20:
            score += 15; notes.append(f"ROE 우수({roe_f:.1f}%)")
        elif roe_f >= 10:
            score += 8; notes.append(f"ROE 양호({roe_f:.1f}%)")
        elif roe_f >= 0:
            score += 2
        else:
            score -= 12; notes.append(f"ROE 적자({roe_f:.1f}%)")

    if per_f is not None and per_f > 0:
        if per_f < 12:
            score += 12; notes.append(f"PER 저평가({per_f:.1f})")
        elif per_f < 25:
            score += 5; notes.append(f"PER 적정({per_f:.1f})")
        elif per_f > 45:
            score -= 10; notes.append(f"PER 고평가({per_f:.1f})")

    if pbr_f is not None and pbr_f > 0:
        if pbr_f < 1.2:
            score += 8; notes.append(f"PBR 저평가({pbr_f:.2f})")
        elif pbr_f > 4:
            score -= 6; notes.append(f"PBR 고평가({pbr_f:.2f})")

    if debt_f is not None:
        if debt_f < 80:
            score += 5; notes.append("부채비율 안정")
        elif debt_f > 200:
            score -= 10; notes.append(f"부채비율 높음({debt_f:.0f})")

    score = max(0, min(100, score))
    if score >= 70:
        grade = "저평가·재무 우량"
    elif score >= 55:
        grade = "적정 밸류"
    elif score >= 40:
        grade = "중립"
    else:
        grade = "고평가·재무 주의"

    return {
        "score": score,
        "grade": grade,
        "per": per_f,
        "pbr": pbr_f,
        "roe": roe_f,
        "debt_to_equity": debt_f,
        "notes": notes[:4],
    }


def enrich_fundamentals_from_fmp(ticker: str, fundamentals: dict | None) -> dict:
    """FMP API 키 있으면 PER/PBR 보강 — 실패 시 원본 유지."""
    base = dict(fundamentals or {})
    try:
        from data_providers import get_provider
        p = get_provider("fmp")
        if not p.enabled:
            return base
        q = p.quote(ticker)
        if q.get("success"):
            if base.get("per") is None and q.get("pe"):
                base["per"] = q.get("pe")
            if base.get("pbr") is None:
                r = p.ratios_ttm(ticker)
                if r.get("success") and isinstance(r.get("data"), list) and r["data"]:
                    base["pbr"] = r["data"][0].get("priceToBookRatioTTM")
    except Exception:
        pass
    return base
