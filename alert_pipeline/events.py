"""
J.A.R.V.I.S — 알림 파이프라인: 정규화 이벤트 봉투(Events)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
risk_engine(자산/트레이딩 리스크)와 fds(행동 이상)를 하나의 위험 이벤트로 정규화.
Severity(INFO/WARNING/CRITICAL)로 디스패치 정책을 일원화하고,
to_overlay_event()으로 기존 jarvis_v2 폴링 루프가 그대로 소비하는 dict 형태를 제공.
"""
from __future__ import annotations
import time
from enum import IntEnum
from dataclasses import dataclass, field
from datetime import datetime


class Severity(IntEnum):
    """순서 비교 가능(INFO < WARNING < CRITICAL) — 임계 분기에 사용."""
    INFO = 10
    WARNING = 20
    CRITICAL = 30

    @classmethod
    def from_name(cls, name: str) -> "Severity":
        return cls[str(name).strip().upper()]


@dataclass
class RiskEvent:
    source: str                      # "risk_engine" | "fds" | ...
    kind: str                        # 세부 종류(예: stress_reweight, anomaly_block)
    severity: Severity
    message: str                     # 사람/TTS 용 한국어 브리핑 텍스트
    title: str = ""
    ticker: str = ""
    sub: str = ""                    # fds 주체(sub)
    risk_score: float = 0.0
    reasons: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_overlay_event(self) -> dict:
        """기존 워커 이벤트 dict 규약(price_alert/background_monitor 호환)으로 변환."""
        return {
            "source": self.source,
            "type": self.kind,
            "severity": self.severity.name,
            "message": self.message,
            "alert_text": self.title or (self.message[:60]),
            "overlay_table": self.meta.get("overlay_table"),
            "reasons": list(self.reasons),
            "ticker": self.ticker,
            "risk_score": self.risk_score,
            "timestamp": datetime.fromtimestamp(self.ts).isoformat(),
            "urgent": self.severity >= Severity.CRITICAL,
        }

    @property
    def urgent(self) -> bool:
        return self.severity >= Severity.CRITICAL
