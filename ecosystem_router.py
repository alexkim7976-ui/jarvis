"""
J.A.R.V.I.S — 생태계 통합 라우터
────────────────────────────────
주식·캘린더·캠퍼스·모바일 알림을 자연어에서 직접 연결.
LLM function calling과 병행 — 고신뢰 의도는 fast-path로 즉시 실행.
"""
from __future__ import annotations

import os
import re
from typing import Any

_STOCK_OPINION = re.compile(
    r"어때|전망|어떄|괜찮|살\s*만|매수|매도|분석|알아\s*봐|브리핑|정보|조회|주가|시세|현재가",
    re.I,
)
_CALENDAR = re.compile(
    r"일정|수업\s*뭐|약속|캘린더|calendar|미리\s*알림|reminder|스케줄|schedule",
    re.I,
)
_NEXT_CLASS = re.compile(
    r"다음\s*수업|수업\s*어디|강의\s*어디|교실|강의실|몇\s*시\s*수업|where.*class|next\s*class",
    re.I,
)
_TOMORROW = re.compile(r"내일|tomorrow", re.I)
_TODAY = re.compile(r"오늘|today", re.I)


def _enabled() -> bool:
    v = os.getenv("JARVIS_ECOSYSTEM_ROUTER", "1").strip().lower()
    return v not in ("0", "false", "off", "no")


def try_ecosystem_response(text: str) -> dict[str, Any] | None:
    """고신뢰 생태계 의도 → 즉시 실행 결과. 없으면 None (LLM에 위임)."""
    if not _enabled():
        return None
    t = (text or "").strip()
    if not t:
        return None

    # 1) 다음 수업 (SBU / Campus Quest)
    if _NEXT_CLASS.search(t):
        try:
            from campus_schedule import next_class_from_schedule, format_next_class_briefing
            local = next_class_from_schedule()
            if local:
                msg = format_next_class_briefing(local)
                loc = local.get("location") or local.get("building") or "-"
                return {
                    "success": True, "domain": "campus", "briefing": msg,
                    "overlay_table": {
                        "type": "table",
                        "title": "🎓 다음 수업 (Campus Quest)",
                        "headers": ["과목", "장소", "시작", "남은 시간"],
                        "rows": [[local.get("course", "-"), loc, local.get("start_time", "-"),
                                  f"{local.get('starts_in_min', 0)}분"]],
                    },
                }
        except Exception:
            pass
        try:
            from campus_agent import execute_campus_tool
            r = execute_campus_tool("get_next_class_location", {})
            if r.get("success"):
                return {"success": True, "domain": "campus", "briefing": r.get("message"),
                        "overlay_table": r.get("overlay_table"), "raw": r}
        except Exception:
            pass

    # 2) 주식 의견 — 로컬 분석 파이프라인 (네이버 어때? 등)
    try:
        from local_stock_router import detect_stock_query, run_local_stock_analysis
        target = detect_stock_query(t)
        if not target and _STOCK_OPINION.search(t):
            from local_stock_router import _scan_known_name
            from stock_quant import resolve_alert_query
            target = _scan_known_name(t)
            if not target:
                r = resolve_alert_query(t)
                if r.get("confident"):
                    target = r.get("name") or r.get("ticker")
        if target and (_STOCK_OPINION.search(t) or detect_stock_query(t)):
            result = run_local_stock_analysis(target)
            if result.get("success"):
                result["domain"] = "stock"
                return result
    except Exception:
        pass

    # 3) 캘린더 / 미리알림
    if _CALENDAR.search(t):
        date = "내일" if _TOMORROW.search(t) else ("오늘" if _TODAY.search(t) else "오늘")
        if re.search(r"미리\s*알림|reminder", t, re.I):
            try:
                from mac_calendar import get_mac_reminders
                r = get_mac_reminders()
                if r.get("success"):
                    items = r.get("reminders") or []
                    lines = [f"- {x.get('title')}" for x in items[:5]]
                    briefing = r.get("message", "")
                    if lines:
                        briefing += " " + ", ".join(lines)
                    return {"success": True, "domain": "reminders", "briefing": briefing, "raw": r}
            except Exception:
                pass
        if re.search(r"다음|upcoming|곧", t, re.I):
            try:
                from mac_calendar import get_mac_upcoming
                r = get_mac_upcoming(hours=48)
                if r.get("success") and r.get("events"):
                    ev = r["events"][0]
                    briefing = f"다음 일정은 {ev.get('title')} ({ev.get('time')})"
                    if ev.get("location"):
                        briefing += f", 장소 {ev['location']}"
                    briefing += "입니다."
                    return {"success": True, "domain": "calendar", "briefing": briefing, "raw": r}
            except Exception:
                pass
        try:
            from mac_calendar import get_mac_events, mac_calendar_available
            if mac_calendar_available():
                r = get_mac_events(date=date)
                if r.get("success"):
                    evs = r.get("events") or []
                    if evs:
                        parts = [f"{e.get('start')} {e.get('title')}" for e in evs[:4]]
                        briefing = f"{date} 일정 {len(evs)}개입니다. " + ", ".join(parts) + "."
                    else:
                        briefing = r.get("message", f"{date} 일정이 없습니다.")
                    return {"success": True, "domain": "calendar", "briefing": briefing,
                            "overlay_table": _events_table(date, evs), "raw": r}
        except Exception:
            pass
        try:
            from calendar_module import get_events
            r = get_events(date=date)
            if r.get("success"):
                evs = r.get("events") or []
                briefing = r.get("message", "")
                if evs:
                    parts = [f"{e.get('start')} {e.get('title')}" for e in evs[:4]]
                    briefing = f"{date} 일정 {len(evs)}개. " + ", ".join(parts) + "."
                return {"success": True, "domain": "calendar", "briefing": briefing,
                        "overlay_table": _events_table(date, evs), "raw": r}
        except Exception:
            pass

    return None


def _events_table(date: str, events: list) -> dict:
    rows = [[e.get("start", ""), e.get("title", ""), e.get("location", "")] for e in events[:8]]
    if not rows:
        rows = [["-", "일정 없음", ""]]
    return {
        "type": "table",
        "title": f"📅 {date} 일정",
        "headers": ["시간", "일정", "장소"],
        "rows": rows,
    }
