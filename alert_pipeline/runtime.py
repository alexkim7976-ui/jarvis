"""
J.A.R.V.I.S — 알림 파이프라인 런타임(Orchestrator)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EventBridge + AlertDispatcher + Collectors + SecureChannel 을 한 곳에서 조립.
jarvis_v2 메인 루프와의 계약:
  · overlay / audio_cue → 즉시 디스패치(구독)
  · TTS / CRITICAL push → pending 큐 → poll_idle() 에서 IDLE 시 1건 처리
"""
from __future__ import annotations

import os
import threading
from collections import deque
from typing import Callable, Optional
from urllib.parse import urlparse

from .bridge import EventBridge
from .collectors import FDSFeedAdapter, RiskFeedAdapter
from .dispatch import AlertDispatcher, AudioCue
from .events import RiskEvent, Severity
from .secure_channel import SecureChannel, SecureChannelError


class AudioCueQueue:
    """GarageBand/MIDI 백그라운드 큐 — 스레드 세이프 deque."""

    def __init__(self, maxlen: int = 32):
        self._q: deque = deque(maxlen=maxlen)
        self._lock = threading.RLock()

    def push(self, cue: AudioCue) -> None:
        with self._lock:
            self._q.append(cue.to_dict() if hasattr(cue, "to_dict") else cue)

    def drain(self) -> list:
        with self._lock:
            out = list(self._q)
            self._q.clear()
            return out

    def pending(self) -> int:
        with self._lock:
            return len(self._q)


class AlertPipelineRuntime:
    """실시간 위험·자산 분석 알림 파이프라인 — 싱글톤 대용 조립체."""

    def __init__(
        self,
        *,
        bridge: EventBridge | None = None,
        dispatcher: AlertDispatcher | None = None,
        risk_feed: RiskFeedAdapter | None = None,
        fds_feed: FDSFeedAdapter | None = None,
        secure_channel: SecureChannel | None = None,
        logger: Callable[[str, str], None] | None = None,
    ):
        self.bridge = bridge or EventBridge()
        self.audio_cues = AudioCueQueue()
        self._tts_pending: deque = deque(maxlen=50)
        self._push_pending: deque = deque(maxlen=20)
        self._lock = threading.RLock()
        self._log = logger or (lambda _lvl, _msg: None)
        self.secure_channel = secure_channel
        self._push_fn: Callable | None = None
        self.risk_feed = risk_feed or RiskFeedAdapter(self.bridge)
        self.fds_feed = fds_feed or FDSFeedAdapter(self.bridge, min_severity=Severity.WARNING)

        if dispatcher is None:
            dispatcher = AlertDispatcher(
                overlay_sink=self._overlay_sink,
                audio_cue_sink=self.audio_cues.push,
                tts_sink=self._enqueue_tts,
                push_sink=self._enqueue_push,
            )
        self.dispatcher = dispatcher
        self.bridge.subscribe(self._on_event)

        self._fds_engine = None

    # ── 싱크 어댑터 ─────────────────────────────────────────────
    _overlay_cb: Callable | None = None

    def bind_overlay(self, fn: Callable[[dict], None]) -> None:
        self._overlay_cb = fn

    def _overlay_sink(self, overlay_evt: dict) -> None:
        if self._overlay_cb and overlay_evt.get("overlay_table"):
            try:
                alert = overlay_evt.get("severity") in ("WARNING", "CRITICAL")
                self._overlay_cb(overlay_evt["overlay_table"], 25 if alert else 18, alert=alert)
            except TypeError:
                self._overlay_cb(overlay_evt["overlay_table"])

    def _enqueue_tts(self, text: str) -> None:
        with self._lock:
            self._tts_pending.append(text)

    def _enqueue_push(self, text: str, ctx: dict) -> None:
        with self._lock:
            self._push_pending.append((text, ctx))

    def _on_event(self, ev: RiskEvent) -> None:
        plan = self.dispatcher.dispatch(ev)
        self._log(
            "INFO",
            f"🛡 Alert [{ev.severity.name}] {ev.source}/{ev.kind} → "
            f"overlay={plan.overlay} tts={plan.tts} push={plan.push} "
            f"audio={plan.audio_cue.motif if plan.audio_cue else '-'}",
        )

    # ── jarvis_v2 IDLE 훅 ─────────────────────────────────────────
    def poll_idle(
        self,
        *,
        can_speak: bool,
        on_speak: Callable[[str], None],
        on_emergency: Callable[[str], None] | None = None,
        on_push: Callable[[str, dict], None] | None = None,
    ) -> dict | None:
        """IDLE·미재생 상태에서 TTS/push 1건 처리. Returns 처리된 RiskEvent 메타."""
        if not can_speak:
            return None
        out = {}

        with self._lock:
            if self._tts_pending:
                msg = self._tts_pending.popleft()
                sev = "CRITICAL"  # 큐에 severity 미포함 — emergency 우선
                if on_emergency and ("차단" in msg or "CRITICAL" in msg or "재조정" in msg):
                    on_emergency(msg)
                elif on_speak:
                    on_speak(msg)
                out["tts"] = msg[:80]

            if self._push_pending:
                text, ctx = self._push_pending.popleft()
                if self.secure_channel and ctx.get("use_secure_channel"):
                    token = ctx.get("sender_token", "")
                    try:
                        meta = self.secure_channel.send(
                            {"text": text, "context": ctx}, sender_token=token,
                        )
                        self._log("OK", f"SecureChannel 전송 — delivered={meta.get('delivered')}")
                    except SecureChannelError as e:
                        self._log("WARN", f"SecureChannel 거부({e.stage}): {e.reason}")
                elif on_push:
                    on_push(text, ctx)
                elif getattr(self, "_push_fn", None):
                    self._push_fn(text, ctx)
                out["push"] = ctx.get("kind", "")

        return out or None

    def attach_fds(self, engine):
        self._fds_engine = self.fds_feed.attach(engine)
        return self._fds_engine

    @property
    def fds_engine(self):
        return self._fds_engine

    def stats(self) -> dict:
        return {
            "bridge": self.bridge.stats(),
            "dispatcher": self.dispatcher.stats(),
            "audio_cues_pending": self.audio_cues.pending(),
            "tts_pending": len(self._tts_pending),
            "push_pending": len(self._push_pending),
        }


