"""
J.A.R.V.I.S — 알림 파이프라인: 중앙 이벤트 브리지(Bridge)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
risk_engine 의 스트레스/재조정 결과와 fds 의 행동 판정을 한 곳에서 수집·정규화.
jarvis_v2 메인 루프의 폴링 계약(get_events/requeue)을 그대로 따른다(드롭인 연동).
인메모리·스레드 세이프(deque + RLock) — 기존 워커들과 동일 정렬.
"""
from __future__ import annotations
import threading
from collections import deque

from .events import RiskEvent, Severity


class EventBridge:
    def __init__(self, maxlen: int = 200,
                 mdd_warn_pct: float = 15.0, mdd_crit_pct: float = 25.0):
        self._lock = threading.RLock()
        self._q: deque = deque(maxlen=maxlen)
        self._subs: list = []                     # 선택적 팬아웃(실시간 구독)
        self.mdd_warn_pct = mdd_warn_pct
        self.mdd_crit_pct = mdd_crit_pct
        self._counts = {"INFO": 0, "WARNING": 0, "CRITICAL": 0}

    # ── 발행/구독/폴링 ──────────────────────────────────────────
    def publish(self, ev: RiskEvent) -> RiskEvent:
        with self._lock:
            self._q.append(ev)
            self._counts[ev.severity.name] += 1
            subs = list(self._subs)
        for cb in subs:                            # 락 밖에서 콜백(데드락 방지)
            try:
                cb(ev)
            except Exception:
                pass
        return ev

    def subscribe(self, cb):
        with self._lock:
            self._subs.append(cb)

    def get_events(self) -> list[RiskEvent]:
        """대기 이벤트 전량 반환(메인 루프 폴링) — 기존 워커와 동일 시그니처."""
        with self._lock:
            out = list(self._q)
            self._q.clear()
            return out

    def requeue(self, events):
        """미처리 이벤트를 원래 순서로 큐 앞쪽에 복원(다음 IDLE 사이클)."""
        with self._lock:
            for ev in reversed(events):
                self._q.appendleft(ev)

    def stats(self) -> dict:
        with self._lock:
            return {"pending": len(self._q), "by_severity": dict(self._counts)}

    # ── 어댑터: risk_engine ─────────────────────────────────────
    def ingest_risk_assessment(self, ticker: str, assessment: dict, *,
                               name: str = "", briefing: str = "",
                               macro_level: str = "안정") -> RiskEvent:
        """risk_engine.assess() 결과(+선택적 forecast_briefing)를 위험 이벤트로."""
        stress = (assessment or {}).get("stress", {}) or {}
        reweight = (assessment or {}).get("reweight", {}) or {}
        mdd = abs(stress.get("estimated_mdd_pct", 0.0))
        triggered = bool(reweight.get("triggered"))
        if triggered or mdd >= self.mdd_crit_pct:
            sev = Severity.CRITICAL
        elif mdd >= self.mdd_warn_pct:
            sev = Severity.WARNING
        else:
            sev = Severity.INFO
        label = stress.get("worst", {}).get("label", "매크로 충격")
        disp = name or ticker
        msg = briefing or (
            f"선생님, {disp} 기준 '{label}' 시나리오에서 예상 최대 낙폭(MDD)이 "
            f"{mdd:.0f}%로 추정됩니다." +
            (f" 손실 임계 초과로 비중 {reweight.get('haircut_pct', 0):.0f}% 축소를 권고합니다."
             if triggered else " 관리 임계 이내로 비중을 유지합니다.")
        )
        overlay = {
            "type": "table", "title": f"⚠️ {disp} 리스크 평가",
            "headers": ["항목", "값", "신호"],
            "rows": [["예상 MDD", f"{mdd:.0f}%", "★" if sev >= Severity.WARNING else "+"],
                     ["시나리오", label, "+"],
                     ["매크로", macro_level, "+"],
                     ["재조정", "발동" if triggered else "유지", "★" if triggered else "+"]],
            "alert_text": f"⚠️ {disp} 예상 MDD {mdd:.0f}%",
        }
        ev = RiskEvent(source="risk_engine", kind="stress_reweight", severity=sev,
                       message=msg, title=f"{disp} 리스크 평가", ticker=ticker,
                       risk_score=mdd, reasons=[label] + (["재조정 발동"] if triggered else []),
                       meta={"overlay_table": overlay, "reweight": reweight,
                             "mdd_pct": mdd, "macro_level": macro_level})
        return self.publish(ev)

    # ── 어댑터: fds ─────────────────────────────────────────────
    def ingest_fds_decision(self, decision, *, ticker: str = "",
                            message: str = "") -> RiskEvent:
        """fds.FDSEngine.evaluate() 의 Decision 을 위험 이벤트로."""
        sev = {"block": Severity.CRITICAL,
               "step_up": Severity.WARNING,
               "allow": Severity.INFO}.get(decision.action, Severity.INFO)
        verb = {"block": "차단", "step_up": "추가 인증 요구", "allow": "허용"}[decision.action]
        msg = message or (
            f"선생님, 행동 위험 점수 {decision.risk_score}점 — "
            f"'{decision.command}' 요청을 {verb}했습니다."
        )
        overlay = {
            "type": "table", "title": f"🛡️ 이상 징후 탐지 ({verb})",
            "headers": ["항목", "값", "신호"],
            "rows": [["위험점수", f"{decision.risk_score}", "★" if sev >= Severity.WARNING else "+"],
                     ["조치", verb, "★" if decision.action == "block" else "+"],
                     ["명령", decision.command or "-", "+"]],
            "alert_text": f"🛡️ 위험점수 {decision.risk_score} — {verb}",
        }
        ev = RiskEvent(source="fds", kind=f"anomaly_{decision.action}", severity=sev,
                       message=msg, title=f"이상 징후 {verb}", ticker=ticker,
                       sub=getattr(decision, "sub", ""), risk_score=decision.risk_score,
                       reasons=list(getattr(decision, "reasons", [])),
                       meta={"overlay_table": overlay, "action": decision.action})
        return self.publish(ev)
