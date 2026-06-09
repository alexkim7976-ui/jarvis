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
        price = market_snap.consensus_price if market_snap else 0.0
        lre_snap = analyze_ticker_local(
            ticker, name=name_hint or ticker,
            change_pct=float(cp or 0), price=float(price or 0),
        )
    except Exception:
        lre_snap = None

    tech_values = (lre_snap.tech_values if lre_snap else {}) or {}
    rsi = float(tech_values.get("rsi") or (lre_snap.rsi if lre_snap else 0) or 0)
    hybrid = int(lre_snap.hybrid_score if lre_snap else 0)

    return {
        "ticker": ticker,
        "query": query,
        "name": (lre_snap.name if lre_snap else None) or name_hint or ticker,
        "consensus_price": (
            market_snap.consensus_price if market_snap else
            (cv_snap or {}).get("consensus_price")
        ),
        "change_pct": (
            market_snap.consensus_change_pct if market_snap else
            (cv_snap or {}).get("consensus_change_pct")
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
        "anomaly_flag": bool(
            (market_snap.anomaly_flag if market_snap else False) or
            (lre_snap.anomaly.triggered if lre_snap else False)
        ),
    }


def build_compact_context(ctx: dict[str, Any]) -> str:
    """Ollama/Gemini 공통 — raw OHLCV 배제 컴팩트 컨텍스트."""
    parts = [
        f"{ctx.get('name')}({ctx.get('ticker')})",
        f"합의가 {ctx.get('consensus_price')}",
        f"변동 {ctx.get('change_pct'):+.1f}%" if ctx.get("change_pct") is not None else "",
        f"RSI {ctx.get('rsi', 0):.0f}",
        f"퀀트점수 {ctx.get('hybrid_score')}/100",
        f"BB스퀴즈 {'Y' if ctx.get('bb_squeeze') else 'N'}",
        f"감성 {ctx.get('sentiment_score', 0):+d}",
    ]
    digest = (ctx.get("compact_digest") or "").strip()
    if digest:
        parts.append(digest[:200])
    return ". ".join(p for p in parts if p)


def build_rule_based_narrative(ctx: dict[str, Any]) -> str:
    """Ollama 불가 시 deterministic 1차 브리핑 — Gemini 0."""
    nm = ctx.get("name") or ctx.get("ticker")
    pos = "중립"
    rsi = float(ctx.get("rsi") or 50)
    if rsi <= 30:
        pos = "과매도 구간 — 반등 관찰"
    elif rsi >= 70:
        pos = "과매수 구간 — 조정 주의"
    elif ctx.get("bb_squeeze"):
        pos = "볼린저 스퀴즈 — 변동성 확대 임박"

    sent = int(ctx.get("sentiment_score") or 0)
    sent_txt = "뉴스 톤 중립"
    if sent >= 4:
        sent_txt = "호재성 뉴스 우세"
    elif sent <= -4:
        sent_txt = "악재성 뉴스 우세"

    price = ctx.get("consensus_price")
    cp = ctx.get("change_pct")
    price_txt = f"합의가 {price}" if price else "가격 데이터 제한"
    ch_txt = f", 전일대비 {cp:+.1f}%" if cp is not None else ""

    return (
        f"선생님, {nm} 로컬 분석입니다. {price_txt}{ch_txt}. "
        f"RSI {rsi:.0f} 기준 {pos}. {sent_txt}. "
        f"퀀트 통합점수 {ctx.get('hybrid_score', 0)}/100."
    )


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
    prompt = (
        "당신은 로컬 주식 분석 어시스턴트입니다. 아래 컨텍스트만 사용해 "
        "한국어 2~3문장으로 포지션·볼린저·퀀트/감성 요약을 작성하세요. "
        "숫자는 컨텍스트에 있는 값만 사용.\n\n"
        f"{compact_context}"
    )
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
