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
    assert campus_mail_account() == ""


def test_message_for_campus_account_match(monkeypatch):
    monkeypatch.setenv("CAMPUS_GMAIL_ACCOUNT", "student@example.edu")
    import importlib
    import campus_agent as ca
    importlib.reload(ca)

    meta = {
        "to": "Student <student@example.edu>",
        "cc": "",
        "delivered_to": "",
    }
    assert ca.message_for_campus_account(meta) is True


def test_message_for_campus_account_rejects_other(monkeypatch):
    monkeypatch.setenv("CAMPUS_GMAIL_ACCOUNT", "student@example.edu")
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
    monkeypatch.setenv("CAMPUS_GMAIL_ACCOUNT", "student@example.edu")
    import importlib
    import campus_agent as ca
    importlib.reload(ca)

    agent = ca.CampusAgent()
    q = agent._build_query()
    assert "student@example.edu" in q
    assert "deliveredto:" in q or "to:" in q
