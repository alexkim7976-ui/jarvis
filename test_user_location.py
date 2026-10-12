"""사용자 도시 입력 — 시작 인사 흐름."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import weather_module as wm


@pytest.fixture
def loc_file(tmp_path, monkeypatch):
    p = tmp_path / "user_location.json"
    monkeypatch.setenv("JARVIS_USER_LOCATION_FILE", str(p))
    wm._USER_LOC_FILE = p
    yield p


def test_extract_city_from_text():
    assert wm.extract_city_from_text("부천이요") == "부천"
    assert wm.extract_city_from_text("지금 서울에 있어") == "서울"
    assert "Stony Brook" in wm.extract_city_from_text("Stony Brook")


def test_save_and_load_user_city(loc_file):
    wm.save_user_city("부천")
    assert wm.load_user_city() == "부천"
    data = json.loads(loc_file.read_text(encoding="utf-8"))
    assert data["city"] == "부천"


def test_build_auto_startup_briefing(monkeypatch):
    monkeypatch.setenv("OPENWEATHER_API_KEY", "")
    monkeypatch.setattr(wm, "resolve_auto_location", lambda **_: {
        "city": "Bucheon-si", "region": "Gyeonggi-do", "country": "South Korea", "offset": 32400,
    })
    msg = wm.build_auto_startup_briefing()
    assert "현재 위치는" in msg
    assert "Bucheon" in msg
