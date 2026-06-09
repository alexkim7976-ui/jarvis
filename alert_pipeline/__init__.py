"""
J.A.R.V.I.S — 실시간 위험·자산 분석 알림 파이프라인(Alert Pipeline)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
risk_engine(자산/트레이딩 리스크)와 fds(행동 이상)를 하나의 위험 이벤트로 연합.

  risk_engine.assess ─┐
                      ├─▶ EventBridge ─(Severity)▶ AlertDispatcher ─▶ 싱크들
  fds.Decision ───────┘   (정규화·폴링)              (라우팅·팬아웃)    ├ overlay_sink (HUD)
                                                                       ├ tts_sink    (음성)
                                                                       ├ audio_cue_sink (GarageBand/MIDI)
                                                                       └ push_sink → SecureChannel
                                                                                     (OIDC+DeviceGuard 제로트러스트)

설계 원칙:
  · 격리·비파괴: jarvis_v2/price_alert/risk_engine/fds 어디도 수정하지 않는다(콜백 주입만).
  · 인메모리·스레드 세이프(deque + RLock) — 기존 워커 정렬과 동일.
  · 폴링 계약(get_events/requeue) 호환 — 메인 루프에 드롭인.
  · 외부 전송은 인증 세션 + 바인딩 기기 컨텍스트에서만(SecureChannel).
"""
from .events import Severity, RiskEvent
from .bridge import EventBridge
from .collectors import RiskFeedAdapter, FDSFeedAdapter
from .dispatch import AlertDispatcher, AudioCue, DispatchPlan, DEFAULT_AUDIO_CUES
from .secure_channel import SecureChannel, SecureChannelError
from .runtime import (
    AlertPipelineRuntime, AudioCueQueue,
    create_jarvis_runtime, get_runtime, init_runtime,
)

__all__ = [
    "Severity", "RiskEvent", "EventBridge",
    "RiskFeedAdapter", "FDSFeedAdapter",
    "AlertDispatcher", "AudioCue", "DispatchPlan", "DEFAULT_AUDIO_CUES",
    "SecureChannel", "SecureChannelError",
    "AlertPipelineRuntime", "AudioCueQueue",
    "create_jarvis_runtime", "get_runtime", "init_runtime",
]
