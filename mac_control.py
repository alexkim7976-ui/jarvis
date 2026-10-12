"""
J.A.R.V.I.S — macOS 시스템 제어 (LLM function calling)
────────────────────────────────────────────────────────
볼륨·타이머·미디어 재생 등 OS 제어를 로컬 LLM 도구로 노출한다.
"""
from __future__ import annotations

import os
import sys
import subprocess
import threading
import time
from datetime import datetime
from typing import Any

MAC_CONTROL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": (
                "시스템 출력 볼륨을 조절합니다. "
                "'음악 좀 줄여', '볼륨 올려', '소리 끄기' 등에 사용. "
                "level(0~100) 또는 direction(up/down/mute) 중 하나를 지정."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {
                        "type": "integer",
                        "description": "절대 볼륨 0~100. direction과 동시 지정 시 level 우선.",
                    },
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down", "mute"],
                        "description": "상대 조절: up(+10), down(-10), mute(0)",
                    },
                    "delta": {
                        "type": "integer",
                        "description": "direction 사용 시 증감량(기본 10)",
                        "default": 10,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_volume",
            "description": "현재 시스템 출력 볼륨(0~100)을 조회합니다.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_timer",
            "description": (
                "N분 후 macOS 알림(및 선택적 음성)으로 타이머를 울립니다. "
                "'5분 타이머', '10분 뒤에 알려줘' 등에 사용."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "minutes": {"type": "number", "description": "타이머 분(소수 가능, 예: 1.5)"},
                    "label": {"type": "string", "description": "알림에 표시할 메시지", "default": "타이머"},
                },
                "required": ["minutes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "media_play_pause",
            "description": (
                "현재 재생 중인 미디어(Spotify 등)를 재생/일시정지 토글합니다. "
                "'음악 멈춰', '다시 재생해' 등에 사용."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

_active_timers: list[dict[str, Any]] = []


def _run_osascript(script: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or "osascript failed").strip()
        return True, (r.stdout or "").strip()
    except Exception as e:
        return False, str(e)


def get_volume() -> dict[str, Any]:
    if sys.platform != "darwin":
        return {"success": False, "message": "macOS 전용 기능입니다."}
    ok, out = _run_osascript("output volume of (get volume settings)")
    if not ok:
        return {"success": False, "message": f"볼륨 조회 실패: {out}"}
    try:
        level = int(out.split(",")[0].strip())
    except ValueError:
        return {"success": False, "message": f"볼륨 파싱 실패: {out}"}
    return {"success": True, "level": max(0, min(100, level)), "message": f"현재 볼륨 {level}%입니다."}


def set_volume(level: int | None = None, direction: str | None = None, delta: int = 10) -> dict[str, Any]:
    if sys.platform != "darwin":
        return {"success": False, "message": "macOS 전용 기능입니다."}

    delta = max(1, min(50, int(delta or 10)))

    if level is not None:
        lv = max(0, min(100, int(level)))
        ok, err = _run_osascript(f"set volume output volume {lv}")
        if not ok:
            return {"success": False, "message": f"볼륨 설정 실패: {err}"}
        return {"success": True, "level": lv, "message": f"볼륨을 {lv}%로 설정했습니다."}

    d = (direction or "").lower().strip()
    if d == "mute":
        ok, err = _run_osascript("set volume output volume 0")
        if not ok:
            return {"success": False, "message": f"음소거 실패: {err}"}
        return {"success": True, "level": 0, "message": "볼륨을 음소거했습니다."}

    cur = get_volume()
    if not cur.get("success"):
        return cur
    now = int(cur["level"])

    if d == "up":
        nv = min(100, now + delta)
    elif d == "down":
        nv = max(0, now - delta)
    else:
        return {"success": False, "message": "level 또는 direction(up/down/mute)을 지정해 주세요."}

    ok, err = _run_osascript(f"set volume output volume {nv}")
    if not ok:
        return {"success": False, "message": f"볼륨 조절 실패: {err}"}
    verb = "올렸" if d == "up" else "내렸"
    return {"success": True, "level": nv, "message": f"볼륨을 {verb}습니다. 현재 {nv}%입니다."}


def set_timer(minutes: float, label: str = "타이머") -> dict[str, Any]:
    try:
        mins = float(minutes)
    except (TypeError, ValueError):
        return {"success": False, "message": "minutes는 숫자여야 합니다."}
    if mins <= 0 or mins > 24 * 60:
        return {"success": False, "message": "타이머는 0분 초과 24시간 이하여야 합니다."}

    seconds = max(1, int(mins * 60))
    safe_label = (label or "타이머").replace('"', "'")[:120]
    timer_id = f"timer_{int(time.time())}_{len(_active_timers)}"
    fire_at = datetime.now().strftime("%H:%M")

    def _fire():
        if sys.platform == "darwin":
            _run_osascript(
                f'display notification "{safe_label}" with title "JARVIS 타이머" sound name "Glass"'
            )
        for t in _active_timers:
            if t.get("id") == timer_id:
                t["fired"] = True
                break

    threading.Timer(seconds, _fire).start()
    _active_timers.append({
        "id": timer_id,
        "minutes": mins,
        "label": safe_label,
        "scheduled_at": fire_at,
        "fired": False,
    })
    if len(_active_timers) > 20:
        _active_timers.pop(0)

    return {
        "success": True,
        "timer_id": timer_id,
        "minutes": mins,
        "message": f"{mins:g}분 타이머를 설정했습니다. ({safe_label})",
    }


def media_play_pause() -> dict[str, Any]:
    if sys.platform != "darwin":
        return {"success": False, "message": "macOS 전용 기능입니다."}
    # Spotify 우선, 없으면 시스템 미디어 키
    ok, err = _run_osascript(
        'try\n'
        '  tell application "Spotify" to playpause\n'
        '  return "spotify"\n'
        'on error\n'
        '  tell application "System Events" to key code 49\n'
        '  return "system"\n'
        'end try'
    )
    if not ok:
        return {"success": False, "message": f"재생 제어 실패: {err}"}
    return {"success": True, "message": "미디어 재생을 전환했습니다."}


def execute_mac_control_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    args = arguments or {}
    if name == "get_volume":
        return get_volume()
    if name == "set_volume":
        return set_volume(
            level=args.get("level"),
            direction=args.get("direction"),
            delta=args.get("delta", 10),
        )
    if name == "set_timer":
        return set_timer(minutes=args.get("minutes", 0), label=args.get("label", "타이머"))
    if name == "media_play_pause":
        return media_play_pause()
    return {"success": False, "message": f"Unknown mac control tool: {name}"}
