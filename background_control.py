"""백그라운드 작업 켜기/끄기. 설정은 data/background_control.json 에 남는다."""
from __future__ import annotations

import json
import os
import re

import cv2

from interest_prefetch import (
    add_interest,
    data_dir,
    list_interests,
    remove_interest,
)
from korean_font import put_text

TASKS = (
    ("prefetch", "관심 미리받기"),
    ("news", "뉴스"),
    ("price", "지정가"),
    ("whale", "고래"),
    ("social", "소셜"),
    ("stock", "주가"),
    ("campus", "캠퍼스"),
    ("github", "깃허브"),
)

_LABELS = dict(TASKS)
_ALIASES = {
    "prefetch": ("관심 미리받기", "관심 미리", "미리받기", "프리페치", "prefetch"),
    "price": ("지정가", "가격 알림"),
    "news": ("뉴스",),
    "whale": ("고래",),
    "social": ("소셜", "레딧"),
    "stock": ("주가",),
    "campus": ("캠퍼스", "학교 메일"),
    "github": ("깃허브", "github", "플레이스토어"),
}
_runners: dict[str, dict] = {}

_PANEL = (15, 12, 8)
_CYAN = (255, 220, 0)
_DIM = (190, 160, 40)
_ON = (120, 220, 140)
_OFF = (140, 140, 150)


def bind_task(key: str, start, stop, running) -> None:
    _runners[key] = {"start": start, "stop": stop, "running": running}


def _path():
    return data_dir() / "background_control.json"


def _load() -> dict[str, bool]:
    state = {key: True for key, _label in TASKS}
    path = _path()
    if not path.is_file():
        return state
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return state
    if isinstance(saved, dict):
        for key in state:
            if key in saved:
                state[key] = bool(saved[key])
    return state


def _save(state: dict[str, bool]) -> None:
    path = _path()
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def is_enabled(key: str) -> bool:
    return bool(_load().get(key, True))


def is_running(key: str) -> bool:
    runner = _runners.get(key)
    if not runner:
        return False
    try:
        return bool(runner["running"]())
    except Exception:
        return False


def set_enabled(key: str, on: bool) -> str:
    state = _load()
    if key not in state:
        return "없는 백그라운드입니다."
    state[key] = bool(on)
    _save(state)
    label = _LABELS[key]
    runner = _runners.get(key)
    if runner:
        try:
            if on:
                if not runner["running"]():
                    runner["start"]()
            else:
                runner["stop"]()
        except Exception as exc:
            return f"{label} 설정은 저장했지만 적용에 실패했습니다. {exc}"
    if on and runner and not runner["running"]():
        return f"{label} 백그라운드를 켰습니다. 지금은 감시 대상이 없어 대기합니다."
    return f"{label} 백그라운드를 {'켰습니다' if on else '껐습니다'}."


def set_all(on: bool) -> str:
    return " ".join(set_enabled(key, on) for key, _label in TASKS)


def summary() -> str:
    parts = []
    for key, label in TASKS:
        if not is_enabled(key):
            state = "꺼짐"
        elif is_running(key):
            state = "실행 중"
        else:
            state = "켜짐"
        parts.append(f"{label} {state}")
    topics = ", ".join(list_interests()) or "없음"
    return "백그라운드. " + ". ".join(parts) + f". 관심: {topics}."


def status_rows() -> tuple[list[tuple[str, str]], str]:
    rows = []
    for key, label in TASKS:
        if not is_enabled(key):
            rows.append((label, "꺼짐"))
        elif is_running(key):
            rows.append((label, "실행 중"))
        else:
            rows.append((label, "켜짐"))
    return rows, ", ".join(list_interests()) or "없음"


def _clean_topic(text: str) -> str:
    text = re.sub(r"\s*(해\s*줘|해줘|해주세요|좀|요)\s*$", "", text or "")
    return text.strip(" .")


def _match_task(text: str) -> str | None:
    lowered = text.lower()
    hits = []
    for key, words in _ALIASES.items():
        for word in words:
            if word.lower() in lowered:
                hits.append((len(word), key))
    if not hits:
        return None
    hits.sort(reverse=True)
    return hits[0][1]


