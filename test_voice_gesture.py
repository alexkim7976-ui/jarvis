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


def test_point_starts_command_once_until_release():
    calls = {"start": 0, "stop": 0}

    h = VoiceGestureHandler(
        on_pause=lambda: None,
        on_mute=lambda: None,
        on_confirm=lambda: None,
        on_command_start=lambda: calls.__setitem__("start", calls["start"] + 1) or True,
        on_command_stop=lambda: calls.__setitem__("stop", calls["stop"] + 1) or True,
    )
    h.observe("POINT")
    assert h.handle("POINT", cooldown_sec=0) is True
    h.observe("POINT")
    assert h.handle("POINT", cooldown_sec=0) is False
    assert calls["start"] == 1

    h.observe("NONE")
    h.observe("PEACE")
    assert h.handle("PEACE", cooldown_sec=0) is True
    assert calls["stop"] == 1


def test_brief_drop_does_not_retrigger():
    calls = {"start": 0}
    h = VoiceGestureHandler(
        on_pause=lambda: None,
        on_mute=lambda: None,
        on_confirm=lambda: None,
        on_command_start=lambda: calls.__setitem__("start", calls["start"] + 1) or True,
    )
    h.observe("POINT")
    assert h.handle("POINT", cooldown_sec=0) is True
    h.observe("NONE")
    h.observe("POINT")
    assert h.handle("POINT", cooldown_sec=0) is False
    assert calls["start"] == 1
    for _ in range(8):
        h.observe("NONE")
    h.observe("POINT")
    assert h.handle("POINT", cooldown_sec=0) is True
    assert calls["start"] == 2


def test_command_gestures_work_when_voice_gestures_disabled(monkeypatch):
    monkeypatch.setenv("JARVIS_VOICE_GESTURES", "0")
    started = {"n": 0}
    h = VoiceGestureHandler(
        on_pause=lambda: None,
        on_mute=lambda: None,
        on_confirm=lambda: None,
        on_command_start=lambda: started.__setitem__("n", started["n"] + 1) or True,
    )
    assert h.handle("OPEN", cooldown_sec=0) is False
    assert h.handle("POINT", cooldown_sec=0) is True
    assert started["n"] == 1

