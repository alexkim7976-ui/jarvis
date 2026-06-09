"""
J.A.R.V.I.S — 알림 파이프라인: 디스패치 계층(Dispatcher)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EventBridge 가 정규화한 RiskEvent 를 Severity 정책에 따라 싱크로 분배.
싱크는 의존성 역전(콜백) — jarvis_v2/price_alert/jarvis_link 어디에도 직접 결합하지 않는다.

싱크 종류(모두 선택적):
  · tts_sink(text)        — 음성 브리핑(코어 TTS 큐)
  · overlay_sink(dict)    — HUD 오버레이(to_overlay_event 규약)
  · audio_cue_sink(cue)   — GarageBand/MIDI 백그라운드 큐(분위기 사운드 모티프)
  · push_sink(text, ctx)  — 외출 알림(jarvis_link.push_alert) / 보안채널

Severity 정책:
  INFO     → 오버레이만(조용)            audio: 없음
  WARNING  → 오버레이 + TTS              audio: warning 모티프(50%, 90bpm)
  CRITICAL → 오버레이 + TTS + 원격 푸시   audio: alert 모티프(90%, 120bpm, 루프)
"""
from __future__ import annotations
import threading
from dataclasses import dataclass, field

from .events import RiskEvent, Severity


@dataclass
class AudioCue:
    """GarageBand/MIDI 백그라운드 큐로 흘려보낼 분위기 사운드 지시(드라이 데이터)."""
    motif: str                     # "warning" | "alert" | ...
    intensity: int                 # 0~100 (믹스 볼륨/긴장도)
    tempo_bpm: int = 90
    loop: bool = False

    def to_dict(self) -> dict:
        return {"motif": self.motif, "intensity": self.intensity,
                "tempo_bpm": self.tempo_bpm, "loop": self.loop}


# Severity → AudioCue 매핑(없으면 무음)
DEFAULT_AUDIO_CUES = {
    Severity.WARNING: AudioCue(motif="warning", intensity=50, tempo_bpm=90, loop=False),
    Severity.CRITICAL: AudioCue(motif="alert", intensity=90, tempo_bpm=120, loop=True),
}


@dataclass
class DispatchPlan:
    """한 이벤트의 분배 결과(테스트/감사용 — 어떤 싱크가 타졌는지)."""
    event: RiskEvent
    overlay: bool = False
    tts: bool = False
    push: bool = False
    audio_cue: object = None       # AudioCue | None
    sinks_fired: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"severity": self.event.severity.name, "overlay": self.overlay,
                "tts": self.tts, "push": self.push,
                "audio_cue": self.audio_cue.to_dict() if self.audio_cue else None,
                "sinks_fired": list(self.sinks_fired)}


class AlertDispatcher:
    """Severity 기반 라우팅 + 싱크 팬아웃.

    push_from: 이 등급 이상에서만 원격 푸시(기본 CRITICAL) — 외출 알림 남발 방지.
    tts_from:  이 등급 이상에서만 음성 브리핑(기본 WARNING).
    """
    def __init__(self, *, tts_sink=None, overlay_sink=None,
                 audio_cue_sink=None, push_sink=None,
                 tts_from: Severity = Severity.WARNING,
                 push_from: Severity = Severity.CRITICAL,
                 audio_cues: dict | None = None):
        self.tts_sink = tts_sink
        self.overlay_sink = overlay_sink
        self.audio_cue_sink = audio_cue_sink
        self.push_sink = push_sink
        self.tts_from = tts_from
        self.push_from = push_from
        self.audio_cues = dict(DEFAULT_AUDIO_CUES if audio_cues is None else audio_cues)
        self._lock = threading.RLock()
        self._history: list = []
        self._counts = {"overlay": 0, "tts": 0, "push": 0, "audio_cue": 0}
        self._secure_token = ""

    def dispatch(self, ev: RiskEvent) -> DispatchPlan:
        sev = ev.severity
        plan = DispatchPlan(event=ev)

        # 1) 오버레이 — 모든 등급(조용한 INFO 포함). HUD 가 항상 최신 상태를 본다.
        if self.overlay_sink is not None:
            self._fire(self.overlay_sink, ev.to_overlay_event(), plan, "overlay")
            plan.overlay = True

        # 2) TTS — WARNING 이상
        if self.tts_sink is not None and sev >= self.tts_from:
            self._fire(self.tts_sink, ev.message, plan, "tts")
            plan.tts = True

        # 3) 오디오 큐(GarageBand/MIDI) — Severity 모티프 매핑
        cue = self.audio_cues.get(sev)
        if cue is not None and self.audio_cue_sink is not None:
            self._fire(self.audio_cue_sink, cue, plan, "audio_cue")
            plan.audio_cue = cue

        # 4) 원격 푸시 — CRITICAL 이상(보안 채널/모바일)
        if self.push_sink is not None and sev >= self.push_from:
            ctx = {"source": ev.source, "kind": ev.kind, "ticker": ev.ticker,
                   "risk_score": ev.risk_score, "severity": sev.name}
            if getattr(self, "_secure_token", None):
                ctx["use_secure_channel"] = True
                ctx["sender_token"] = self._secure_token
            self._fire2(self.push_sink, ev.message, ctx, plan, "push")
            plan.push = True

        with self._lock:
            for k in plan.sinks_fired:
                self._counts[k] += 1
            self._history.append(plan)
        return plan

    # bridge 구독 어댑터로 그대로 꽂을 수 있게(subscribe(dispatcher.on_event))
    def on_event(self, ev: RiskEvent) -> DispatchPlan:
        return self.dispatch(ev)

    def drain(self, bridge) -> list:
        """폴링 모드: 브리지의 대기 이벤트를 전량 디스패치(jarvis_v2 IDLE 훅 호환)."""
        plans = [self.dispatch(ev) for ev in bridge.get_events()]
        return plans

    def stats(self) -> dict:
        with self._lock:
            return {"dispatched": len(self._history), "by_sink": dict(self._counts)}

    # ── 내부: 싱크 호출(예외 격리 — 한 싱크 실패가 파이프라인을 죽이지 않게) ──
    def _fire(self, sink, arg, plan, label):
        try:
            sink(arg)
            plan.sinks_fired.append(label)
        except Exception:
            pass

    def _fire2(self, sink, a, b, plan, label):
        try:
            sink(a, b)
            plan.sinks_fired.append(label)
        except Exception:
            pass
