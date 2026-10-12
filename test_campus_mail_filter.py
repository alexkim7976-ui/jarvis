"""campus_agent — SBU 수신 주소 필터."""
from __future__ import annotations

import os

import pytest

from campus_agent import (
    CampusAgent,
    campus_mail_account,
    message_for_campus_account,
)


def test_default_mail_account():
    os.environ.pop("CAMPUS_GMAIL_ACCOUNT", None)
    # 모듈 상수는 import 시점 값 — env override 테스트는 monkeypatch로
    assert campus_mail_account()  # 기본값 존재


def test_message_for_campus_account_match(monkeypatch):
    monkeypatch.setenv("CAMPUS_GMAIL_ACCOUNT", "taehyeon.kim.2@stonybrook.org")
    import importlib
    import campus_agent as ca
    importlib.reload(ca)

    meta = {
        "to": "Kim Taehyeon <taehyeon.kim.2@stonybrook.org>",
        "cc": "",
        "delivered_to": "",
    }
    assert ca.message_for_campus_account(meta) is True


def test_message_for_campus_account_rejects_other(monkeypatch):
    monkeypatch.setenv("CAMPUS_GMAIL_ACCOUNT", "taehyeon.kim.2@stonybrook.org")
    import importlib
    import campus_agent as ca
    importlib.reload(ca)

    meta = {
        "to": "other@gmail.com",
        "cc": "friend@example.com",
        "delivered_to": "other@gmail.com",
    }
    assert ca.message_for_campus_account(meta) is False


def test_build_query_includes_recipient(monkeypatch):
    monkeypatch.setenv("CAMPUS_GMAIL_ACCOUNT", "taehyeon.kim.2@stonybrook.org")
    import importlib
    import campus_agent as ca
    importlib.reload(ca)

    agent = ca.CampusAgent()
    q = agent._build_query()
    assert "taehyeon.kim.2@stonybrook.org" in q
    assert "deliveredto:" in q or "to:" in q
