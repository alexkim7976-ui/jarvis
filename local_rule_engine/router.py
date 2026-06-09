"""local_rule_engine — Cost-Aware Routing 오케스트레이터."""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import LocalRuleConfig, get_config
from .engine import LocalSnapshot, analyze_ticker_local
from .gemini_gate import fetch_gemini_brief


@dataclass
class RouteDecision:
    local_snapshot: LocalSnapshot | None = None
    gemini_called: bool = False
    gemini_summary: str = ""
    headlines: list = field(default_factory=list)
    routing: str = "local_only"          # local_only | gemini_brief
    message_suffix: str = ""
    overlay_rows: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "routing": self.routing,
            "gemini_called": self.gemini_called,
            "gemini_summary": self.gemini_summary,
            "local_snapshot": self.local_snapshot.as_dict() if self.local_snapshot else None,
        }


class CostAwareRouter:
    """로컬 1차 방어 → 임계치 통과 시에만 Gemini."""

    def __init__(self, cfg: LocalRuleConfig | None = None):
        self.cfg = cfg or get_config()

    def route_alert(
        self,
        ticker: str,
        name: str,
        *,
        change_pct: float,
        price: float,
        rule_triggered: bool = True,
    ) -> RouteDecision:
        decision = RouteDecision()

        if not self.cfg.enabled:
            decision.routing = "disabled"
            return decision

        snap = analyze_ticker_local(ticker, name=name, change_pct=change_pct, cfg=self.cfg)
        if not snap:
            return decision

        decision.local_snapshot = snap
        decision.overlay_rows = [
            ["RSI", f"{snap.rsi:.0f}", "★" if snap.rsi <= self.cfg.rsi_oversold else "+"],
            ["퀀트점수", f"{snap.hybrid_score}/100", "+"],
            ["로컬특이", str(snap.anomaly.count), "★" if snap.anomaly.triggered else "+"],
        ]

        if not rule_triggered:
            return decision

        allow_gemini = snap.anomaly.triggered
        if not self.cfg.require_anomaly_for_gemini:
            allow_gemini = True

        if not allow_gemini:
            decision.routing = "local_only"
            if snap.anomaly.reasons:
                decision.message_suffix = f" 로컬 지표: {snap.compact_summary}"
            return decision

        brief = fetch_gemini_brief(
            ticker, name, compact_context=snap.compact_summary,
        )
        if brief and brief.get("summary"):
            decision.gemini_called = True
            decision.routing = "gemini_brief"
            decision.gemini_summary = brief["summary"]
            decision.headlines = brief.get("headlines") or []
            decision.message_suffix = f" AI 분석: {brief['summary']}"
            decision.overlay_rows.append(["AI 원인", brief["summary"][:45], "🧠"])
        else:
            decision.routing = "local_only"
            if snap.compact_summary:
                decision.message_suffix = f" {snap.compact_summary}"

        return decision
