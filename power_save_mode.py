"""런타임 절전 모드 — 웹캠 OFF, Whisper/STT OFF, Ollama 예측 길이 확대."""
from __future__ import annotations

import os
import re
from typing import Callable

import numpy as np

_active = False
_saved_env: dict[str, str] = {}

LogFn = Callable[[str, str], None] | None


def is_active() -> bool:
    if _active:
        return True
    return os.getenv("JARVIS_POWER_SAVE", "").strip().lower() in ("1", "true", "yes", "on")


def _long_predict() -> str:
    return os.getenv("JARVIS_POWER_SAVE_NUM_PREDICT", "2048").strip() or "2048"


def _long_orch() -> str:
    return os.getenv("JARVIS_POWER_SAVE_ORCH_PREDICT", "1024").strip() or "1024"


def _long_agent() -> str:
    return os.getenv("JARVIS_POWER_SAVE_AGENT_PREDICT", "768").strip() or "768"


def status_message() -> str:
    if is_active():
        return (
            "Power save mode is active. Webcam and Whisper are off, and "
            f"Ollama num_predict is set to {os.getenv('OLLAMA_NUM_PREDICT', _long_predict())}. "
            "Press Z or say 'Power save off' to disable."
        )
    return "Power save mode is off. Press Z or say 'Power save on' to enable."


def set_power_save(
    on: bool,
    *,
    camera_off: Callable[[], None] | None = None,
    camera_on: Callable[[], None] | None = None,
    wake_off: Callable[[], None] | None = None,
    wake_on: Callable[[], None] | None = None,
    log_fn: LogFn = None,
) -> str:
    global _active, _saved_env

    def _log(level: str, msg: str) -> None:
        if log_fn:
            log_fn(level, msg)

    if on and is_active():
        return status_message()
    if not on and not is_active():
        return status_message()

    if on:
        for key in (
            "OLLAMA_NUM_PREDICT",
            "OLLAMA_ORCH_NUM_PREDICT",
            "OLLAMA_AGENT_NUM_PREDICT",
        ):
            _saved_env[key] = os.getenv(key, "")
        os.environ["OLLAMA_NUM_PREDICT"] = _long_predict()
        os.environ["OLLAMA_ORCH_NUM_PREDICT"] = _long_orch()
        os.environ["OLLAMA_AGENT_NUM_PREDICT"] = _long_agent()
        os.environ["JARVIS_POWER_SAVE"] = "1"
        _active = True
        if wake_off:
            try:
                wake_off()
            except Exception as e:
                _log("WARN", f"Power save: failed to stop wake word: {e}")
        if camera_off:
            try:
                camera_off()
            except Exception as e:
                _log("WARN", f"Power save: failed to stop webcam: {e}")
        _log("OK", "Power save mode ON — webcam & Whisper OFF, Ollama predict length expanded")
        return (
            "Power save mode enabled. Webcam and Whisper are turned off, and "
            f"response token limit is expanded to {_long_predict()}. "
            "Please send commands using Enter / on-screen keyboard or T typing dialog."
        )

    os.environ.pop("JARVIS_POWER_SAVE", None)
    for key, val in _saved_env.items():
        if val:
            os.environ[key] = val
        else:
            os.environ.pop(key, None)
    _saved_env = {}
    _active = False
    if camera_on:
        try:
            camera_on()
        except Exception as e:
            _log("WARN", f"Power save off: failed to restore webcam: {e}")
    if wake_on:
        try:
            wake_on()
        except Exception as e:
            _log("WARN", f"Power save off: failed to restore wake word: {e}")
    _log("OK", "Power save mode OFF")
    return "Power save mode disabled. Webcam and voice recognition are back online."


def toggle(**kwargs) -> str:
    return set_power_save(not is_active(), **kwargs)


def interpret_power_save_command(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if re.search(r"(?:절전\s*(?:모드)?\s*(?:상태|확인|뭐|어때)|power\s*save\s*(?:mode)?\s*(?:status|check))", raw, re.I):
        return status_message()
    if re.search(r"(?:절전\s*(?:모드)?\s*(?:켜|켜줘|켜라|on|시작|활성)|power\s*save\s*(?:mode)?\s*on)", raw, re.I):
        return None
    if re.search(r"(?:절전\s*(?:모드)?\s*(?:꺼|끄|끄줘|off|종료|해제|중지)|power\s*save\s*(?:mode)?\s*off)", raw, re.I):
        return None
    if re.fullmatch(r"(?:절전\s*(?:모드)?|power\s*save(?:\s*mode)?)", raw, re.I):
        return None
    return None


def wants_power_save_on(text: str) -> bool:
    raw = (text or "").strip()
    return bool(re.search(r"(?:절전\s*(?:모드)?\s*(?:켜|켜줘|켜라|on|시작|활성)|power\s*save\s*(?:mode)?\s*on|enable\s*power\s*save)", raw, re.I))


def wants_power_save_off(text: str) -> bool:
    raw = (text or "").strip()
    return bool(re.search(r"(?:절전\s*(?:모드)?\s*(?:꺼|끄|끄줘|off|종료|해제|중지)|power\s*save\s*(?:mode)?\s*off|disable\s*power\s*save)", raw, re.I))


class PowerSaveFrameReader:
    """Static HUD frame when webcam is off."""

    def __init__(self, width: int, height: int):
        from korean_font import put_text

        self._w = max(320, width)
        self._h = max(240, height)
        self._frame = np.zeros((self._h, self._w, 3), dtype=np.uint8)
        self._frame[:] = (12, 10, 8)
        put_text(
            self._frame,
            "Power Save Mode — Webcam OFF",
            (self._w // 2 - 165, self._h // 2 - 20),
            size=22,
            color=(255, 220, 0),
            bold=True,
        )
        put_text(
            self._frame,
            "Enter / T : Text Command  ·  Z : Exit Power Save",
            (self._w // 2 - 210, self._h // 2 + 18),
            size=15,
            color=(200, 210, 220),
        )

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def read(self, timeout: float = 1.0):
        return True, self._frame.copy()


_PANEL = (15, 12, 8)
_CYAN = (255, 220, 0)
_ON = (120, 220, 140)
_DIM = (190, 160, 40)


def draw_power_save_button(frame, active: bool):
    """HUD Power Save button (Z key toggle)."""
    import cv2

    from korean_font import put_text

    h, w = frame.shape[:2]
    x, y, bw, bh = 16, 36, 128, 34
    ov = frame.copy()
    cv2.rectangle(ov, (x, y), (x + bw, y + bh), _PANEL, -1)
    alpha = 0.88 if active else 0.72
    cv2.addWeighted(ov, alpha, frame, 1.0 - alpha * 0.85, 0, frame)
    border = _ON if active else _CYAN
    cv2.rectangle(frame, (x, y), (x + bw, y + bh), border, 1, cv2.LINE_AA)
    label = "PowerSave ON" if active else "Z PowerSave"
    put_text(frame, label, (x + 10, y + 23), size=14, color=border, bold=active)
    if active:
        put_text(frame, "Whisper·Cam OFF", (x + bw + 8, y + 23), size=12, color=_DIM)
    return frame
