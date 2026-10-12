"""
J.A.R.V.I.S — 음성 모드 제스처 (MediaPipe HandState)

G키 마우스 제어 OFF일 때:
  ☝ POINT (검지)     → 음성 명령 녹음 시작
  ✌ PEACE (브이)     → 녹음 종료 후 명령 전송
  ✋ OPEN (손바닥)    → TTS/녹음 일시정지
  ✊ FIST (주먹)      → 음소거
  👍 THUMB_UP         → 확인 (대기 중 선택·승인)
  🖐 THREE            → 재생/일시정지

환경변수:
  JARVIS_VOICE_GESTURES   1 — 일시정지/음소거/확인 (기본 ON)
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
    if g in ("OPEN", "FIST", "THUMB_UP", "THREE", "POINT", "PEACE"):
        return g
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
        on_command_start: Callable[[], bool] | None = None,
        on_command_stop: Callable[[], bool] | None = None,
        log_fn: Callable[[str, str], None] | None = None,
    ):
        self._on_pause = on_pause
        self._on_mute = on_mute
        self._on_confirm = on_confirm
        self._on_play_pause = on_play_pause
        self._on_command_start = on_command_start
        self._on_command_stop = on_command_stop
        self._log = log_fn or (lambda _l, _m: None)
        self._last_action = ""
        self._last_at = 0.0
        self._latch = ""
        self._release_frames = 0

    def observe(self, gesture: str) -> None:
        """매 프레임 호출. 잠깐 손이 빠져도 같은 제스처를 다시 실행하지 않는다."""
        if not self._latch or gesture == self._latch:
            self._release_frames = 0
            return
        if gesture in ("NONE", ""):
            self._release_frames += 1
            if self._release_frames >= 8:
                self._latch = ""
                self._release_frames = 0
            return
        self._latch = ""
        self._release_frames = 0

    def handle(self, gesture: str, *, cooldown_sec: float = 1.2) -> bool:
        """제스처 처리. True면 액션 실행됨."""
        if gesture in ("NONE", ""):
            return False
        if gesture == self._latch:
            return False
        import time
        now = time.time()
        if gesture == self._last_action and (now - self._last_at) < cooldown_sec:
            return False

        acted = False
        if gesture == "POINT" and self._on_command_start:
            acted = bool(self._on_command_start())
            if acted:
                self._log("INFO", "☝ 검지 → 손짓 명령 시작")
        elif gesture == "PEACE" and self._on_command_stop:
            acted = bool(self._on_command_stop())
            if acted:
                self._log("INFO", "✌ 브이 → 손짓 명령 전송")
        elif voice_gestures_enabled() and gesture == "OPEN":
            self._on_pause()
            self._log("INFO", "✋ 손바닥 → 일시정지")
            acted = True
        elif voice_gestures_enabled() and gesture == "FIST":
            self._on_mute()
            self._log("INFO", "✊ 주먹 → 음소거")
            acted = True
        elif voice_gestures_enabled() and gesture == "THUMB_UP":
            self._on_confirm()
            self._log("INFO", "👍 엄지 → 확인")
            acted = True
        elif voice_gestures_enabled() and gesture == "THREE" and self._on_play_pause:
            self._on_play_pause()
            self._log("INFO", "🖐 세 손가락 → 재생/일시정지")
            acted = True

        if acted:
            self._last_action = gesture
            self._last_at = now
            self._latch = gesture
        return acted
