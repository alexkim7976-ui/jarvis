"""security_bootstrap 100-pass 감사 테스트."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest


def test_requests_patch_blocks_localhost():
    from security_policy import apply_environment_hardening
    from security_bootstrap import patch_requests_egress_guard

    with patch.dict(os.environ, {"JARVIS_SECURITY_LEVEL": "strict"}, clear=False):
        apply_environment_hardening()
        assert patch_requests_egress_guard()
        import requests
        with pytest.raises(PermissionError):
            requests.get("http://127.0.0.1:8080/secret", timeout=1)


def test_audit_100_passes_runs():
    from security_policy import apply_environment_hardening
    from security_bootstrap import run_security_audit_passes, patch_requests_egress_guard

    with patch.dict(os.environ, {"JARVIS_SECURITY_LEVEL": "maximum"}, clear=False):
        apply_environment_hardening()
        patch_requests_egress_guard()
        rep = run_security_audit_passes(10, auto_fix=True)
        assert rep["passes"] == 10
        assert "findings" in rep
        assert "ok" in rep


def test_data_providers_uses_http_wrapper():
    text = open("data_providers.py", encoding="utf-8").read()
    assert "requests.get(" not in text
    assert "requests.post(" not in text
    assert "_http_get" in text
