"""local_rule_engine → alert_pipeline 연동 (선택적)."""
from __future__ import annotations


def push_local_anomaly_to_pipeline(runtime, evt: dict) -> None:
    """stock_monitor 이벤트의 로컬 특이점을 EventBridge에 주입."""
    if not runtime or not evt.get("local_anomaly"):
        return
    snap = evt.get("local_snapshot") or {}
    if not snap.get("anomaly", {}).get("triggered"):
        return

    try:
        from alert_pipeline.events import RiskEvent, Severity

        ticker = evt.get("ticker", "")
        name = evt.get("name", ticker)
        reasons = snap.get("anomaly", {}).get("reasons") or []
        sev = Severity.WARNING if evt.get("gemini_called") else Severity.INFO
        msg = f"로컬 퀀트 특이점 — {name}: " + "; ".join(reasons[:2])
        overlay = {
            "type": "table",
            "title": f"📊 로컬 퀀트 특이점 — {name}",
            "headers": ["항목", "값", "신호"],
            "rows": [
                ["RSI", f"{snap.get('rsi', 0):.0f}", "★"],
                ["변동률", f"{snap.get('change_pct', 0):+.1f}%", "+"],
                ["특이점", "; ".join(reasons[:2]) or "-", "★"],
            ],
            "alert_text": f"📊 {name} 로컬 특이점",
        }
        ev = RiskEvent(
            source="local_rule_engine",
            kind="tech_anomaly",
            severity=sev,
            message=msg,
            title=f"{name} 로컬 특이점",
            ticker=ticker,
            risk_score=float(snap.get("hybrid_score", 0)),
            reasons=reasons,
            meta={"overlay_table": overlay, "local_snapshot": snap, "gemini_called": evt.get("gemini_called")},
        )
        runtime.bridge.publish(ev)
    except Exception:
        pass
