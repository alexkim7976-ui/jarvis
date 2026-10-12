"""
Campus Quest / SBU 수업 시간표 — 로컬 JSON + Google Calendar 폴백
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

_SCHEDULE_FILE = Path(os.getenv("CAMPUS_SCHEDULE_FILE", "data/campus_schedule.json"))
_DAY_MAP = {
    "mon": 0, "monday": 0, "월": 0,
    "tue": 1, "tuesday": 1, "화": 1,
    "wed": 2, "wednesday": 2, "수": 2,
    "thu": 3, "thursday": 3, "목": 3,
    "fri": 4, "friday": 4, "금": 4,
    "sat": 5, "saturday": 5, "토": 5,
    "sun": 6, "sunday": 6, "일": 6,
}

_CLASS_RE = re.compile(
    r"class|lecture|lab|recitation|seminar|수업|강의|course|cse|bio|chem|mat|phy|ams",
    re.I,
)


def _load_schedule() -> list[dict]:
    if not _SCHEDULE_FILE.exists():
        return []
    try:
        data = json.loads(_SCHEDULE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return list(data.get("classes") or [])
        if isinstance(data, list):
            return list(data)
        return []
    except Exception:
        return []


def _parse_time(s: str) -> tuple[int, int] | None:
    s = (s or "").strip().replace("시", ":").replace("분", "")
    if ":" not in s:
        return None
    try:
        h, m = s.split(":", 1)
        return int(h), int(m)
    except ValueError:
        return None


def next_class_from_schedule(now: datetime | None = None) -> dict | None:
    """로컬 Campus Quest JSON에서 다음 수업 찾기."""
    classes = _load_schedule()
    if not classes:
        return None
    now = now or datetime.now()
    best = None
    best_delta = None

    for c in classes:
        days = c.get("days") or []
        day_nums = {_DAY_MAP.get(str(d).lower(), -1) for d in days}
        day_nums.discard(-1)
        if not day_nums:
            continue
        start = _parse_time(c.get("start", ""))
        if not start:
            continue
        sh, sm = start
        for offset in range(8):
            cand = now + timedelta(days=offset)
            if cand.weekday() not in day_nums:
                continue
            st = cand.replace(hour=sh, minute=sm, second=0, microsecond=0)
            if st <= now:
                continue
            delta = (st - now).total_seconds()
            if best_delta is None or delta < best_delta:
                best_delta = delta
                end = _parse_time(c.get("end", ""))
                best = {
                    "course": c.get("course") or c.get("title") or "수업",
                    "location": c.get("location") or c.get("room") or "",
                    "building": c.get("building") or "",
                    "start_time": st.strftime("%H:%M"),
                    "start_date": st.strftime("%Y-%m-%d"),
                    "starts_in_min": int(delta / 60),
                    "source": "campus_schedule.json",
                }
                if end:
                    best["end_time"] = f"{end[0]:02d}:{end[1]:02d}"
    return best


def format_next_class_briefing(info: dict) -> str:
    course = info.get("course", "수업")
    loc = info.get("location") or info.get("building") or "장소 미등록"
    mins = info.get("starts_in_min", 0)
    when = info.get("start_time", "")
    if mins <= 0:
        time_phrase = "곧 시작합니다"
    elif mins < 60:
        time_phrase = f"{mins}분 후({when})"
    else:
        time_phrase = f"{mins // 60}시간 {mins % 60}분 후({when})"
    return f"다음 수업은 {course}이고, {loc}에서 {time_phrase} 시작합니다."


def is_class_like_event(title: str, location: str = "") -> bool:
    t = f"{title} {location}"
    return bool(_CLASS_RE.search(t)) or bool(location.strip())
