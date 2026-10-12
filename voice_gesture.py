"""
J.A.R.V.I.S — 음성 모드 제스처 (MediaPipe HandState)

G키 마우스 제어 OFF일 때:
  ✋ OPEN (손바닥)  → TTS/녹음 일시정지
  ✊ FIST (주먹)    → 음소거
  👍 THUMB_UP       → 확인 (대기 중 선택·승인)

환경변수:
  JARVIS_VOICE_GESTURES   1 — 음성 모드 제스처 (기본 ON)
"""
from __future__ import annotations

import os
from typing import Any, Callable

from gesture_control import HandState


def voice_gestures_enabled() -> bool:
    return os.getenv("JARVIS_VOICE_GESTURES", "1").strip().lower() in (
        "1", "true", "yes", "on",
    )


def detect_from_landmarks(hl, frame_w: int, frame_h: int) -> str:
    """MediaPipe hand landmarks → OPEN | FIST | THUMB_UP | ..."""
    hs = HandState(hl.landmark, frame_w, frame_h)
    g = hs.detect_gesture()
    if g == "OPEN":
        return "OPEN"
    if g == "FIST":
        return "FIST"
    if g == "THUMB_UP":
        return "THUMB_UP"
    if g == "THREE":
        return "THREE"
    return "NONE"


# jarvis_v2 호환 alias
OPEN_PALM = "OPEN"


class VoiceGestureHandler:
    """확정된 제스처(gc=True)에 대한 액션 디스패치."""

    def __init__(
        self,
        *,
        on_pause: Callable[[], None],
        on_mute: Callable[[], None],
        on_confirm: Callable[[], None],
        on_play_pause: Callable[[], None] | None = None,
        log_fn: Callable[[str, str], None] | None = None,
    ):
        self._on_pause = on_pause
        self._on_mute = on_mute
        self._on_confirm = on_confirm
        self._on_play_pause = on_play_pause
        self._log = log_fn or (lambda _l, _m: None)
        self._last_action = ""
        self._last_at = 0.0

    def handle(self, gesture: str, *, cooldown_sec: float = 1.2) -> bool:
        """제스처 처리. True면 액션 실행됨."""
        if not voice_gestures_enabled() or gesture in ("NONE", ""):
            return False
        import time
        now = time.time()
        if gesture == self._last_action and (now - self._last_at) < cooldown_sec:
            return False

        acted = False
        if gesture == "OPEN":
            self._on_pause()
            self._log("INFO", "✋ 손바닥 → 일시정지")
            acted = True
        elif gesture == "FIST":
            self._on_mute()
            self._log("INFO", "✊ 주먹 → 음소거")
            acted = True
        elif gesture == "THUMB_UP":
            self._on_confirm()
            self._log("INFO", "👍 엄지 → 확인")
            acted = True
        elif gesture == "THREE" and self._on_play_pause:
            self._on_play_pause()
            self._log("INFO", "🖐 세 손가락 → 재생/일시정지")
            acted = True

        if acted:
            self._last_action = gesture
            self._last_at = now
        return acted
