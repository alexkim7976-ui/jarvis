"""
J.A.R.V.I.S — 이상 징후 탐지(FDS) / 동적 신뢰 패키지.

OIDC(누구) + Device Binding(어떤 기기) 이후, '그 행동이 평소다운가'를 본다.
이미 인증·기기검증을 통과한 요청에 실시간 위험점수를 매겨
  allow / step_up(jarvis_link 재승인) / block 으로 분기하는 동적 신뢰 계층.

구성:
  BehaviorEvent / EventCollector  — 행동 이벤트 수집(슬라이딩 윈도우)
  BaselineProfile                 — 주체별 평소 패턴 누적(Welford z-score)
  rules(*)                        — 결정론적 규칙(속도/시퀀스/임계/기기/시간) + z-score
  RiskScorer                      — 시그널 가중합 → 0~100
  FDSEngine / FDSConfig / Decision — 판정·감사로그·step-up 훅

DeviceGuard 결합(개념): 서명 검증 통과 직후 BehaviorEvent 를 만들어
FDSEngine.evaluate() 에 흘려보내고, step_up 이면 jarvis_link 재승인을 요구한다.
fds 는 프레임워크 비종속(순수 로직) — 기존 62개 테스트와 완전 격리.
"""
from .collector import BehaviorEvent, EventCollector
from .profile import BaselineProfile
from .rules import (Signal, DEFAULT_RULES, rule_threshold, rule_velocity,
                    rule_sequence, rule_new_device, rule_off_hours, rule_amount_zscore)
from .scorer import RiskScorer
from .decision import (FDSEngine, FDSConfig, Decision,
                       DEFAULT_WEIGHTS, DEFAULT_DANGEROUS_SEQUENCES)

__all__ = [
    "BehaviorEvent", "EventCollector", "BaselineProfile",
    "Signal", "DEFAULT_RULES", "rule_threshold", "rule_velocity",
    "rule_sequence", "rule_new_device", "rule_off_hours", "rule_amount_zscore",
    "RiskScorer", "FDSEngine", "FDSConfig", "Decision",
    "DEFAULT_WEIGHTS", "DEFAULT_DANGEROUS_SEQUENCES",
]
