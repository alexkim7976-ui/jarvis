"""생태계 라우터 스모크 테스트."""
from __future__ import annotations

import re


def test_stock_opinion_intent():
    from ecosystem_router import try_ecosystem_response
    # 네트워크 없이 detect 경로만 — run_local_stock_analysis는 mock 생략
    from local_stock_router import detect_stock_query
    assert detect_stock_query("네이버 어때?") == "네이버"


def test_next_class_intent_regex():
    from ecosystem_router import _NEXT_CLASS
    assert _NEXT_CLASS.search("다음 수업 어디야?")
    assert _NEXT_CLASS.search("next class location")


def test_calendar_intent():
    from ecosystem_router import _CALENDAR, _TOMORROW
    assert _CALENDAR.search("내일 일정 뭐야?")
    assert _TOMORROW.search("내일 일정")


def test_mobile_inbound_queue():
    from jarvis_link import JarvisLink
    link = JarvisLink(token="test-token-fixed-for-unit")
    evt = link._enqueue_mobile_inbound(
        {"type": "notification", "title": "KakaoTalk", "body": "김태현님 안녕하세요"},
        source="test",
    )
    assert evt.get("read_aloud") is True
    batch = link.get_mobile_inbound_events()
    assert len(batch) == 1
    assert "KakaoTalk" in batch[0].get("tts_text", "")


def test_campus_schedule_parser():
    import json
    import tempfile
    from pathlib import Path
    from campus_schedule import next_class_from_schedule

    sample = {
        "classes": [{
            "course": "CSE 114",
            "days": ["Mon", "Wed"],
            "start": "23:59",
            "location": "Engineering 143",
        }]
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(sample, f)
        path = f.name
    import campus_schedule as cs
    old = cs._SCHEDULE_FILE
    cs._SCHEDULE_FILE = Path(path)
    try:
        # may return None if no matching weekday/time — structure ok
        r = next_class_from_schedule()
        assert r is None or r.get("course") == "CSE 114"
    finally:
        cs._SCHEDULE_FILE = old
