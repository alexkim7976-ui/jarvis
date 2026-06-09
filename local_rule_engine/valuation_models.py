"""
local_rule_engine — 로컬 DCF · 그레이엄 내재가치 (Gemini 0)

변수 매핑:
  DCF: FCF₀, g, WACC, g_terminal, N → EV → Equity → fair_price, MoS%
  Graham: EPS, g(%), Y(10Y) → V → undervaluation%
"""
from __future__ import annotations

from typing import Any

# ── Deterministic 기본값 (입력 없을 때) ──
DEFAULT_WACC = 0.09          # 9% (8~10% 중간)
DEFAULT_TERMINAL_GROWTH = 0.02
DEFAULT_PROJECTION_YEARS = 5
DEFAULT_FCF_GROWTH = 0.05    # 5% — 재무 데이터 없을 때
GRAHAM_BASE_YIELD = 4.4      # Graham 원식 상수 (%)
DEFAULT_TREASURY_YIELD = 4.4  # ^TNX 수집 실패 시 폴백 (%)


def _f(val, default: float | None = None) -> float | None:
    try:
        if val is None:
            return default
        v = float(val)
        return v if v == v else default  # NaN guard
    except (TypeError, ValueError):
        return default


def fetch_valuation_inputs(ticker: str, fundamentals: dict | None) -> dict[str, Any]:
    """
    yfinance info + FMP cash_flow/balance 로 DCF·Graham 입력 보강.
    네트워크 실패 시 fundamentals 원본 유지 — Gemini 0.
    """
    base = dict(fundamentals or {})
    out: dict[str, Any] = {
        "fcf": _f(base.get("fcf")),
        "total_debt": _f(base.get("total_debt")),
        "cash": _f(base.get("cash")),
        "shares_outstanding": _f(base.get("shares_outstanding")),
        "fcf_growth": _f(base.get("fcf_growth") or base.get("revenue_growth")),
        "eps": _f(base.get("eps")),
        "earnings_growth": _f(base.get("earnings_growth")),
        "price": _f(base.get("price")),
    }

    # yfinance info 보강
    try:
        import yfinance as yf
        info = getattr(yf.Ticker(ticker), "info", {}) or {}
        if out["fcf"] is None:
            out["fcf"] = _f(info.get("freeCashflow"))
        if out["total_debt"] is None:
            out["total_debt"] = _f(info.get("totalDebt"))
        if out["cash"] is None:
            out["cash"] = _f(info.get("totalCash") or info.get("cash"))
        if out["shares_outstanding"] is None:
            out["shares_outstanding"] = _f(info.get("sharesOutstanding"))
        if out["eps"] is None:
            out["eps"] = _f(info.get("trailingEps"))
        if out["price"] is None:
            out["price"] = _f(info.get("currentPrice") or info.get("regularMarketPrice"))
        eg = info.get("earningsGrowth")
        if out["earnings_growth"] is None and eg is not None:
            out["earnings_growth"] = _f(eg) * 100 if abs(_f(eg, 0) or 0) < 2 else _f(eg)
        rg = info.get("revenueGrowth")
        if out["fcf_growth"] is None and rg is not None:
            out["fcf_growth"] = _f(rg) * 100 if abs(_f(rg, 0) or 0) < 2 else _f(rg)
    except Exception:
        pass

    # FMP 보강
    try:
        from data_providers import get_provider
        fmp = get_provider("fmp")
        if not fmp.enabled:
            return out
        cf = fmp.cash_flow(ticker, limit=1)
        if cf.get("success") and isinstance(cf.get("data"), list) and cf["data"]:
            row = cf["data"][0]
            if out["fcf"] is None:
                out["fcf"] = _f(row.get("freeCashFlow"))
        bal = fmp.balance(ticker, limit=1)
        if bal.get("success") and isinstance(bal.get("data"), list) and bal["data"]:
            row = bal["data"][0]
            if out["total_debt"] is None:
                out["total_debt"] = _f(row.get("totalDebt"))
            if out["cash"] is None:
                out["cash"] = _f(row.get("cashAndCashEquivalents"))
        prof = fmp.profile(ticker)
        if prof.get("success") and isinstance(prof.get("data"), list) and prof["data"]:
            if out["shares_outstanding"] is None:
                out["shares_outstanding"] = _f(prof["data"][0].get("sharesOutstanding"))
    except Exception:
        pass

    return out


