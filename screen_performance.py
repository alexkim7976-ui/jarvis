"""HUD 성능 프로필 패널 — P키, 1/2/3 선택."""
from __future__ import annotations

import cv2

from korean_font import put_text
from performance_presets import (
    PRESET_BALANCED,
    PRESET_ECO,
    PRESET_LABELS,
    PRESET_QUALITY,
    apply_preset,
    current_profile,
)

_PANEL = (15, 12, 8)
_CYAN = (255, 220, 0)
_DIM = (190, 160, 40)
_ACTIVE = (120, 220, 140)
_IDLE = (180, 190, 200)

_ORDER = (PRESET_ECO, PRESET_BALANCED, PRESET_QUALITY)


def draw_performance_panel(frame, visible: bool):
    h, w = frame.shape[:2]
    if not visible:
        put_text(frame, "P  Perf", (min(380, max(16, w - 200)), 36), size=14, color=_DIM)
        return frame

    cur = current_profile()
    x, y = 270, 108
    if x + 300 > w - 16:
        x = 16
    pw = min(320, w - x - 12)
    ph = min(200, h - y - 80)
    ov = frame.copy()
    cv2.rectangle(ov, (x, y), (x + pw, y + ph), _PANEL, -1)
    cv2.addWeighted(ov, 0.86, frame, 0.14, 0, frame)
    cv2.rectangle(frame, (x, y), (x + pw, y + ph), _CYAN, 1, cv2.LINE_AA)
    put_text(frame, "Performance Profile", (x + 14, y + 26), size=17, color=_CYAN, bold=True)
    ly = y + 52
    for i, pid in enumerate(_ORDER, start=1):
        label = PRESET_LABELS[pid]
        mark = "●" if pid == cur else "○"
        col = _ACTIVE if pid == cur else _IDLE
        put_text(frame, f"{i}  {mark} {label}", (x + 14, ly), size=15, color=col)
        ly += 26
    put_text(frame, "1 Eco   2 Balanced   3 High Quality", (x + 14, ly + 4), size=12, color=_DIM)
    put_text(frame, "Restart JARVIS to fully apply cam/STT", (x + 14, ly + 24), size=11, color=_DIM)
    return frame


def handle_performance_key(key: int, visible: bool) -> str | None:
    """패널이 열려 있을 때 1/2/3 — 적용 메시지 또는 None."""
    if not visible:
        return None
    mapping = {
        ord("1"): PRESET_ECO,
        ord("2"): PRESET_BALANCED,
        ord("3"): PRESET_QUALITY,
    }
    pid = mapping.get(key)
    if not pid:
        return None
    return apply_preset(pid)
