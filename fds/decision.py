"""
J.A.R.V.I.S — FDS: 판정·오케스트레이션(Decision Engine)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
수집 → 규칙 → 점수 → 판정을 엮는 동적 신뢰 미들웨어의 코어.

위험 점수 분기(설정 가능):
   score <  40        → allow      (통과)
   40 ≤ score < 70    → step_up    (jarvis_link 일회성 승인코드 재요구 훅)
   score ≥ 70         → block      (즉시 차단)

차단된 요청은 베이스라인 학습에서 제외(오염 방지). 모든 판정은 감사로그에 적재.
인메모리·스레드 세이프.
"""
from __future__ import annotations
import time, threading
from dataclasses import dataclass, field
from collections import deque

from .collector import EventCollector, BehaviorEvent
from .profile import BaselineProfile
from .scorer import RiskScorer
from .rules import DEFAULT_RULES


# ── 설정 ────────────────────────────────────────────────────────
DEFAULT_WEIGHTS = {
    "threshold":     70,    # 하드캡 초과 금액 — 단독 즉시 차단
    "sequence":      50,    # 위험 명령 시퀀스
    "velocity":      40,    # 명령 폭주
    "amount_zscore": 40,    # 금액 통계 이탈
    "new_device":    25,    # 미사용 기기 활성
    "off_hours":     15,    # 비정상 시간대
}
DEFAULT_DANGEROUS_SEQUENCES = {
    ("unlock_risk_guard", "rebalance_execute"),
    ("unlock_risk_guard", "sell_all"),
    ("unlock_risk_guard", "withdraw"),
}


@dataclass
class FDSConfig:
    velocity_window_sec: int = 60
    velocity_max: int = 5                       # 윈도우 내 이 수치 초과 시 속도 시그널
    amount_hard_cap: float = 100_000_000        # 하드 임계(1억)
    z_threshold: float = 3.0
    step_up_band: tuple = (40, 70)              # [lo, hi): step_up / score≥hi: block
    off_hours: frozenset = frozenset({0, 1, 2, 3, 4, 5})
    weights: dict = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    dangerous_sequences: set = field(default_factory=lambda: set(DEFAULT_DANGEROUS_SEQUENCES))


@dataclass
class Decision:
    action: str                 # allow | step_up | block
    risk_score: int
    signals: list               # list[Signal]
    reasons: list               # list[str]
    sub: str = ""
    command: str = ""
    step_up_challenge: object = None     # step-up 훅 반환값(jarvis_link 승인 요구 등)


class FDSEngine:
    """동적 위험평가 엔진.

    step_up_hook(event, decision) -> challenge :
        40~70 구간에서 호출. 운영 시 jarvis_link.issue_approval(context) 를 연결해
        일회성 승인코드 재인증(step-up)을 강제한다(인터페이스만 개통, 비파괴).
    """
    def __init__(self, config: FDSConfig | None = None, *,
                 rules=None, step_up_hook=None, audit_max: int = 2048):
        self.config = config or FDSConfig()
        self.collector = EventCollector(self.config.velocity_window_sec)
        self.profile = BaselineProfile()
        self.scorer = RiskScorer()
        self.rules = list(rules) if rules is not None else list(DEFAULT_RULES)
        self.step_up_hook = step_up_hook
        self._lock = threading.RLock()
        self._audit: deque = deque(maxlen=audit_max)

    def evaluate(self, event: BehaviorEvent) -> Decision:
        # 1) 수집(속도/시퀀스 원천) — 시도 자체를 기록(폭주 공격도 카운트)
        self.collector.record(event)
        # 2) 규칙 평가(현재 베이스라인 기준 — 학습은 판정 후)
        signals = []
        for rule in self.rules:
            signals.extend(rule(event, self.collector, self.profile, self.config))
        # 3) 점수 → 4) 판정
        score = self.scorer.score(signals)
        action = self._action(score)
        decision = Decision(action=action, risk_score=score, signals=signals,
                            reasons=[f"{s.name}(+{s.weight:g}): {s.detail}" for s in signals],
                            sub=event.sub, command=event.command)
        # 5) 감사로그(민감값 미포함 — 메타만)
        self._record_audit(event, decision)
        # 6) 학습: 차단은 제외(베이스라인 오염 방지)
        if action != "block":
            self.profile.update(event)
        # 7) step-up 훅: 중위험 구간에서 jarvis_link 재승인 연결
        if action == "step_up" and self.step_up_hook:
            decision.step_up_challenge = self.step_up_hook(event, decision)
        return decision

    def _action(self, score: int) -> str:
        lo, hi = self.config.step_up_band
        if score >= hi:
            return "block"
        if score >= lo:
            return "step_up"
        return "allow"

    def _record_audit(self, event, decision):
        with self._lock:
            self._audit.append({
                "ts": event.ts, "sub": event.sub, "device_id": event.device_id,
                "command": event.command, "action": decision.action,
                "risk_score": decision.risk_score,
                "signals": [s.name for s in decision.signals],
            })

    def audit_log(self, sub: str | None = None) -> list:
        with self._lock:
            if sub is None:
                return list(self._audit)
            return [a for a in self._audit if a["sub"] == sub]
