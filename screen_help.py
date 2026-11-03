"""화면 위 사용법. H키로 열거나 닫는다."""
from __future__ import annotations

import time

import cv2

from korean_font import put_text, wrap_text, blend_rect

_PANEL = (15, 12, 8)
_CYAN = (255, 220, 0)
_DIM = (190, 160, 40)

_HELP_HANDS = """Gesture Commands (with G mouse control OFF)
Hold your index finger up briefly to start recording.
You do not need to say the wake word.
Hold a peace sign (V) with index & middle fingers
to send your voice command.
Lowering your hand and staying quiet for 3 seconds
also sends the command automatically.

On-Screen Keyboard
Press Enter or / to open the input bar at the bottom.
Type your command and press Enter to send,
Tab to toggle EN/KO input, or Esc to cancel.

Background Tasks
Press B to toggle prefetch, news, price alerts, etc.
Press H to show or hide this help panel.
Press D to dismiss any active analysis overlay.

Power Save
Press Z or say "Power save on" — turns off webcam & Whisper."""

_HELP = """Voice & Keyboard Commands
Say "Jarvis" / "Hey Jarvis" or press Space
to start voice recording.
Press V to enroll your voice profile (5 samples)
for owner-only wake authentication.

On-Screen Keyboard
Press Enter or / to open the input bar at the bottom.
Type your command and press Enter to send,
Tab to toggle EN/KO input, or Esc to cancel.

Background Tasks
Press B to toggle prefetch, news, price alerts, etc.
Press H to show or hide this help panel.
Press D to dismiss any active analysis overlay."""

_HELP_COMPACT_HANDS = """Gesture: Hold Index → Talk · Peace(V) → Send
Keyboard: Enter or / · Tab EN/KO · Esc Cancel
B: Background · P: Perf · H: Help · D: Close Overlay"""

_HELP_COMPACT = """Voice: "Jarvis" · Space to Talk · V Voice Enroll
Keyboard: Enter or / · Tab EN/KO · Esc Cancel
Z: Power Save (Cam/STT OFF) · B · P · H · D"""

# 패널 위치 부드럽게 보간
_SMOOTH = {"x": 0.0, "y": 0.0, "open": 0.0, "t": 0.0}


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def _rects_overlap(a, b, pad: int = 12) -> bool:
    if not a or not b:
        return False
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ax1 -= pad
    ay1 -= pad
    ax2 += pad
    ay2 += pad
    return not (ax2 <= bx1 or bx2 <= ax1 or ay2 <= by1 or by2 <= ay1)


def _status_rect(w: int) -> tuple[int, int, int, int]:
    return (w - 190, 58, w - 8, 228)


def _processing_rect(w: int) -> tuple[int, int, int, int]:
    bw = 380
    x0 = w // 2 - bw // 2
    return (x0, 58, x0 + bw, 58 + 64)


def _comm_log_rect(h: int) -> tuple[int, int, int, int]:
    py, pw, vis, lh = 115, 250, 14, 17
    ph = vis * lh + 40
    return (10, py, 10 + pw, py + ph)