def _parse_allow_hosts(raw: str) -> frozenset | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def create_jarvis_runtime(
    *,
    overlay_fn: Callable | None = None,
    push_fn: Callable | None = None,
    logger: Callable[[str, str], None] | None = None,
    token_verifier=None,
    sender_token: str = "",
) -> AlertPipelineRuntime:
    """jarvis_v2 부팅용 팩토리 — SecureChannel·싱크·FDS 엔진까지 조립."""
    endpoint = os.getenv("ALERT_SECURE_ENDPOINT", "").strip()
    secure = None
    if endpoint and token_verifier and sender_token:
        try:
            from security import validate_url
            import net
            secure = SecureChannel(
                endpoint,
                token_verifier=token_verifier,
                required_scope=os.getenv("ALERT_PUSH_SCOPE", "alert.push"),
                url_validator=validate_url,
                transport=net.post,
                allow_hosts=_parse_allow_hosts(os.getenv("ALERT_ALLOW_HOSTS", "")),
                require_device_binding=os.getenv("ALERT_REQUIRE_DEVICE_BINDING", "1").strip()
                not in ("0", "false", "off"),
            )
        except Exception as e:
            if logger:
                logger("WARN", f"SecureChannel 초기화 실패 — push_if_away 폴백: {e}")

    rt = AlertPipelineRuntime(logger=logger, secure_channel=secure)
    rt._push_fn = push_fn
    if secure and sender_token:
        rt.dispatcher._secure_token = sender_token
    if overlay_fn:
        rt.bind_overlay(overlay_fn)

    if os.getenv("ALERT_PIPELINE_FDS", "1").strip().lower() not in ("0", "false", "off"):
        try:
            from fds import FDSEngine, FDSConfig
            eng = FDSEngine(FDSConfig())
            rt.attach_fds(eng)
        except Exception as e:
            if logger:
                logger("WARN", f"FDS 엔진 부착 실패: {e}")

    return rt


_runtime: AlertPipelineRuntime | None = None
_runtime_lock = threading.Lock()


def get_runtime() -> AlertPipelineRuntime | None:
    return _runtime


def init_runtime(rt: AlertPipelineRuntime) -> AlertPipelineRuntime:
    global _runtime
    with _runtime_lock:
        _runtime = rt
    return rt
