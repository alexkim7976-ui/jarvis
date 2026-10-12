"""wake_engine — fuzzy 한국어 웨이크워드 + openWakeWord 설정."""
from __future__ import annotations

import os

import pytest

from wake_engine import (
    default_wake_words,
    matches_wake_word,
    normalize_wake_text,
    oww_enabled,
    wake_engine_mode,
)


def test_normalize_wake_text():
    assert normalize_wake_text("  자비스야!  ") == "자비스야"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("자비스야", True),
        ("자비스 아", True),
        ("hey jarvis", True),
        ("Jarvis wake up", True),
        ("안녕하세요", False),
        ("", False),
    ],
)
def test_matches_wake_word(text, expected):
    assert matches_wake_word(text, default_wake_words()) is expected


def test_wake_engine_mode_hybrid_default():
    os.environ.pop("JARVIS_WAKE_ENGINE", None)
    assert wake_engine_mode() == "hybrid"
    assert oww_enabled() is True


def test_wake_engine_whisper_only():
    os.environ["JARVIS_WAKE_ENGINE"] = "whisper"
    assert oww_enabled() is False
