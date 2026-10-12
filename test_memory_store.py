"""memory_store — SQLite 대화·선호 영속."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import memory_store as ms


@pytest.fixture
def mem_db(tmp_path, monkeypatch):
    db = tmp_path / "test_mem.db"
    monkeypatch.setenv("JARVIS_MEMORY_DB", str(db))
    monkeypatch.setenv("JARVIS_MEMORY_ENABLED", "1")
    ms.close_db()
    yield db
    ms.close_db()


def test_save_and_load_exchange(mem_db):
    ms.init_db()
    ms.save_exchange("네이버 어때?", "네이버는 상승세입니다.")
    hist = ms.load_history(limit=10)
    assert len(hist) == 2
    assert hist[0]["role"] == "user"
    assert "네이버" in hist[0]["content"]
    assert hist[1]["role"] == "assistant"


def test_app_pref_and_context(mem_db):
    ms.record_app_open("Google Chrome")
    ms.record_app_open("Google Chrome")
    suffix = ms.get_memory_context_suffix()
    assert "Google Chrome" in suffix
    assert "2회" in suffix


def test_disabled_returns_empty(monkeypatch):
    monkeypatch.setenv("JARVIS_MEMORY_ENABLED", "0")
    ms.close_db()
    assert ms.load_history() == []
    assert ms.get_memory_context_suffix() == ""
