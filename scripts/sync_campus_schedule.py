#!/usr/bin/env python3
"""
Campus Quest 시간표 자동 동기화
──────────────────────────────
1) macOS Calendar.app 수업 이벤트 → data/campus_schedule.json
2) Google Calendar(campus 토큰) 학사 이벤트 병합
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = Path(os.getenv("CAMPUS_SCHEDULE_FILE", "data/campus_schedule.json"))
_DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_CLASS_HINT = re.compile(r"class|lecture|lab|recitation|cse|ams|bio|chem|mat|phy|수업|강의", re.I)
_SKIP_TITLE = re.compile(r"제헌절|공휴|holiday|birthday|생일|anniversary", re.I)


def _load_existing() -> dict:
    if OUT.exists():
        try:
            return json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"classes": []}


def _merge_class(store: dict, course: str, day: str, start: str, end: str, location: str):
    course = (course or "").strip()
    if not course:
        return
    key = course.lower()
    entry = store.get(key)
    if not entry:
        entry = store[key] = {
            "course": course,
            "days": [],
            "start": start or "09:00",
            "end": end or "",
            "location": location or "",
            "building": "",
        }
    if day and day not in entry["days"]:
        entry["days"].append(day)
    if location and not entry.get("location"):
        entry["location"] = location
    if start and entry.get("start") in ("", "09:00"):
        entry["start"] = start
    if end:
        entry["end"] = end


def sync_from_mac_calendar(store: dict) -> int:
    from mac_calendar import get_mac_upcoming
    r = get_mac_upcoming(hours=24 * 14, max_results=40)
    if not r.get("success"):
        return 0
    n = 0
    for ev in r.get("events") or []:
        title = ev.get("title", "")
        loc = (ev.get("location") or "").replace("missing value", "").strip()
        if _SKIP_TITLE.search(title):
            continue
        if not _CLASS_HINT.search(title) and not loc:
            continue
        t = ev.get("time", "")
        if t in ("00:00", "0:00") and not _CLASS_HINT.search(title):
            continue
        try:
            st = datetime.strptime(f"{ev['date']} {ev['time']}", "%Y-%m-%d %H:%M")
            day = _DAY_NAMES[st.weekday()]
        except (KeyError, ValueError):
            continue
        _merge_class(store, title, day, ev.get("time", ""), "", loc)
        n += 1
    return n


def sync_from_google(store: dict) -> int:
    from campus_agent import CampusAgent
    agent = CampusAgent()
    if not agent._ensure_services():
        return 0
    r = agent.get_academic_schedule(days=90, max_results=30)
    n = 0
    for ev in r.get("events") or []:
        title = ev.get("title", "")
        day_str = ev.get("date", "")
        if not title:
            continue
        try:
            st = datetime.strptime(day_str, "%Y-%m-%d")
            day = _DAY_NAMES[st.weekday()]
        except ValueError:
            day = "Mon"
        _merge_class(store, title, day, "09:00", "", "")
        n += 1
    return n


def main() -> int:
    store: dict[str, dict] = {}
    for c in _load_existing().get("classes") or []:
        course = c.get("course", "")
        if course:
            store[course.lower()] = dict(c)

    mac_n = sync_from_mac_calendar(store)
    g_n = sync_from_google(store)

    classes = [c for c in store.values() if not _SKIP_TITLE.search(c.get("course", ""))]
    for c in classes:
        c["days"] = sorted(set(c.get("days") or []), key=lambda d: _DAY_NAMES.index(d) if d in _DAY_NAMES else 99)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"classes": classes}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✓ {OUT} — {len(classes)}개 과목 (Mac +{mac_n}, Google +{g_n})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
