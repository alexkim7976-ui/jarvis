"""
J.A.R.V.I.S — FDS: 위험 점수 합산(Scorer)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
시그널 가중치를 합산해 0~100 의 risk_score 로 환산한다.
가산 모델(해석 가능) — 어떤 시그널이 점수를 끌어올렸는지 그대로 추적된다.
"""
from __future__ import annotations


class RiskScorer:
    def __init__(self, ceiling: int = 100):
        self.ceiling = ceiling

    def score(self, signals) -> int:
        total = sum(s.weight for s in signals)
        return int(min(self.ceiling, max(0, round(total))))
