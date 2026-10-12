"""power_manager — 유휴 절전·PID·앱 런처 연동 테스트."""
from __future__ import annotations

import os
import time
from pathlib import Path

import power_manager as pm


def test_auto_quit_idle_seconds(monkeypatch):
    monkeypatch.setenv("JARVIS_AUTO_QUIT_IDLE_SEC", "120")
    assert pm.auto_quit_idle_seconds() == 120
    monkeypatch.setenv("JARVIS_AUTO_QUIT_IDLE_SEC", "0")
    assert pm.auto_quit_idle_seconds() == 0


def test_pid_roundtrip(tmp_path, monkeypatch):
    pidfile = tmp_path / ".jarvis.pid"
    wakefile = tmp_path / ".jarvis.wake"
    monkeypatch.setattr(pm, "PID_FILE", pidfile)
    monkeypatch.setattr(pm, "WAKE_FLAG", wakefile)
    pm.write_pid(4242)
    assert pm.read_pid() == 4242
    pm.clear_pid()
    assert pm.read_pid() is None


def test_idle_tracker(monkeypatch):
    monkeypatch.setenv("JARVIS_AUTO_QUIT_IDLE_SEC", "10")
    t = pm.IdleTracker()
    assert not t.should_auto_quit()
    t._last = time.time() - 11
    assert t.should_auto_quit()


def test_wake_flag(monkeypatch, tmp_path):
    wakefile = tmp_path / ".jarvis.wake"
    monkeypatch.setattr(pm, "WAKE_FLAG", wakefile)
    assert not pm.consume_wake_request()
    pm.request_wake()
    assert pm.consume_wake_request()
    assert not pm.consume_wake_request()
