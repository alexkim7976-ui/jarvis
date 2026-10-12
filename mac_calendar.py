"""
J.A.R.V.I.S — macOS 네이티브 Calendar / Reminders (AppleScript)
────────────────────────────────────────────────────────────────
Google OAuth 없이 Calendar.app · Reminders.app 연동.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta


def _run_applescript(script: str, timeout: float = 20) -> tuple[bool, str]:
    if sys.platform != "darwin":
        return False, "macOS 전용"
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or "osascript failed").strip()
        return True, (r.stdout or "").strip()
    except Exception as e:
        return False, str(e)


def _parse_date_hint(date: str) -> tuple[str, str]:
    """AppleScript date range — locale-independent (current date + offset)."""
    dl = (date or "오늘").lower().strip()
    if dl in ("내일", "tomorrow"):
        return ("1", "2")   # day offset start, day offset end (exclusive)
    if dl in ("모레",):
        return ("2", "3")
    if dl in ("이번주", "this week"):
        return ("0", "7")
    return ("0", "1")  # 오늘


def _parse_event_lines(out: str, include_end: bool = True) -> list[dict]:
    events = []
    for chunk in (out or "").split("\n"):
        line = chunk.strip()
        if not line or line == "missing value" or "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue
        day, ss, title = parts[0], parts[1], parts[2]
        if include_end and len(parts) >= 5:
            es, loc = parts[3], parts[4]
        else:
            es, loc = "", parts[3] if len(parts) > 3 else ""
        events.append({
            "title": title,
            "start": ss,
            "end": es,
            "location": loc,
            "date": day,
        })
    events.sort(key=lambda x: (x.get("date", ""), x.get("start", "")))
    return events


_APPLESCRIPT_HELPERS = '''
on pad(n)
    if n < 10 then return "0" & n
    return n as text
end pad

on fmtDay(d)
    return (year of d as text) & "-" & my pad(month of d as integer) & "-" & my pad(day of d)
end fmtDay

on fmtTime(d)
    return my pad(hours of d) & ":" & my pad(minutes of d)
end fmtTime
'''


def get_mac_events(date: str = "오늘", max_results: int = 10) -> dict:
    d0, d1 = _parse_date_hint(date)
    script = _APPLESCRIPT_HELPERS + f'''
set outText to ""
set tStart to current date
set time of tStart to 0
set tStart to tStart + ({d0} * days)
set tEnd to tStart + ({int(d1) - int(d0)} * days)
tell application "Calendar"
    repeat with cal in calendars
        try
            set evts to (every event of cal whose start date ≥ tStart and start date < tEnd)
            repeat with e in evts
                set s to start date of e
                set en to end date of e
                set t to summary of e
                set loc to ""
                try
                    set loc to location of e
                end try
                set outText to outText & my fmtDay(s) & "|" & my fmtTime(s) & "|" & t & "|" & loc & linefeed
            end repeat
        end try
    end repeat
end tell
return outText
'''
    ok, out = _run_applescript(script, timeout=25)
    if not ok:
        return {"success": False, "message": f"캘린더 조회 실패: {out}"}

    events = _parse_event_lines(out, include_end=False)[: int(max_results)]
    msg = f"{date} 일정 {len(events)}개입니다." if events else f"{date}에 일정이 없습니다."
    return {
        "success": True, "date": date, "count": len(events), "events": events,
        "message": msg, "backend": "mac_calendar",
    }


def get_mac_upcoming(hours: int = 24, max_results: int = 5) -> dict:
    script = _APPLESCRIPT_HELPERS + f'''
set outText to ""
set tNow to current date
set tEnd to tNow + ({int(hours)} * hours)
tell application "Calendar"
    repeat with cal in calendars
        try
            set evts to (every event of cal whose start date ≥ tNow and start date ≤ tEnd)
            repeat with e in evts
                set s to start date of e
                set t to summary of e
                set loc to ""
                try
                    set loc to location of e
                end try
                set outText to outText & my fmtDay(s) & "|" & my fmtTime(s) & "|" & t & "|" & loc & linefeed
            end repeat
        end try
    end repeat
end tell
return outText
'''
    ok, out = _run_applescript(script, timeout=25)
    if not ok:
        return {"success": False, "message": f"다가오는 일정 조회 실패: {out}"}

    now = datetime.now()
    events = []
    for e in _parse_event_lines(out, include_end=False):
        try:
            st = datetime.strptime(f"{e['date']} {e['start']}", "%Y-%m-%d %H:%M")
            rem_min = int((st - now).total_seconds() / 60)
            e["remaining"] = f"{rem_min}분 후" if rem_min < 60 else f"{rem_min // 60}시간 후"
            e["time"] = e["start"]
        except ValueError:
            e["remaining"] = ""
            e["time"] = e.get("start", "")
        events.append(e)
    events = events[: int(max_results)]
    return {
        "success": True,
        "count": len(events),
        "events": events,
        "message": f"다가오는 일정 {len(events)}개입니다." if events else "다가오는 일정이 없습니다.",
        "backend": "mac_calendar",
    }


def get_mac_reminders(max_results: int = 8) -> dict:
    script = '''
set outText to ""
tell application "Reminders"
    repeat with lst in lists
        try
            set rs to (every reminder of lst whose completed is false)
            repeat with r in rs
                set n to name of r
                set b to ""
                try
                    set b to body of r
                end try
                set outText to outText & n & "|" & b & linefeed
            end repeat
        end try
    end repeat
end tell
return outText
'''
    ok, out = _run_applescript(script, timeout=15)
    if not ok:
        return {"success": False, "message": f"미리알림 조회 실패: {out}"}

    items = []
    for line in (out or "").splitlines():
        line = line.strip()
        if not line:
            continue
        title, _, body = line.partition("|")
        items.append({"title": title, "body": body})
    items = items[: int(max_results)]
    return {
        "success": True,
        "count": len(items),
        "reminders": items,
        "message": f"미완료 미리알림 {len(items)}개입니다." if items else "미완료 미리알림이 없습니다.",
        "backend": "mac_reminders",
    }


def mac_calendar_available() -> bool:
    if sys.platform != "darwin":
        return False
    ok, out = _run_applescript('return application "Calendar" exists', timeout=5)
    return ok and out.lower() == "true"
