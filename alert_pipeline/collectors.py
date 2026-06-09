"""
J.A.R.V.I.S — 알림 파이프라인: 소스 어댑터(Collectors)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
risk_engine / fds 를 수정하지 않고 EventBridge 로 이벤트를 주입하는 얇은 어댑터.
"""
from __future__ import annotations

from typing import Callable, Optional

from .bridge import EventBridge
from .events import RiskEvent, Severity


class RiskFeedAdapter:
    """risk_engine.assess() 또는 stock_quant 도구 결과 → EventBridge."""

    def __init__(self, bridge: EventBridge, *, min_severity: Severity = Severity.INFO):
        self.bridge = bridge
        self.min_severity = min_severity

    def ingest_assessment(
        self,
        ticker: str,
        assessment: dict,
        *,
        name: str = "",
        briefing: str = "",
        macro_level: str = "안정",
    ) -> Optional[RiskEvent]:
        if not assessment:
            return None
        ev = self.bridge.ingest_risk_assessment(
            ticker, assessment, name=name, briefing=briefing, macro_level=macro_level,
        )
        if ev.severity < self.min_severity:
            return ev
        return ev

    def ingest_stock_tool_result(self, result: dict) -> Optional[RiskEvent]:
        """execute_stock_tool / matrix 분석 결과에서 리스크 이벤트 추출."""
        if not isinstance(result, dict) or not result.get("success"):
            return None
        ticker = result.get("ticker") or result.get("resolved") or ""
        name = result.get("name") or ticker
        macro = result.get("macro_status") or {}
        macro_level = macro.get("risk_level", "안정")
        briefing = result.get("forecast_briefing") or ""

        assessment = result.get("stress_assessment")
        if not assessment:
            rw = result.get("risk_reweight") or {}
            if not rw and not briefing:
                return None
            assessment = {"stress": {}, "reweight": rw}

        rw = (assessment or {}).get("reweight") or {}
        if not briefing and not rw.get("triggered") and macro_level == "안정":
            return None

        return self.ingest_assessment(
            ticker, assessment, name=name, briefing=briefing, macro_level=macro_level,
        )


class FDSFeedAdapter:
    """fds.FDSEngine.evaluate() 를 래핑해 Decision → EventBridge (fds 모듈 비수정)."""

    def __init__(self, bridge: EventBridge, *, min_severity: Severity = Severity.WARNING):
        self.bridge = bridge
        self.min_severity = min_severity

    def ingest_decision(self, decision, *, ticker: str = "", message: str = "") -> Optional[RiskEvent]:
        if getattr(decision, "action", "allow") == "allow":
            return None
        ev = self.bridge.ingest_fds_decision(decision, ticker=ticker, message=message)
        if ev.severity < self.min_severity:
            return ev
        return ev

    def attach(self, engine, *, ticker_resolver: Callable | None = None):
        """FDSEngine.evaluate 를 래핑 — 기존 호출자는 그대로, 파이프라인만 구독."""
        original = engine.evaluate

        def wrapped(event, *args, **kwargs):
            decision = original(event, *args, **kwargs)
            ticker = ""
            if ticker_resolver:
                try:
                    ticker = ticker_resolver(event, decision) or ""
                except Exception:
                    ticker = ""
            self.ingest_decision(decision, ticker=ticker)
            return decision

        engine.evaluate = wrapped  # type: ignore[method-assign]
        return engine
