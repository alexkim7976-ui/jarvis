"""voice_gesture — 음성 모드 제스처 핸들러."""
from __future__ import annotations

from voice_gesture import VoiceGestureHandler, voice_gestures_enabled


def test_voice_gestures_enabled_default():
    assert voice_gestures_enabled() is True


def test_handler_pause_and_cooldown():
    calls = {"pause": 0, "mute": 0, "confirm": 0}

    h = VoiceGestureHandler(
        on_pause=lambda: calls.__setitem__("pause", calls["pause"] + 1),
        on_mute=lambda: calls.__setitem__("mute", calls["mute"] + 1),
        on_confirm=lambda: calls.__setitem__("confirm", calls["confirm"] + 1),
    )
    assert h.handle("OPEN", cooldown_sec=0) is True
    assert h.handle("OPEN", cooldown_sec=999) is False  # same gesture cooldown
    assert calls["pause"] == 1

    assert h.handle("FIST", cooldown_sec=0) is True
    assert calls["mute"] == 1

    assert h.handle("THUMB_UP", cooldown_sec=0) is True
    assert calls["confirm"] == 1

    assert h.handle("NONE", cooldown_sec=0) is False