def interpret_control_command(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None

    added = re.search(r"관심\s*(?:주제\s*)?(?:추가|등록)\s+(.+)", raw)
    if added:
        return add_interest(_clean_topic(added.group(1)))
    removed = re.search(r"관심\s*(?:주제\s*)?(?:삭제|제거|빼)\s+(.+)", raw)
    if removed:
        return remove_interest(_clean_topic(removed.group(1)))
    if re.search(r"관심\s*(?:주제\s*)?(?:목록|보여|뭐)", raw) or raw in ("관심", "관심 목록"):
        topics = list_interests()
        listed = ", ".join(topics) if topics else "없습니다"
        return f"관심 주제는 {listed}. 추가하려면 관심 추가와 주제를 말하세요."

    if re.search(r"백그라운드\s*(?:전부|모두|전체)\s*(?:꺼|끄|중지|멈춰|정지)", raw):
        return set_all(False)
    if re.search(r"백그라운드\s*(?:전부|모두|전체)\s*(?:켜|시작)", raw):
        return set_all(True)
    if re.fullmatch(r"백그라운드(?:\s*(?:목록|상태|보여\s*줘|보여|뭐|확인))?", raw):
        return summary()

    if "백그라운드" not in raw:
        return None
    key = _match_task(raw)
    if not key:
        if re.search(r"(?:목록|상태|보여|뭐|확인)", raw):
            return summary()
        return None
    if re.search(r"(?:꺼|끄|중지|멈춰|정지)", raw):
        return set_enabled(key, False)
    if re.search(r"(?:켜|시작)", raw):
        return set_enabled(key, True)
    return summary()


def draw_background_panel(frame, visible: bool):
    h, w = frame.shape[:2]
    if not visible:
        put_text(frame, "B  백그라운드", (min(280, max(16, w - 220)), 36), size=14, color=_DIM)
        return frame

    rows, topics = status_rows()
    x, y = 270, 108
    if x + 280 > w - 16:
        x = 16
    pw = min(300, w - x - 12)
    ph = min(78 + 22 * len(rows) + 46, h - y - 80)
    ov = frame.copy()
    cv2.rectangle(ov, (x, y), (x + pw, y + ph), _PANEL, -1)
    cv2.addWeighted(ov, 0.86, frame, 0.14, 0, frame)
    cv2.rectangle(frame, (x, y), (x + pw, y + ph), _CYAN, 1, cv2.LINE_AA)
    put_text(frame, "백그라운드", (x + 14, y + 26), size=17, color=_CYAN, bold=True)
    line_y = y + 50
    for label, state in rows:
        color = _OFF if state == "꺼짐" else _ON
        put_text(frame, f"{label}   {state}", (x + 14, line_y), size=14, color=color)
        line_y += 22
    put_text(frame, f"관심  {topics}", (x + 14, line_y + 6), size=13, color=(230, 240, 245))
    put_text(frame, "말로  관심 추가 · 뉴스 백그라운드 꺼", (x + 14, line_y + 26), size=12, color=_DIM)
    return frame


BACKGROUND_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_background_tasks",
            "description": "백그라운드 작업의 켜짐/꺼짐과 관심 주제를 알려 줍니다.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_background_task",
            "description": "백그라운드 작업 하나를 켜거나 끕니다. task는 prefetch, news, price, whale, social, stock, campus, github 중 하나입니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "enabled": {"type": "boolean"},
                },
                "required": ["task", "enabled"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_interest_topic",
            "description": "미리 받아 둘 관심 주제를 추가합니다.",
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string"}},
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_interest_topic",
            "description": "관심 주제를 목록에서 뺍니다.",
            "parameters": {
                "type": "object",
                "properties": {"topic": {"type": "string"}},
                "required": ["topic"],
            },
        },
    },
]

BACKGROUND_TOOL_NAMES = {tool["function"]["name"] for tool in BACKGROUND_TOOLS}


def execute_background_tool(name: str, args: dict | None) -> dict:
    args = args or {}
    if name == "list_background_tasks":
        return {"success": True, "message": summary()}
    if name == "set_background_task":
        task = str(args.get("task") or "").strip()
        if task not in _LABELS:
            return {"success": False, "message": "없는 백그라운드입니다."}
        enabled = args.get("enabled")
        on = enabled if isinstance(enabled, bool) else str(enabled).strip().lower() in ("1", "true", "yes", "on")
        return {"success": True, "message": set_enabled(task, on)}
    if name == "add_interest_topic":
        return {"success": True, "message": add_interest(str(args.get("topic") or ""))}
    if name == "remove_interest_topic":
        return {"success": True, "message": remove_interest(str(args.get("topic") or ""))}
    return {"success": False, "message": f"알 수 없는 도구: {name}"}
