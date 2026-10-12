"""mac_control + file context + LLM brain gate smoke tests."""
from __future__ import annotations

import os


def test_mac_control_volume_roundtrip(monkeypatch):
    monkeypatch.setattr("mac_control.sys.platform", "darwin")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        class R:
            returncode = 0
            stdout = "50"
            stderr = ""
        return R()

    monkeypatch.setattr("mac_control.subprocess.run", fake_run)
    from mac_control import get_volume, set_volume

    r = get_volume()
    assert r["success"] and r["level"] == 50

    r2 = set_volume(direction="down", delta=10)
    assert r2["success"]
    assert any("set volume" in str(c) for c in calls)


def test_file_context_suffix():
    from file_manager import register_recent_file, get_file_context_suffix

    register_recent_file("/tmp/report.pdf", "report.pdf", "opened")
    suffix = get_file_context_suffix()
    assert "RECENT FILES" in suffix
    assert "report.pdf" in suffix


def test_llm_brain_disables_stock_bypass(monkeypatch):
    monkeypatch.setenv("JARVIS_LLM_BRAIN", "1")
    monkeypatch.setenv("JARVIS_NO_GEMINI", "1")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")

    import importlib
    import jarvis_v2
    importlib.reload(jarvis_v2)

    assert jarvis_v2._llm_brain_enabled() is True
    assert jarvis_v2._try_mac_local_stock("키움증권 분석해줘") is None
