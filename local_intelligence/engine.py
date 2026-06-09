"""
Local Intelligence Engine — CV 합의 + 14지표 + Ollama 정성 평가

Gemini 0회. Ollama 불가 시 rule-based narrative 폴백(크래시 없음).
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .report_cache import get_report, upsert_report

_ollama_fn: Callable[[str], str] | None = None
_gather_fn: Callable[[str], dict] | None = None


def set_ollama_fn(fn: Callable[[str], str] | None) -> None:
    global _ollama_fn
    _ollama_fn = fn


def set_gather_fn(fn: Callable[[str], dict] | None) -> None:
    global _gather_fn
    _gather_fn = fn


@dataclass
class LocalAnalysisReport:
    ticker: str
    query: str
    name: str | None
    consensus_price: float | None
    change_pct: float | None
    rsi: float | None
    hybrid_score: int | None
    bb_squeeze: bool
    sentiment_score: int
    narrative: str
    compact_context: str
    sources_ok: list[str] = field(default_factory=list)
    ollama_used: bool = False
    gemini_called: bool = False
    volume_profile: dict = field(default_factory=dict)
    ma_ribbon: dict = field(default_factory=dict)
    valuation: dict = field(default_factory=dict)
    local_matrix: dict = field(default_factory=dict)
    dcf_valuation: dict = field(default_factory=dict)
    graham_valuation: dict = field(default_factory=dict)
    sentiment_golden_cross: bool = False
    sentiment_divergence_signal: str = ""
    sentiment_ma5: float | None = None
    sentiment_ma20: float | None = None
    metrics_snapshot: dict = field(default_factory=dict)
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "query": self.query,
            "name": self.name,
            "consensus_price": self.consensus_price,
            "change_pct": self.change_pct,
            "rsi": self.rsi,
            "hybrid_score": self.hybrid_score,
            "bb_squeeze": self.bb_squeeze,
            "sentiment_score": self.sentiment_score,
            "narrative": self.narrative,
            "compact_context": self.compact_context,
            "sources_ok": self.sources_ok,
            "ollama_used": self.ollama_used,
            "gemini_called": False,
            "volume_profile": self.volume_profile,
            "ma_ribbon": self.ma_ribbon,
            "valuation": self.valuation,
            "local_matrix": self.local_matrix,
            "dcf_valuation": self.dcf_valuation,
            "graham_valuation": self.graham_valuation,
            "sentiment_golden_cross": self.sentiment_golden_cross,
            "sentiment_divergence_signal": self.sentiment_divergence_signal,
            "sentiment_ma5": self.sentiment_ma5,
            "sentiment_ma20": self.sentiment_ma20,
            "local_matrix_score": self.local_matrix.get("local_matrix_score"),
            "local_matrix_grade": self.local_matrix.get("grade"),
            "metrics_snapshot": self.metrics_snapshot,
            "timestamp": int(self.timestamp or time.time()),
        }


def _resolve_ticker(query: str) -> tuple[str, str | None]:
    try:
        from cross_validation import _resolve_yahoo_ticker
        from stock_quant import resolve_alert_query
        r = resolve_alert_query(query)
        ticker = r["ticker"] if r.get("confident") else _resolve_yahoo_ticker(query)
        return str(ticker).upper(), r.get("name")
    except Exception:
        return (query or "").strip().upper(), None


def _detect_bb_squeeze(values: dict) -> bool:
    bb_upper = float(values.get("bb_upper") or 0)
    bb_lower = float(values.get("bb_lower") or 0)
    price = float(values.get("price") or 0)
    if bb_upper <= 0 or bb_lower <= 0 or price <= 0:
        return False
    width_pct = (bb_upper - bb_lower) / price * 100
    return width_pct < 4.0


def gather_fusion_context(query: str, *, timeout: float = 12.0) -> dict[str, Any]:
    """
    cross_validation 합의 + local_rule_engine 14지표 + CV 캐시 digest 융합.
    네트워크 호출 포함 — 테스트에서 set_gather_fn 으로 mock.
    """
    if _gather_fn:
        return _gather_fn(query)

    ticker, name_hint = _resolve_ticker(query)

    cv_snap = None
    try:
        from cv_cache_manager import get_snapshot
        cv_snap = get_snapshot(ticker)
    except ImportError:
        pass

    report = None
    market_snap = None
    try:
        from cross_validation import collect_consensus_local
        report, market_snap = collect_consensus_local(query, timeout=timeout, include_news=True)
    except Exception:
        report = None
        market_snap = None

    lre_snap = None
    try:
        from local_rule_engine import analyze_ticker_local
        cp = market_snap.consensus_change_pct if market_snap else 0.0
        lre_snap = analyze_ticker_local(
            ticker, name=name_hint or ticker,
            change_pct=float(cp or 0),
        )
    except Exception:
        lre_snap = None

    tech_values = (lre_snap.tech_values if lre_snap else {}) or {}
    rsi = float(tech_values.get("rsi") or (lre_snap.rsi if lre_snap else 0) or 0)
    hybrid = int(lre_snap.hybrid_score if lre_snap else 0)
    volume_profile = (lre_snap.volume_profile if lre_snap else {}) or {}
    ma_ribbon = (lre_snap.ma_ribbon if lre_snap else {}) or {}
    valuation = (lre_snap.valuation if lre_snap else {}) or {}
    local_matrix = (lre_snap.local_matrix if lre_snap else {}) or {}
    dcf_valuation = (lre_snap.dcf_valuation if lre_snap else {}) or {}
    graham_valuation = (lre_snap.graham_valuation if lre_snap else {}) or {}

    price_closes = None
    try:
        if lre_snap and lre_snap.tech_values:
            from stock_quant import fetch_stock_data
            df, _, err = fetch_stock_data(ticker, period="2mo")
            if df is not None and not df.empty and not err:
                price_closes = df["Close"]
    except Exception:
        price_closes = None

    sentiment_momentum: dict = {}
    try:
        from local_rule_engine.sentiment_momentum import analyze_sentiment_momentum
        if market_snap and market_snap.sentiment_score is not None:
            from cross_validation import record_sentiment_observation
            record_sentiment_observation(ticker, int(market_snap.sentiment_score))
        sentiment_momentum = analyze_sentiment_momentum(ticker, price_closes=price_closes)
    except Exception:
        sentiment_momentum = {}

    return {
        "ticker": ticker,
        "query": query,
        "name": (lre_snap.name if lre_snap else None) or name_hint or ticker,
        "consensus_price": (
            market_snap.consensus_price if market_snap else
            (cv_snap or {}).get("consensus_price") or
            (lre_snap.price if lre_snap else None)
        ),
        "change_pct": (
            market_snap.consensus_change_pct if market_snap else
            (cv_snap or {}).get("consensus_change_pct") or
            (lre_snap.change_pct if lre_snap else None)
        ),
        "compact_digest": (
            (market_snap.compact_digest if market_snap else "") or
            (cv_snap or {}).get("compact_digest") or
            (lre_snap.compact_summary if lre_snap else "")
        ),
        "sentiment_score": (
            market_snap.sentiment_score if market_snap else
            (cv_snap or {}).get("sentiment_score") or 0
        ),
        "sources_ok": (
            list(market_snap.sources_ok) if market_snap else
            (cv_snap or {}).get("sources_ok") or []
        ),
        "rsi": rsi,
        "hybrid_score": hybrid,
        "bb_squeeze": _detect_bb_squeeze(tech_values),
        "tech_signals": (lre_snap.tech_signals if lre_snap else {}) or {},
        "timeline_text": (market_snap.timeline_text if market_snap else "") or "",
        "volume_profile": volume_profile,
        "ma_ribbon": ma_ribbon,
        "valuation": valuation,
        "local_matrix": local_matrix,
        "dcf_valuation": dcf_valuation,
        "graham_valuation": graham_valuation,
        "sentiment_ma5": sentiment_momentum.get("sentiment_ma5") or (
            market_snap.sentiment_ma5 if market_snap else None
        ),
        "sentiment_ma20": sentiment_momentum.get("sentiment_ma20") or (
            market_snap.sentiment_ma20 if market_snap else None
        ),
        "sentiment_golden_cross": bool(sentiment_momentum.get("sentiment_golden_cross")),
        "sentiment_divergence_signal": str(
            sentiment_momentum.get("sentiment_divergence_signal") or ""
        ),
        "sentiment_momentum_signal": str(
            sentiment_momentum.get("sentiment_momentum_signal") or ""
        ),
        "local_matrix_score": local_matrix.get("local_matrix_score"),
        "local_matrix_grade": local_matrix.get("grade"),
        "anomaly_flag": bool(
            (market_snap.anomaly_flag if market_snap else False) or
            (lre_snap.anomaly.triggered if lre_snap else False)
        ),
    }


def build_compact_context(ctx: dict[str, Any]) -> str:
    """Ollama 전용 — raw OHLCV 배제, 확장 지표 수치 스냅샷."""
    vp = ctx.get("volume_profile") or {}
    rb = ctx.get("ma_ribbon") or {}
    val = ctx.get("valuation") or {}
    mx = ctx.get("local_matrix") or {}
    dcf = ctx.get("dcf_valuation") or {}
    gr = ctx.get("graham_valuation") or {}
    sm5 = ctx.get("sentiment_ma5")
    sm20 = ctx.get("sentiment_ma20")

    lines = [
        f"종목: {ctx.get('name')}({ctx.get('ticker')})",
        f"합의가: {ctx.get('consensus_price')} | 변동: {ctx.get('change_pct'):+.1f}%"
        if ctx.get("change_pct") is not None else f"합의가: {ctx.get('consensus_price')}",
        f"RSI: {ctx.get('rsi', 0):.0f} | hybrid: {ctx.get('hybrid_score')}/100",
        f"BB스퀴즈: {'Y' if ctx.get('bb_squeeze') else 'N'} | 뉴스감성: {ctx.get('sentiment_score', 0):+d}",
    ]
    if sm5 is not None and sm20 is not None:
        lines.append(
            f"감성MA5={sm5:+.1f} MA20={sm20:+.1f} "
            f"골든크로스={'Y' if ctx.get('sentiment_golden_cross') else 'N'}"
        )
    div = (ctx.get("sentiment_divergence_signal") or "").strip()
    if div:
        lines.append(f"감성다이버전스: {div[:80]}")
    if vp.get("poc"):
        lines.append(
            f"매물대 POC={vp.get('poc')} 지지={vp.get('support')} 저항={vp.get('resistance')} | {vp.get('signal', '')[:50]}"
        )
    if rb.get("spread_pct") is not None:
        lines.append(
            f"이평선 밀집도 spread={rb.get('spread_pct')}% state={rb.get('state')} | {rb.get('signal', '')[:50]}"
        )
    if val.get("score") is not None:
        lines.append(
            f"밸류에이션 점수={val.get('score')}/100 ({val.get('grade')}) "
            f"PER={val.get('per')} PBR={val.get('pbr')} ROE={val.get('roe')}%"
        )
    if mx.get("local_matrix_score") is not None:
        layers = mx.get("layers") or {}
        lines.append(
            f"로컬종합매트릭스={mx.get('local_matrix_score')}/100 ({mx.get('grade')}) "
            f"[기술{layers.get('technical', {}).get('score', '?')}+"
            f"재무{layers.get('fundamental', {}).get('score', '?')}+"
            f"고래{layers.get('whale', {}).get('score', '?')}+"
            f"소셜{layers.get('social', {}).get('score', '?')}]"
        )
    if dcf.get("fair_price") is not None:
        lines.append(
            f"DCF적정주가=${dcf.get('fair_price')} "
            f"WACC={dcf.get('wacc_pct')}% "
            f"안전마진={dcf.get('margin_of_safety_pct')}% "
            f"({dcf.get('signal', '')[:40]})"
        )
    if gr.get("intrinsic_value") is not None:
        lines.append(
            f"그레이엄내재가치=${gr.get('intrinsic_value')} "
            f"EPS={gr.get('eps')} g={gr.get('growth_pct')}% Y={gr.get('treasury_yield_pct')}% "
            f"저평가율={gr.get('undervaluation_pct')}%"
        )
    digest = (ctx.get("compact_digest") or "").strip()
    if digest:
        lines.append(f"CV합의: {digest[:180]}")
    return "\n".join(lines)


def build_rule_based_narrative(ctx: dict[str, Any]) -> str:
    """Ollama 불가 시 deterministic 전문 브리핑 — Gemini 0."""
    nm = ctx.get("name") or ctx.get("ticker")
    vp = ctx.get("volume_profile") or {}
    rb = ctx.get("ma_ribbon") or {}
    val = ctx.get("valuation") or {}
    mx = ctx.get("local_matrix") or {}
    dcf = ctx.get("dcf_valuation") or {}
    gr = ctx.get("graham_valuation") or {}

    trend = rb.get("signal") or "이평선 중립"
    vp_txt = vp.get("signal") or "매물대 데이터 제한"
    val_score = val.get("score", 50)
    val_grade = val.get("grade", "중립")
    matrix = mx.get("local_matrix_score", ctx.get("hybrid_score", 50))

    dcf_txt = ""
    if dcf.get("fair_price") is not None:
        mos = dcf.get("margin_of_safety_pct")
        dcf_txt = (
            f" DCF 기준 적정 주가는 ${dcf['fair_price']:.2f}이며, "
            f"현재가 대비 안전마진은 {mos:+.1f}%입니다."
            if mos is not None else
            f" DCF 기준 적정 주가는 ${dcf['fair_price']:.2f}입니다."
        )

    gr_txt = ""
    if gr.get("intrinsic_value") is not None:
        uv = gr.get("undervaluation_pct")
        gr_txt = (
            f" 그레이엄 내재가치 ${gr['intrinsic_value']:.2f}, 저평가율 {uv:+.1f}%입니다."
            if uv is not None else
            f" 그레이엄 내재가치 ${gr['intrinsic_value']:.2f}입니다."
        )

    sent_txt = ""
    if ctx.get("sentiment_golden_cross") and ctx.get("sentiment_divergence_signal"):
        sent_txt = (
            " 현재 주가는 바닥을 기고 있으나 뉴스 심리 모멘텀은 먼저 골든크로스를 그리며 "
            f"{ctx['sentiment_divergence_signal']}를 보이고 있습니다."
        )
    elif ctx.get("sentiment_golden_cross"):
        sent_txt = " 뉴스 감성 MA5가 MA20을 상방 돌파하는 골든크로스가 확인됩니다."
    elif ctx.get("sentiment_divergence_signal"):
        sent_txt = f" {ctx['sentiment_divergence_signal']}."

    price = ctx.get("consensus_price")
    cp = ctx.get("change_pct")
    price_txt = f"합의가 {price}" if price else "가격 제한"
    ch_txt = f", 전일대비 {cp:+.1f}%" if cp is not None else ""

    return (
        f"선생님, {nm} 로컬 정밀 분석입니다. {price_txt}{ch_txt}. "
        f"{trend}. {vp_txt}. "
        f"재무 안정성(밸류에이션) 점수는 {val_score}점({val_grade})이며, "
        f"로컬 종합 매트릭스 점수는 {matrix}/100({mx.get('grade', 'HOLD')})입니다."
        f"{dcf_txt}{gr_txt}{sent_txt}"
    )


_OLLAMA_NARRATIVE_PROMPT = """당신은 Apple Silicon 로컬 퀀트 애널리스트입니다.
아래 수치 스냅샷만 사용해 한국어 3~4문장 전문 브리핑을 작성하세요.

