"""
local_rule_engine — 14개 지표 로컬 연산 (stock_quant 바인딩)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Gemini 호출 없이 Pandas/yfinance 환경에서 스냅샷·이상 징후 판별.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import LocalRuleConfig, get_config


@dataclass
class AnomalyReport:
    triggered: bool = False
    reasons: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.reasons)


@dataclass
class LocalSnapshot:
    ticker: str
    name: str
    price: float
    change_pct: float
    rsi: float
    bb_lower: float
    bb_upper: float
    hybrid_score: int
    tech_signals: dict
    tech_values: dict
    compact_summary: str = ""
    anomaly: AnomalyReport = field(default_factory=AnomalyReport)

    def as_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "name": self.name,
            "price": self.price,
            "change_pct": self.change_pct,
            "rsi": self.rsi,
            "hybrid_score": self.hybrid_score,
            "compact_summary": self.compact_summary,
            "anomaly": {
                "triggered": self.anomaly.triggered,
                "reasons": list(self.anomaly.reasons),
                "signals": list(self.anomaly.signals),
            },
        }


def build_compact_summary(name: str, ticker: str, values: dict,
                          change_pct: float, anomaly: AnomalyReport) -> str:
    """Gemini 프롬프트용 1~2문장 스냅샷 — raw OHLCV 미포함."""
    rsi = values.get("rsi", 0)
    price = values.get("price", 0)
    parts = [f"{name}({ticker}) 현재가 {price:.2f}, 전일대비 {change_pct:+.1f}%, RSI {rsi:.0f}"]
    if anomaly.reasons:
        parts.append("특이점: " + "; ".join(anomaly.reasons[:3]))
    return ". ".join(parts)


def detect_anomalies(
    tech: dict,
    *,
    change_pct: float,
    cfg: LocalRuleConfig | None = None,
) -> AnomalyReport:
    """로컬 임계치 — 명백한 특이점만 True."""
    cfg = cfg or get_config()
    values = tech.get("values") or {}
    signals = tech.get("signals") or {}
    price = values.get("price", 0)
    rsi = values.get("rsi", 50)
    bb_lower = values.get("bb_lower", 0)
    bb_upper = values.get("bb_upper", 0)

    reasons: list[str] = []
    sigs: list[str] = []

    if rsi <= cfg.rsi_oversold:
        reasons.append(f"RSI {rsi:.0f} 과매도(≤{cfg.rsi_oversold:g})")
        sigs.append("rsi_oversold")
    elif rsi >= cfg.rsi_overbought:
        reasons.append(f"RSI {rsi:.0f} 과매수(≥{cfg.rsi_overbought:g})")
        sigs.append("rsi_overbought")

    bb_sig = signals.get("Bollinger", ("", ""))[0]
    if price and bb_lower and price <= bb_lower:
        reasons.append("볼린저 하단 돌파")
        sigs.append("bb_lower_break")
    elif price and bb_upper and price >= bb_upper:
        reasons.append("볼린저 상단 돌파")
        sigs.append("bb_upper_break")
    elif "하단" in bb_sig:
        reasons.append(f"볼린저 {bb_sig}")
        sigs.append("bb_touch_lower")

    if abs(change_pct) >= cfg.change_spike_pct:
        reasons.append(f"변동률 {change_pct:+.1f}% (≥±{cfg.change_spike_pct:g}%)")
        sigs.append("change_spike")

    macd_label = signals.get("MACD", ("", ""))[0]
    if macd_label in ("골든크로스", "데드크로스"):
        reasons.append(f"MACD {macd_label}")
        sigs.append(f"macd_{macd_label}")

    stoch_sig = signals.get("Stochastic", ("", ""))[1]
    if stoch_sig == "BUY" and values.get("stoch_k", 50) < 20:
        reasons.append(f"스토캐스틱 과매도(K={values.get('stoch_k', 0):.0f})")
        sigs.append("stoch_oversold")

    return AnomalyReport(triggered=len(reasons) > 0, reasons=reasons, signals=sigs)


def analyze_ticker_local(
    ticker: str,
    *,
    name: str = "",
    change_pct: float | None = None,
    cfg: LocalRuleConfig | None = None,
) -> LocalSnapshot | None:
    """
    stock_quant.fetch_stock_data + compute_technical_snapshot + compute_hybrid_score.
    100% 로컬 — LLM 미호출.
    """
    cfg = cfg or get_config()
    if not cfg.enabled:
        return None

    try:
        from stock_quant import fetch_stock_data, compute_technical_snapshot, compute_hybrid_score
    except ImportError:
        return None

    df, fundamentals, err = fetch_stock_data(ticker, period=cfg.local_period)
    if df is None or df.empty or err:
        return None

    tech = compute_technical_snapshot(df)
    values = tech.get("values") or {}

    if change_pct is None:
        try:
            if len(df) >= 2:
                prev = float(df["Close"].iloc[-2])
                cur = float(df["Close"].iloc[-1])
                change_pct = (cur - prev) / prev * 100 if prev > 0 else 0.0
            else:
                change_pct = 0.0
        except Exception:
            change_pct = 0.0

    anomaly = detect_anomalies(tech, change_pct=change_pct, cfg=cfg)
    hybrid = compute_hybrid_score(fundamentals or {}, tech)
    display = name or (fundamentals or {}).get("name") or ticker
    compact = build_compact_summary(display, ticker, values, change_pct, anomaly)

    return LocalSnapshot(
        ticker=ticker,
        name=display,
        price=values.get("price", 0),
        change_pct=round(change_pct, 3),
        rsi=values.get("rsi", 0),
        bb_lower=values.get("bb_lower", 0),
        bb_upper=values.get("bb_upper", 0),
        hybrid_score=int(hybrid.get("score", 50)),
        tech_signals=tech.get("signals") or {},
        tech_values=values,
        compact_summary=compact,
        anomaly=anomaly,
    )
