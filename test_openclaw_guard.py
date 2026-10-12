"""OpenClaw 제로트러스트 가드 테스트."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest


def test_localhost_only_blocks_remote():
    from openclaw_guard import _localhost_only

    ok, _ = _localhost_only("http://127.0.0.1:18789/health")
    assert ok
    ok2, reason = _localhost_only("http://192.168.1.5:18789/health")
    assert not ok2
    assert "localhost" in reason.lower() or "127" in reason


def test_blocked_dangerous_paths():
    from openclaw_guard import _path_allowed

    assert not _path_allowed("/api/v1/exec.approvals.set")[0]
    assert not _path_allowed("/api/v1/config.patch")[0]
    assert _path_allowed("/health")[0]
    assert _path_allowed("/api/v1/chat/completions")[0]


def test_openclaw_enabled_maximum():
    from openclaw_guard import openclaw_enabled

    with patch.dict(os.environ, {
        "JARVIS_SECURITY_LEVEL": "maximum",
        "OPENCLAW_GUARD_STRICT": "1",
    }, clear=False):
        assert openclaw_enabled()


def test_guarded_request_blocks_bad_path():
    from openclaw_guard import guarded_request

    with patch.dict(os.environ, {"OPENCLAW_ENABLED": "1"}, clear=False):
        ok, msg = guarded_request("POST", "/api/v1/config.patch", json_body={})
        assert not ok
        assert "차단" in str(msg) or "허용" in str(msg)