def get_treasury_yield_pct() -> float:
    """미 10년물 국채금리 (^TNX) — get_macro_market_status 재사용."""
    try:
        from stock_quant import get_macro_market_status
        macro = get_macro_market_status(force_refresh=False)
        tnx = (macro.get("indicators") or {}).get("^TNX", {})
        y = _f(tnx.get("value"))
        if y is not None and y > 0:
            return y
    except Exception:
        pass
    return DEFAULT_TREASURY_YIELD


def calculate_local_dcf_valuation(
    *,
    fcf: float | None,
    total_debt: float | None = None,
    cash: float | None = None,
    shares_outstanding: float | None = None,
    current_price: float | None = None,
    fcf_growth: float | None = None,
    wacc: float | None = None,
    terminal_growth: float | None = None,
    projection_years: int | None = None,
) -> dict[str, Any]:
    """
    2-Stage DCF → 주당 적정가 · 안전마진(%).

    EV = Σ FCF_t/(1+WACC)^t + TV/(1+WACC)^N
    Equity = EV - (Debt - Cash)
    fair_price = Equity / shares
    margin_of_safety_pct = (fair - price) / fair * 100
    """
    empty = {
        "fair_price": None,
        "enterprise_value": None,
        "equity_value": None,
        "margin_of_safety_pct": None,
        "wacc_pct": None,
        "terminal_growth_pct": None,
        "projection_years": projection_years or DEFAULT_PROJECTION_YEARS,
        "fcf_base": fcf,
        "fcf_growth_pct": None,
        "pv_fcf": None,
        "terminal_value": None,
        "pv_terminal": None,
        "signal": "FCF 데이터 부족",
        "undervalued": False,
    }

    fcf0 = _f(fcf)
    shares = _f(shares_outstanding)
    price = _f(current_price)

    if fcf0 is None or fcf0 <= 0 or shares is None or shares <= 0:
        return empty

    w = _f(wacc, DEFAULT_WACC) or DEFAULT_WACC
    if w <= 0:
        w = DEFAULT_WACC
    tg = _f(terminal_growth, DEFAULT_TERMINAL_GROWTH) or DEFAULT_TERMINAL_GROWTH
    if tg >= w:
        tg = min(tg, w - 0.01)
    n = int(projection_years or DEFAULT_PROJECTION_YEARS)
    n = max(5, min(10, n))

    # fcf_growth: 입력이 % 단위(15) 또는 소수(0.15) 모두 허용
    g_raw = _f(fcf_growth, DEFAULT_FCF_GROWTH * 100) or DEFAULT_FCF_GROWTH * 100
    g = g_raw / 100 if abs(g_raw) > 1 else g_raw
    g = max(-0.05, min(0.25, g))

    pv_fcf = 0.0
    fcf_t = fcf0
    for t in range(1, n + 1):
        fcf_t *= (1 + g)
        pv_fcf += fcf_t / ((1 + w) ** t)

    tv = fcf_t * (1 + tg) / (w - tg) if w > tg else 0.0
    pv_tv = tv / ((1 + w) ** n)
    ev = pv_fcf + pv_tv

    debt = _f(total_debt, 0) or 0
    cash_val = _f(cash, 0) or 0
    equity = ev - (debt - cash_val)
    fair = equity / shares

    mos = None
    undervalued = False
    if price is not None and price > 0 and fair > 0:
        mos = (fair - price) / fair * 100
        undervalued = mos > 0

    if mos is not None:
        if mos >= 25:
            signal = f"DCF 적정가 ${fair:.2f} · 안전마진 {mos:.1f}% — 강한 저평가"
        elif mos >= 10:
            signal = f"DCF 적정가 ${fair:.2f} · 안전마진 {mos:.1f}% — 저평가"
        elif mos >= -10:
            signal = f"DCF 적정가 ${fair:.2f} · 안전마진 {mos:.1f}% — 적정"
        else:
            signal = f"DCF 적정가 ${fair:.2f} · 안전마진 {mos:.1f}% — 고평가"
    else:
        signal = f"DCF 적정가 ${fair:.2f} (현재가 비교 불가)"

    return {
        "fair_price": round(fair, 4),
        "enterprise_value": round(ev, 0),
        "equity_value": round(equity, 0),
        "margin_of_safety_pct": round(mos, 2) if mos is not None else None,
        "wacc_pct": round(w * 100, 2),
        "terminal_growth_pct": round(tg * 100, 2),
        "projection_years": n,
        "fcf_base": round(fcf0, 0),
        "fcf_growth_pct": round(g * 100, 2),
        "pv_fcf": round(pv_fcf, 0),
        "terminal_value": round(tv, 0),
        "pv_terminal": round(pv_tv, 0),
        "signal": signal,
        "undervalued": undervalued,
    }