반드시 포함:
1) 이평선 밀집/수렴·돌파 상태
2) 매물대(POC) 지지·저항
3) 재무 밸류에이션 점수
4) 로컬 종합 매트릭스 점수
5) DCF 적정 주가 및 안전마진(%)
6) 그레이엄 내재가치 및 저평가율(%)
7) 뉴스 감성 MA5/MA20, 골든크로스, 강세 다이버전스

규칙:
- '선생님,'으로 시작
- 컨텍스트에 없는 숫자 invent 금지
- Gemini/클라우드 API 언급 금지

[로컬 지표 스냅샷]
{context}
"""


def run_ollama_qualitative_analysis(compact_context: str) -> tuple[str, bool]:
    """
    Ollama 정성 평가 — compact_context 만 전달.
    Returns: (narrative, ollama_used)
    """
    if not compact_context:
        return "", False

    if _ollama_fn:
        try:
            return _ollama_fn(compact_context), True
        except Exception:
            return "", False

    url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct-q4_K_M")
    prompt = _OLLAMA_NARRATIVE_PROMPT.format(context=compact_context)
    try:
        import requests
        r = requests.post(
            f"{url}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"num_ctx": int(os.getenv("OLLAMA_NUM_CTX", "4096"))},
            },
            timeout=float(os.getenv("LOCAL_INTEL_OLLAMA_TIMEOUT", "25")),
        )
        r.raise_for_status()
        msg = r.json().get("message", {}).get("content", "").strip()
        if msg:
            return msg[:800], True
    except Exception:
        pass
    return "", False


def refresh_local_analysis_report(
    query: str,
    *,
    timeout: float = 12.0,
    force_ollama: bool = True,
) -> LocalAnalysisReport:
    """백그라운드/온디맨드 — local_analysis_report.json 갱신, Gemini 0."""
    ctx = gather_fusion_context(query, timeout=timeout)
    compact = build_compact_context(ctx)

    narrative = ""
    ollama_used = False
    if force_ollama and os.getenv("LOCAL_INTEL_OLLAMA_ENABLED", "1").strip().lower() not in (
        "0", "false", "no", "off",
    ):
        narrative, ollama_used = run_ollama_qualitative_analysis(compact)

    if not narrative:
        narrative = build_rule_based_narrative(ctx)

    metrics_snapshot = {
        "consensus_price": ctx.get("consensus_price"),
        "change_pct": ctx.get("change_pct"),
        "rsi": ctx.get("rsi"),
        "hybrid_score": ctx.get("hybrid_score"),
        "volume_profile": ctx.get("volume_profile"),
        "ma_ribbon": ctx.get("ma_ribbon"),
        "valuation": ctx.get("valuation"),
        "dcf_valuation": ctx.get("dcf_valuation"),
        "graham_valuation": ctx.get("graham_valuation"),
        "local_matrix_score": ctx.get("local_matrix_score"),
        "local_matrix_grade": ctx.get("local_matrix_grade"),
        "sentiment_score": ctx.get("sentiment_score"),
        "sentiment_ma5": ctx.get("sentiment_ma5"),
        "sentiment_ma20": ctx.get("sentiment_ma20"),
        "sentiment_golden_cross": ctx.get("sentiment_golden_cross"),
        "sentiment_divergence_signal": ctx.get("sentiment_divergence_signal"),
    }

    rep = LocalAnalysisReport(
        ticker=str(ctx.get("ticker") or ""),
        query=query,
        name=ctx.get("name"),
        consensus_price=ctx.get("consensus_price"),
        change_pct=ctx.get("change_pct"),
        rsi=ctx.get("rsi"),
        hybrid_score=ctx.get("hybrid_score"),
        bb_squeeze=bool(ctx.get("bb_squeeze")),
        sentiment_score=int(ctx.get("sentiment_score") or 0),
        narrative=narrative,
        compact_context=compact,
        sources_ok=list(ctx.get("sources_ok") or []),
        ollama_used=ollama_used,
        gemini_called=False,
        volume_profile=dict(ctx.get("volume_profile") or {}),
        ma_ribbon=dict(ctx.get("ma_ribbon") or {}),
        valuation=dict(ctx.get("valuation") or {}),
        local_matrix=dict(ctx.get("local_matrix") or {}),
        dcf_valuation=dict(ctx.get("dcf_valuation") or {}),
        graham_valuation=dict(ctx.get("graham_valuation") or {}),
        sentiment_golden_cross=bool(ctx.get("sentiment_golden_cross")),
        sentiment_divergence_signal=str(ctx.get("sentiment_divergence_signal") or ""),
        sentiment_ma5=ctx.get("sentiment_ma5"),
        sentiment_ma20=ctx.get("sentiment_ma20"),
        metrics_snapshot=metrics_snapshot,
        timestamp=time.time(),
    )
    upsert_report(rep.to_dict())
    return rep


def get_local_report(ticker_or_query: str) -> dict[str, Any] | None:
    return get_report(ticker_or_query)


def format_tool_response(report: LocalAnalysisReport | dict) -> dict:
    """execute_any_tool / crossval 응답 형식."""
    d = report.to_dict() if hasattr(report, "to_dict") else dict(report)
    return {
        "success": bool(d.get("consensus_price") or d.get("narrative")),
        "briefing": d.get("narrative", ""),
        "report": d,
        "local_intelligence": True,
        "ollama_used": d.get("ollama_used", False),
        "gemini_called": False,
        "compact_context": d.get("compact_context", ""),
    }
