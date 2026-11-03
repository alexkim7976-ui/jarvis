"""HUD — Apple-style liquid glass quick icons (OpenCV)."""
from __future__ import annotations

import cv2
import numpy as np

# BGR
_GLASS_TINT = (248, 244, 240)
_GLASS_EDGE = (255, 255, 255)
_DIVIDER = (200, 210, 225)
_ICON = {
    "chat": (220, 70, 210),       # magenta
    "calendar": (240, 130, 40),   # blue
    "location": (90, 200, 75),    # green
    "gear": (155, 155, 155),      # gray
}
_ACTIVE_GLOW = (255, 255, 255)


def _round_mask(w: int, h: int, r: int) -> np.ndarray:
    r = max(2, min(r, min(w, h) // 2))
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.rectangle(mask, (r, 0), (w - r, h), 255, -1)
    cv2.rectangle(mask, (0, r), (w, r), 255, -1)
    for cx, cy in ((r, r), (w - r - 1, r), (r, h - r - 1), (w - r - 1, h - r - 1)):
        cv2.circle(mask, (cx, cy), r, 255, -1)
    return mask


def _paste_glass_pill(frame, x: int, y: int, w: int, h: int) -> None:
    fh, fw = frame.shape[:2]
    if x < 0 or y < 0 or x + w > fw or y + h > fh:
        return
    roi = frame[y : y + h, x : x + w]
    blur = cv2.GaussianBlur(roi, (0, 0), sigmaX=10, sigmaY=10)
    tint = np.full_like(blur, _GLASS_TINT)
    glass = cv2.addWeighted(blur, 0.58, tint, 0.38, 4)
    mask = _round_mask(w, h, h // 2)
    for c in range(3):
        roi[:, :, c] = np.where(mask, glass[:, :, c], roi[:, :, c])
    edge = cv2.Canny(mask, 40, 120)
    edge = cv2.dilate(edge, np.ones((2, 2), np.uint8), iterations=1)
    frame[y : y + h, x : x + w][edge > 0] = _GLASS_EDGE


def _cell_highlight(frame, x: int, y: int, cw: int, h: int, on: bool) -> None:
    if not on:
        return
    pad = 6
    cx, cy = x + pad, y + pad
    cw, ch = cw - pad * 2, h - pad * 2
    ov = frame.copy()
    cv2.ellipse(ov, (x + cw // 2 + pad, y + h // 2), (cw // 2, ch // 2), 0, 0, 360, _ACTIVE_GLOW, -1)
    cv2.addWeighted(ov, 0.14, frame, 0.86, 0, frame)


def _draw_icon_chat(frame, cx: int, cy: int, size: int, color: tuple[int, int, int]) -> None:
    s = size
    x0, y0 = cx - s // 2, cy - s // 2 + 2
    bh = int(s * 0.72)
    cv2.ellipse(frame, (x0 + 8, y0 + 8), (8, 8), 0, 0, 360, color, 2, cv2.LINE_AA)
    cv2.ellipse(frame, (x0 + s - 8, y0 + 8), (8, 8), 0, 0, 360, color, 2, cv2.LINE_AA)
    cv2.line(frame, (x0 + 8, y0), (x0 + s - 8, y0), color, 2, cv2.LINE_AA)
    cv2.line(frame, (x0 + 8, y0 + bh), (x0 + s - 8, y0 + bh), color, 2, cv2.LINE_AA)
    cv2.line(frame, (x0, y0 + 8), (x0, y0 + bh - 8), color, 2, cv2.LINE_AA)
    cv2.line(frame, (x0 + s, y0 + 8), (x0 + s, y0 + bh - 8), color, 2, cv2.LINE_AA)
    tail_y = y0 + bh
    tri = np.array(
        [[x0 + s // 3, tail_y], [x0 + s // 3 + 8, tail_y + 10], [x0 + s // 3 + 14, tail_y]],
        np.int32,
    )
    cv2.polylines(frame, [tri], False, color, 2, cv2.LINE_AA)


def _draw_icon_calendar(frame, cx: int, cy: int, size: int, color: tuple[int, int, int]) -> None:
    s = size
    x0, y0 = cx - s // 2, cy - s // 2
    cv2.rectangle(frame, (x0, y0 + 6), (x0 + s, y0 + s), color, 2, cv2.LINE_AA)
    cv2.line(frame, (x0, y0 + 16), (x0 + s, y0 + 16), color, 2, cv2.LINE_AA)
    for dx in (s // 3, 2 * s // 3):
        cv2.line(frame, (x0 + dx, y0 + 6), (x0 + dx, y0), color, 2, cv2.LINE_AA)
    for row in range(2):
        for col in range(3):
            px = x0 + 6 + col * (s // 3)
            py = y0 + 22 + row * (s // 4)
            cv2.rectangle(frame, (px, py), (px + 5, py + 4), color, 1, cv2.LINE_AA)


def _draw_icon_pin(frame, cx: int, cy: int, size: int, color: tuple[int, int, int]) -> None:
    s = size
    cv2.circle(frame, (cx, cy - 2), s // 3, color, -1, cv2.LINE_AA)
    tri = np.array([[cx, cy + s // 2], [cx - s // 3, cy - 2], [cx + s // 3, cy - 2]], np.int32)
    cv2.fillConvexPoly(frame, tri, color, cv2.LINE_AA)
    cv2.circle(frame, (cx, cy - 4), max(2, s // 8), (255, 255, 255), -1, cv2.LINE_AA)


def _draw_icon_gear(frame, cx: int, cy: int, size: int, color: tuple[int, int, int]) -> None:
    s = size
    r_outer = s // 2
    r_inner = max(3, s // 5)
    for i in range(8):
        ang = i * (360 / 8)
        rad = np.deg2rad(ang)
        x1 = int(cx + r_outer * 0.55 * np.cos(rad))
        y1 = int(cy + r_outer * 0.55 * np.sin(rad))
        x2 = int(cx + r_outer * np.cos(rad))
        y2 = int(cy + r_outer * np.sin(rad))
        cv2.line(frame, (x1, y1), (x2, y2), color, max(2, s // 7), cv2.LINE_AA)
    cv2.circle(frame, (cx, cy), r_inner, color, -1, cv2.LINE_AA)
    cv2.circle(frame, (cx, cy), max(2, r_inner // 2), (255, 255, 255), -1, cv2.LINE_AA)


def _draw_pill_row(
    frame,
    x: int,
    y: int,
    w: int,
    h: int,
    left_on: bool,
    right_on: bool,
    left_icon: str,
    right_icon: str,
) -> None:
    _paste_glass_pill(frame, x, y, w, h)
    cw = w // 2
    _cell_highlight(frame, x, y, cw, h, left_on)
    _cell_highlight(frame, x + cw, y, cw, h, right_on)
    cv2.line(frame, (x + cw, y + 8), (x + cw, y + h - 8), _DIVIDER, 1, cv2.LINE_AA)
    icons = {
        "chat": _draw_icon_chat,
        "calendar": _draw_icon_calendar,
        "location": _draw_icon_pin,
        "gear": _draw_icon_gear,
    }
    lc = _ICON[left_icon]
    rc = _ICON[right_icon]
    if left_on:
        lc = tuple(min(255, c + 25) for c in lc)
    if right_on:
        rc = tuple(min(255, c + 25) for c in rc)
    icons[left_icon](frame, x + cw // 2, y + h // 2, 22, lc)
    icons[right_icon](frame, x + cw + cw // 2, y + h // 2, 22, rc)


def draw_glass_quick_dock(
    frame,
    *,
    chat_open: bool = False,
    calendar_open: bool = False,
    settings_open: bool = False,
    location_active: bool = False,
) -> np.ndarray:
    """
    우측 상단 2×2 liquid glass 도크.
    chat=⌨/Enter · calendar=B 백그라운드 · location=위치·날씨 · gear=P/H 설정
    """
    h, w = frame.shape[:2]
    pill_w, pill_h, gap = 168, 42, 8
    x = w - pill_w - 14
    y0 = 54
    if x < 160:
        x = max(8, w - pill_w - 8)
    _draw_pill_row(frame, x, y0, pill_w, pill_h, chat_open, calendar_open, "chat", "calendar")
    _draw_pill_row(
        frame, x, y0 + pill_h + gap, pill_w, pill_h,
        location_active, settings_open, "location", "gear",
    )
    return frame


def draw_glass_power_chip(frame, active: bool) -> np.ndarray:
    """좌측 Z 절전 — 작은 glass pill."""
    from korean_font import put_text

    x, y, bw, bh = 14, 52, 96, 36
    _paste_glass_pill(frame, x, y, bw, bh)
    col = (100, 230, 140) if active else (200, 210, 220)
    cx, cy = x + 18, y + bh // 2
    cv2.circle(frame, (cx, cy), 7, col, 2, cv2.LINE_AA)
    if active:
        cv2.line(frame, (cx - 4, cy + 2), (cx + 4, cy - 2), col, 2, cv2.LINE_AA)
    label = "절전" if active else "Z"
    put_text(frame, label, (x + 32, y + 24), size=14, color=col, bold=active)
    return frame