def calculate_graham_intrinsic_value(
    *,
    eps: float | None,
    growth_pct: float | None = None,
    treasury_yield_pct: float | None = None,
    current_price: float | None = None,
) -> dict[str, Any]:
    """
    Graham: V = EPS × (8.5 + 2g) × 4.4 / Y
    g, Y는 % 단위 (g=10 → 10%, Y=4.4 → 4.4%)
    undervaluation_pct = (V - P) / V × 100  (MoS)
    """
    empty = {
        "intrinsic_value": None,
        "undervaluation_pct": None,
        "eps": eps,
        "growth_pct": growth_pct,
        "treasury_yield_pct": treasury_yield_pct,
        "graham_multiplier": None,
        "signal": "EPS 데이터 부족",
        "undervalued": False,
    }

    eps_f = _f(eps)
    if eps_f is None or eps_f <= 0:
        return empty

    g = _f(growth_pct, 5.0) or 5.0
    g = max(-5.0, min(25.0, g))
    y = _f(treasury_yield_pct) or get_treasury_yield_pct()
    if y <= 0:
        y = DEFAULT_TREASURY_YIELD

    multiplier = (8.5 + 2 * g) * GRAHAM_BASE_YIELD / y
    intrinsic = eps_f * multiplier

    price = _f(current_price)
    undervaluation = None
    undervalued = False
    if price is not None and price > 0 and intrinsic > 0:
        undervaluation = (intrinsic - price) / intrinsic * 100
        undervalued = undervaluation > 0

    if undervaluation is not None:
        if undervaluation >= 30:
            signal = f"그레이엄 내재가치 ${intrinsic:.2f} · 저평가율 {undervaluation:.1f}% — 강한 마진"
        elif undervaluation >= 10:
            signal = f"그레이엄 내재가치 ${intrinsic:.2f} · 저평가율 {undervaluation:.1f}% — 저평가"
        elif undervaluation >= -10:
            signal = f"그레이엄 내재가치 ${intrinsic:.2f} · 저평가율 {undervaluation:.1f}% — 적정"
        else:
            signal = f"그레이엄 내재가치 ${intrinsic:.2f} · 저평가율 {undervaluation:.1f}% — 고평가"
    else:
        signal = f"그레이엄 내재가치 ${intrinsic:.2f}"

    return {
        "intrinsic_value": round(intrinsic, 4),
        "undervaluation_pct": round(undervaluation, 2) if undervaluation is not None else None,
        "eps": round(eps_f, 4),
        "growth_pct": round(g, 2),
        "treasury_yield_pct": round(y, 2),
        "graham_multiplier": round(multiplier, 4),
        "signal": signal,
        "undervalued": undervalued,
    }


def run_local_valuation_suite(
    ticker: str,
    fundamentals: dict | None,
    *,
    current_price: float | None = None,
) -> dict[str, Any]:
    """입력 수집 + DCF + Graham 일괄 실행."""
    inputs = fetch_valuation_inputs(ticker, fundamentals)
    price = _f(current_price) or inputs.get("price")

    dcf = calculate_local_dcf_valuation(
        fcf=inputs.get("fcf"),
        total_debt=inputs.get("total_debt"),
        cash=inputs.get("cash"),
        shares_outstanding=inputs.get("shares_outstanding"),
        current_price=price,
        fcf_growth=inputs.get("fcf_growth"),
    )
    graham = calculate_graham_intrinsic_value(
        eps=inputs.get("eps"),
        growth_pct=inputs.get("earnings_growth"),
        current_price=price,
    )
    return {
        "dcf": dcf,
        "graham": graham,
        "inputs": {k: v for k, v in inputs.items() if v is not None},
    }