def _pick_layout(
    w: int,
    h: int,
    *,
    compact: bool,
    avoid: list[tuple[int, int, int, int]],
) -> tuple[int, int, int, int, int]:
    """(x, y, pw, ph, font_size)"""
    bottom_reserve = 72
    font = 12 if compact else 13
    body = _HELP_COMPACT if compact else _HELP
    max_pw = 360 if compact else 400
    pw = min(max_pw, w - 32)
    lines = wrap_text(body, pw - 28, font)
    line_h = font + 7
    ph = min(len(lines) * line_h + 44, h - bottom_reserve - 80)
    ph = max(ph, 88 if compact else 120)

    candidates: list[tuple[int, int, int, int, int]] = []

    # 1) COMM LOG 오른쪽 하단 — 분석/우측 패널과 겹치지 않을 때 우선
    cl = _comm_log_rect(h)
    x_bl = cl[2] + 8
    y_bl = h - bottom_reserve - ph
    if y_bl > cl[3] + 8:
        candidates.append((x_bl, y_bl, pw, ph, font))

    # 2) COMM LOG 아래 좁은 컬럼
    y_mid = cl[3] + 10
    if y_mid + ph < h - bottom_reserve:
        candidates.append((x_bl, y_mid, min(pw, w - x_bl - 12), ph, font))

    # 3) 우측 상단 — SYSTEM STATUS 아래 (표/종목 없을 때)
    x_tr = w - pw - 16
    y_tr = 232
    candidates.append((x_tr, y_tr, pw, min(ph, h - bottom_reserve - y_tr), font))

    # 4) 좌하단 (최후)
    candidates.append((16, h - bottom_reserve - ph, pw, ph, font))

    blocks = list(avoid) + [_status_rect(w)]
    for x, y, cpw, cph, cf in candidates:
        rect = (x, y, x + cpw, y + cph)
        if any(_rects_overlap(rect, b) for b in blocks):
            continue
        return x, y, cpw, cph, cf

    # 모두 겹치면 가장 작은 compact를 하단 좌측에
    cpw = min(300, w - 32)
    cph = min(160, h - bottom_reserve - 100)
    return 16, h - bottom_reserve - cph, cpw, cph, 11


def draw_help(
    frame,
    visible: bool,
    *,
    hud_state: str = "IDLE",
    overlay_active: bool = False,
    overlay_rect: tuple[int, int, int, int] | None = None,
    has_answer: bool = False,
    hands_enabled: bool = False,
):
    from i18n import get_lang_meta, tr
    h, w = frame.shape[:2]
    now = time.monotonic()
    dt = now - _SMOOTH["t"] if _SMOOTH["t"] else 1 / 60.0
    _SMOOTH["t"] = now
    ease = min(1.0, dt * 10.0)

    target_open = 1.0 if visible else 0.0
    _SMOOTH["open"] = _lerp(_SMOOTH["open"], target_open, ease)

    if not visible and _SMOOTH["open"] < 0.04:
        put_text(frame, tr("help_button"), (w - 110, 36), size=14, color=_DIM)
        return frame

    busy = (
        overlay_active
        or hud_state in ("PROCESSING", "SPEAKING")
        or bool(has_answer)
    )
    compact = busy or overlay_rect is not None
    avoid: list[tuple[int, int, int, int]] = []
    if overlay_rect:
        avoid.append(overlay_rect)
    if hud_state == "PROCESSING":
        avoid.append(_processing_rect(w))

    tx, ty, pw, ph, font = _pick_layout(w, h, compact=compact, avoid=avoid)

    if _SMOOTH["x"] == 0.0 and _SMOOTH["y"] == 0.0:
        _SMOOTH["x"], _SMOOTH["y"] = float(tx), float(ty)
    _SMOOTH["x"] = _lerp(_SMOOTH["x"], float(tx), ease)
    _SMOOTH["y"] = _lerp(_SMOOTH["y"], float(ty), ease)
    x, y = int(_SMOOTH["x"]), int(_SMOOTH["y"])

    alpha_panel = 0.78 * _SMOOTH["open"]
    lang_lbl = get_lang_meta()["label"]
    body = tr("help_compact", lang=lang_lbl) if compact else tr("help_body", lang=lang_lbl)
    lines = wrap_text(body, pw - 28, font)
    line_h = font + 7
    used_h = min(ph, len(lines) * line_h + 44)
    ph = used_h

    blend_rect(frame, x, y, x + pw, y + ph, _PANEL, alpha_panel)
    cv2.rectangle(frame, (x, y), (x + pw, y + ph), _CYAN, 1, cv2.LINE_AA)

    title = tr("help_title") + (tr("help_summary_suffix") if compact else "")
    put_text(frame, title, (x + 14, y + 26), size=15 if compact else 17,
             color=_CYAN, bold=True)
    ly = y + 48
    text_col = (220, 232, 238)
    for ln in lines:
        if ly > y + ph - 12:
            break
        put_text(frame, ln, (x + 14, ly), size=font, color=text_col)
        ly += line_h

    if compact and _SMOOTH["open"] > 0.5:
        put_text(frame, tr("help_close"), (x + pw - 74, y + ph - 6), size=10, color=_DIM)
    return frame
