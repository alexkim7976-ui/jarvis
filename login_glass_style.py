"""Bloomie 스타일 리퀴드 글래스 — Tkinter 로그인 UI용 (Pillow)."""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageTk
except ImportError:
    Image = ImageDraw = ImageFilter = ImageTk = None  # type: ignore

# Bloomie LiquidGlassTone / GlassBox 팔레트
_TONE = {
    "mist": {"fill": (232, 245, 252), "label": (74, 122, 150), "a_top": 0.18, "a_bot": 0.06},
    "sky": {"fill": (142, 200, 236), "label": (30, 84, 116), "a_top": 0.42, "a_bot": 0.26},
    "deep": {"fill": (47, 134, 188), "label": (10, 42, 64), "a_top": 0.70, "a_bot": 0.54},
}


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def _rgba(c: tuple[int, int, int], alpha: float) -> tuple[int, int, int, int]:
    return (c[0], c[1], c[2], int(max(0, min(255, round(255 * alpha)))))


def glass_pill_image(
    width: int,
    height: int,
    *,
    tone: str = "sky",
    outline: bool = False,
) -> "Image.Image | None":
    if Image is None:
        return None
    t = _TONE.get(tone, _TONE["sky"])
    fill = t["fill"]
    w, h = max(40, width), max(28, height)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = h // 2
    box = (1, 1, w - 2, h - 2)

    top = _rgba(
        (
            int(_lerp(fill[0], 255, 0.55)),
            int(_lerp(fill[1], 255, 0.55)),
            int(_lerp(fill[2], 255, 0.55)),
        ),
        t["a_top"] if not outline else t["a_top"] * 0.45,
    )
    mid = _rgba(fill, (t["a_top"] + t["a_bot"]) / 2 if not outline else t["a_top"] * 0.35)
    bot = _rgba(
        (
            int(_lerp(fill[0], 26, 0.08)),
            int(_lerp(fill[1], 74, 0.08)),
            int(_lerp(fill[2], 104, 0.08)),
        ),
        t["a_bot"] if not outline else t["a_bot"] * 0.35,
    )

    grad = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(grad)
    for y in range(h):
        u = y / max(h - 1, 1)
        if u < 0.45:
            tt = u / 0.45
            col = tuple(int(_lerp(top[i], mid[i], tt)) for i in range(4))
        else:
            tt = (u - 0.45) / 0.55
            col = tuple(int(_lerp(mid[i], bot[i], tt)) for i in range(4))
        gdraw.line([(0, y), (w, y)], fill=col)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
    img = Image.composite(grad, img, mask)

    rim_a = 0.62 if not outline else 0.74
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(box, radius=radius, outline=_rgba((255, 255, 255), rim_a), width=2)

    # 상단 rim 하이라이트
    hi = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hi)
    hd.ellipse(
        (w * 0.12, -h * 0.35, w * 0.88, h * 0.55),
        fill=(255, 255, 255, 48 if not outline else 32),
    )
    img = Image.alpha_composite(img, hi)
    return img.filter(ImageFilter.GaussianBlur(0.4))


def glass_card_image(width: int, height: int, *, radius: int = 28) -> "Image.Image | None":
    """Bloomie GlassBox — 큰 설정 패널."""
    if Image is None:
        return None
    w, h = max(120, width), max(80, height)
    fill = (220, 237, 250)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    grad = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(grad)
    for y in range(h):
        u = y / max(h - 1, 1)
        if u < 0.45:
            a = 0.40 + (0.55 - 0.40) * (1.0 - u / 0.45)
        else:
            a = 0.16 + (0.40 - 0.16) * (1.0 - (u - 0.45) / 0.55)
        col = _rgba(
            (
                int(_lerp(fill[0], 255, 0.5)),
                int(_lerp(fill[1], 255, 0.5)),
                int(_lerp(fill[2], 255, 0.5)),
            ),
            a,
        )
        gdraw.line([(0, y), (w, y)], fill=col)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    img = Image.composite(grad, img, mask)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        (1, 1, w - 2, h - 2), radius=radius,
        outline=_rgba((255, 255, 255), 0.58), width=2,
    )
    hi = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(hi).ellipse(
        (w * 0.08, -h * 0.2, w * 0.92, h * 0.42),
        fill=(255, 255, 255, 36),
    )
    img = Image.alpha_composite(img, hi)
    return img.filter(ImageFilter.GaussianBlur(0.6))


def glass_log_panel_image(width: int, height: int, *, radius: int = 16) -> "Image.Image | None":
    if Image is None:
        return None
    w, h = max(80, width), max(48, height)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    grad = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(grad)
    for y in range(h):
        a = 0.55 if y < h * 0.4 else 0.38
        gdraw.line([(0, y), (w, y)], fill=(18, 28, 38, int(255 * a)))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    img = Image.composite(grad, img, mask)
    ImageDraw.Draw(img).rounded_rectangle(
        (1, 1, w - 2, h - 2), radius=radius,
        outline=_rgba((142, 200, 236), 0.45), width=1,
    )
    return img


# Sign-in card (참고: dark glassmorphism Sign in)
SIGNIN_INNER = "#2b3340"
SIGNIN_FIELD = "#3a4452"
SIGNIN_FG = "#f2f6fa"
SIGNIN_MUTED = "#9aa8b8"
SIGNIN_PRIMARY = (47, 134, 188)


def glass_signin_card_image(width: int, height: int, *, radius: int = 32) -> "Image.Image | None":
    """중앙 Sign-in 카드 — 다크 frosted glass."""
    if Image is None:
        return None
    w, h = max(280, width), max(200, height)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    grad = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(grad)
    inner = (43, 51, 64)
    for y in range(h):
        u = y / max(h - 1, 1)
        lift = 1.0 - abs(u - 0.35) * 0.35
        col = _rgba(
            (
                int(_lerp(inner[0], 58, lift * 0.15)),
                int(_lerp(inner[1], 66, lift * 0.15)),
                int(_lerp(inner[2], 78, lift * 0.15)),
            ),
            0.88 if u < 0.5 else 0.82,
        )
        gdraw.line([(0, y), (w, y)], fill=col)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    img = Image.composite(grad, img, mask)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        (1, 1, w - 2, h - 2), radius=radius,
        outline=_rgba((255, 255, 255), 0.42), width=2,
    )
    hi = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(hi).ellipse(
        (w * 0.1, -h * 0.15, w * 0.9, h * 0.38),
        fill=(255, 255, 255, 38),
    )
    img = Image.alpha_composite(img, hi)
    return img.filter(ImageFilter.GaussianBlur(0.5))


def glass_signin_field_image(width: int, height: int = 46, *, radius: int = 23) -> "Image.Image | None":
    if Image is None:
        return None
    w, h = max(120, width), max(36, height)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    grad = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(grad)
    base = (58, 68, 82)
    for y in range(h):
        a = 0.92 if y < h * 0.45 else 0.85
        gdraw.line([(0, y), (w, y)], fill=(*base, int(255 * a)))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    img = Image.composite(grad, img, mask)
    ImageDraw.Draw(img).rounded_rectangle(
        (1, 1, w - 2, h - 2), radius=radius,
        outline=_rgba((255, 255, 255), 0.22), width=1,
    )
    return img


def glass_primary_button_image(width: int, height: int = 48) -> "Image.Image | None":
    if Image is None:
        return None
    w, h = max(100, width), max(40, height)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    grad = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(grad)
    top = (72, 168, 228)
    bot = (38, 118, 178)
    radius = h // 2
    for y in range(h):
        u = y / max(h - 1, 1)
        col = tuple(int(_lerp(top[i], bot[i], u)) for i in range(3))
        gdraw.line([(0, y), (w, y)], fill=(*col, 255))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    img = Image.composite(grad, img, mask)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((1, 1, w - 2, h - 2), radius=radius, outline=_rgba((255, 255, 255), 0.55), width=2)
    hi = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(hi).ellipse((w * 0.15, -h * 0.2, w * 0.85, h * 0.55), fill=(255, 255, 255, 55))
    img = Image.alpha_composite(img, hi)
    return img.filter(ImageFilter.GaussianBlur(0.25))


def glass_panel_image(width: int, height: int, *, radius: int = 22) -> "Image.Image | None":
    """입력 필드용 GlassBox 톤."""
    if Image is None:
        return None
    w, h = max(80, width), max(36, height)
    base = glass_pill_image(w, h, tone="mist", outline=True)
    if base is None:
        return None
    # 더 납작한 라운드
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(base, (0, 0), mask)
    return out


def _pil_draw_chat(draw, cx: int, cy: int, s: int, color: tuple[int, int, int]) -> None:
    x0, y0 = cx - s // 2, cy - s // 2 + 2
    bh = int(s * 0.72)
    draw.rounded_rectangle((x0, y0, x0 + s, y0 + bh), radius=8, outline=color, width=2)
    draw.polygon(
        [(x0 + s // 3, y0 + bh), (x0 + s // 3 + 8, y0 + bh + 10), (x0 + s // 3 + 14, y0 + bh)],
        outline=color,
    )


def _pil_draw_calendar(draw, cx: int, cy: int, s: int, color: tuple[int, int, int]) -> None:
    x0, y0 = cx - s // 2, cy - s // 2
    draw.rectangle((x0, y0 + 6, x0 + s, y0 + s), outline=color, width=2)
    draw.line((x0, y0 + 16, x0 + s, y0 + 16), fill=color, width=2)
    for dx in (s // 3, 2 * s // 3):
        draw.line((x0 + dx, y0 + 6, x0 + dx, y0), fill=color, width=2)


def _pil_draw_pin(draw, cx: int, cy: int, s: int, color: tuple[int, int, int]) -> None:
    draw.ellipse((cx - s // 3, cy - s // 3, cx + s // 3, cy + s // 6), fill=color)
    draw.polygon([(cx, cy + s // 2), (cx - s // 3, cy - 2), (cx + s // 3, cy - 2)], fill=color)
    draw.ellipse((cx - 3, cy - 6, cx + 3, cy), fill=(255, 255, 255, 200))


def _pil_draw_gear(draw, cx: int, cy: int, s: int, color: tuple[int, int, int]) -> None:
    r = s // 2
    for i in range(8):
        import math

        ang = math.radians(i * 45)
        x1 = cx + int(r * 0.55 * math.cos(ang))
        y1 = cy + int(r * 0.55 * math.sin(ang))
        x2 = cx + int(r * math.cos(ang))
        y2 = cy + int(r * math.sin(ang))
        draw.line((x1, y1, x2, y2), fill=color, width=max(2, s // 8))
    draw.ellipse((cx - s // 5, cy - s // 5, cx + s // 5, cy + s // 5), fill=color)


def glass_quick_dock_image(
    width: int = 168,
    height: int = 92,
    *,
    active: tuple[bool, bool, bool, bool] = (False, False, False, False),
) -> "Image.Image | None":
    """설정/로그인 UI용 2×2 liquid glass 아이콘 바 (HUD와 동일 톤)."""
    if Image is None:
        return None
    w, h = max(120, width), max(80, height)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    pill_h = (h - 8) // 2
    colors = (
        (210, 70, 220),
        (40, 130, 240),
        (75, 200, 90),
        (155, 155, 155),
    )
    icons = (_pil_draw_chat, _pil_draw_calendar, _pil_draw_pin, _pil_draw_gear)
    for row in range(2):
        py = row * (pill_h + 8)
        pill = glass_panel_image(w, pill_h, radius=max(14, pill_h // 2))
        if pill is None:
            continue
        img.paste(pill, (0, py), pill)
        draw = ImageDraw.Draw(img)
        draw.line((w // 2, py + 10, w // 2, py + pill_h - 10), fill=(225, 230, 240, 180), width=1)
        for col in range(2):
            idx = row * 2 + col
            cx = w // 4 + col * (w // 2)
            cy = py + pill_h // 2
            col_rgb = colors[idx]
            if active[idx]:
                col_rgb = tuple(min(255, c + 20) for c in col_rgb)
            icons[idx](draw, cx, cy, 22, col_rgb)
    return img


def icon_in_liquid_glass(
    icon_path: Path,
    size: int = 96,
) -> "Image.Image | None":
    """앱 아이콘을 Bloomie식 물방울 글래스 캡슐 안에."""
    if Image is None or not icon_path.is_file():
        return None
    pad = max(8, size // 8)
    outer = size + pad * 2
    ring = glass_pill_image(outer, outer, tone="sky")
    if ring is None:
        return None
    # 원형 마스크
    mask = Image.new("L", (outer, outer), 0)
    ImageDraw.Draw(mask).ellipse((2, 2, outer - 3, outer - 3), fill=255)
    bg = Image.new("RGBA", (outer, outer), (0, 0, 0, 0))
    bg.paste(ring, (0, 0), mask)

    icon = Image.open(icon_path).convert("RGBA")
    icon = icon.resize((size, size), Image.Resampling.LANCZOS)
    cx = (outer - size) // 2
    cy = (outer - size) // 2
    bg.paste(icon, (cx, cy), icon)

    spec = Image.new("RGBA", (outer, outer), (0, 0, 0, 0))
    sd = ImageDraw.Draw(spec)
    sd.ellipse(
        (outer * 0.18, outer * 0.08, outer * 0.72, outer * 0.48),
        fill=(255, 255, 255, 70),
    )
    bg = Image.alpha_composite(bg, spec)
    return bg


def background_gradient(size: tuple[int, int]) -> "Image.Image | None":
    if Image is None:
        return None
    w, h = size
    img = Image.new("RGB", (w, h))
    top = (8, 14, 24)
    mid = (12, 22, 38)
    bot = (6, 10, 18)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        u = y / max(h - 1, 1)
        if u < 0.55:
            t = u / 0.55
            c = tuple(int(_lerp(top[i], mid[i], t)) for i in range(3))
        else:
            t = (u - 0.55) / 0.45
            c = tuple(int(_lerp(mid[i], bot[i], t)) for i in range(3))
        draw.line([(0, y), (w, y)], fill=c)
    # 은은한 블루 caustic
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for cx, cy, r, a in (
        (w * 0.2, h * 0.15, min(w, h) * 0.35, 28),
        (w * 0.85, h * 0.25, min(w, h) * 0.28, 22),
        (w * 0.5, h * 0.92, min(w, h) * 0.45, 18),
    ):
        gd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(100, 180, 230, a))
    glow = glow.filter(ImageFilter.GaussianBlur(42))
    # silk diagonal bands (sign-in 배경 참고)
    bands = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(bands)
    step = max(48, w // 8)
    for i in range(-h, w + h, step):
        bd.polygon(
            [(i, 0), (i + step // 2, 0), (i + step // 2 + h, h), (i + h, h)],
            fill=(255, 255, 255, 10),
        )
    bands = bands.filter(ImageFilter.GaussianBlur(18))
    base = Image.alpha_composite(img.convert("RGBA"), bands)
    return Image.alpha_composite(base, glow)


def default_icon_path() -> Path:
    root = Path(__file__).resolve().parent
    for p in (
        root / "assets" / "jarvis_icon_512.png",
        root / "assets" / "AppIcon.icns",
    ):
        if p.is_file():
            return p
    return root / "assets" / "jarvis_icon_512.png"


def pil_available() -> bool:
    return Image is not None and ImageTk is not None


def tk_korean_font(size: int = 13) -> tuple[str, int]:
    """macOS Aqua에서 한글 IME·렌더링에 쓸 폰트 (실제 등록된 패밀리명)."""
    candidates = (
        "Apple SD Gothic Neo",
        "AppleSDGothicNeo",
        ".AppleSystemUIFont",
        "Nanum Gothic",
        "Helvetica Neue",
        "Helvetica",
    )
    try:
        import tkinter as tk
        import tkinter.font as tkfont

        root = tk._default_root
        if root is not None:
            families = set(tkfont.families())
            for fam in candidates:
                if fam in families:
                    return (fam, size)
    except Exception:
        pass
    if sys.platform == "darwin":
        return ("Apple SD Gothic Neo", size)
    return ("Helvetica", size)


def prepare_tk_signin_entry(widget, *, secret: bool = False) -> None:
    widget.configure(
        font=tk_korean_font(14),
        relief="flat",
        highlightthickness=0,
        bd=0,
        bg=SIGNIN_FIELD,
        fg=SIGNIN_FG,
        insertbackground=SIGNIN_FG,
        insertwidth=2,
    )
    if secret:
        widget.configure(show="•")


def prepare_tk_input_widget(widget, *, secret: bool = False) -> None:
    """한글 IME 조합·표시 안정화 (macOS는 흰 배경·검은 글자 — text_input_dialog 와 동일)."""
    widget.configure(
        font=tk_korean_font(13),
        relief="flat",
        highlightthickness=0,
        bd=0,
    )
    if secret:
        widget.configure(show="•")
    if sys.platform == "darwin":
        widget.configure(
            bg="#f6fbff",
            fg="#143042",
            insertbackground="#1e5474",
            insertwidth=2,
        )
    else:
        widget.configure(
            bg=UI_ENTRY_BG,
            fg=UI_ENTRY_FG,
            insertbackground=UI_ENTRY_FG,
        )


def focus_tk_window(root, widget=None, *, topmost_ms: int = 300) -> None:
    """창을 앞으로 — 포커스는 입력 위젯에 (root.focus_force 는 IME 조합을 끊는다)."""
    try:
        root.lift()
        root.attributes("-topmost", True)
        root.after(topmost_ms, lambda: root.attributes("-topmost", False))
        if widget is not None:
            root.after(30, lambda: widget.focus_set())
        else:
            root.focus_force()
    except Exception:
        pass


# Tkinter 공통 (Bloomie liquid glass)
UI_BG = "#0a1218"
UI_FG = "#e8f4fc"
UI_MUTED = "#8aa8be"
UI_LABEL = "#c5dce8"
UI_ENTRY_BG = "#eef6fc"
UI_ENTRY_FG = "#1e5474"
TONE_LABEL = {"mist": "#4a7a96", "sky": "#1e5474", "deep": "#f0f8ff"}


def attach_window_gradient(root, shell, refs: list) -> None:
    """배경 caustic 그radient (Pillow 필요)."""
    shell.configure(bg=UI_BG)
    root.configure(bg=UI_BG)
    if not pil_available():
        return
    from tkinter import Label

    bg_l = Label(shell, bd=0, bg=UI_BG)
    bg_l.place(relx=0, rely=0, relwidth=1, relheight=1)

    def _resize(_event=None) -> None:
        w = max(shell.winfo_width(), 400)
        h = max(shell.winfo_height(), 400)
        grad = background_gradient((w, h))
        if grad is None:
            return
        ph = ImageTk.PhotoImage(grad)
        refs.append(ph)
        bg_l.configure(image=ph)
        bg_l.lower()

    root.bind("<Configure>", _resize)
    root.after(60, _resize)


def create_signin_card_host(parent, refs: list, *, width: int, height: int):
    """Sign-in 카드 + 내부 패널 (옆 세로 띠 없음 — 전체 inner 색)."""
    import tkinter as tk

    host = tk.Frame(parent, bg=UI_BG, width=width, height=height, highlightthickness=0)
    host.pack_propagate(False)
    bg_lbl = tk.Label(host, bd=0, bg=UI_BG)
    bg_lbl.place(relx=0, rely=0, relwidth=1, relheight=1)
    body = tk.Frame(host, bg=SIGNIN_INNER, highlightthickness=0, bd=0)
    body.place(relx=0.045, rely=0.04, relwidth=0.91, relheight=0.92)

    def _repaint(_=None) -> None:
        if not pil_available():
            return
        img = glass_signin_card_image(width, height)
        if img is None:
            return
        ph = ImageTk.PhotoImage(img)
        refs.append(ph)
        bg_lbl.configure(image=ph)
        bg_lbl.image = ph  # type: ignore[attr-defined]

    host.after(50, _repaint)
    return host, body


def create_signin_field(parent, refs: list, *, secret: bool = False, width: int = 320):
    import tkinter as tk

    host = tk.Frame(parent, bg=SIGNIN_INNER, height=46, highlightthickness=0)
    host.pack_propagate(False)
    bg_lbl = tk.Label(host, bd=0, bg=SIGNIN_INNER)
    bg_lbl.place(relx=0, rely=0, relwidth=1, relheight=1)
    entry = tk.Entry(host, show="•" if secret else "")
    prepare_tk_signin_entry(entry, secret=secret)
    entry.place(relx=0.07, rely=0.22, relwidth=0.86, relheight=0.56)

    def _repaint(_=None) -> None:
        w = max(width, host.winfo_width(), 200)
        if w < 24 or not pil_available():
            return
        panel = glass_signin_field_image(w, 46)
        if panel is None:
            return
        ph = ImageTk.PhotoImage(panel)
        refs.append(ph)
        bg_lbl.configure(image=ph)
        bg_lbl.image = ph  # type: ignore[attr-defined]

    def _focus(_=None) -> None:
        entry.focus_set()

    bg_lbl.bind("<Button-1>", _focus)
    host.bind("<Button-1>", _focus)
    host.bind("<Configure>", lambda e: host.after_idle(_repaint) if e.widget is host else None)
    host.after(80, _repaint)
    return host, entry


def create_primary_button(parent, text: str, command, refs: list, *, width: int = 320):
    import tkinter as tk

    h = 48
    host = tk.Frame(parent, bg=SIGNIN_INNER, width=width, height=h, highlightthickness=0)
    host.pack_propagate(False)
    kw = dict(
        text=text,
        command=command,
        bd=0,
        highlightthickness=0,
        font=(tk_korean_font(14)[0], tk_korean_font(14)[1], "bold"),
        fg="#ffffff",
        activeforeground="#ffffff",
        bg=SIGNIN_INNER,
        activebackground=SIGNIN_INNER,
        cursor="hand2",
    )
    if pil_available():
        img = glass_primary_button_image(width, h)
        if img is not None:
            ph = ImageTk.PhotoImage(img)
            refs.append(ph)
            btn = tk.Button(host, image=ph, compound="center", **kw)
            btn.place(relx=0, rely=0, relwidth=1, relheight=1)
            return host
    btn = tk.Button(host, bg="#2f86bc", activebackground="#3a96cc", relief="flat", **kw)
    btn.place(relx=0, rely=0, relwidth=1, relheight=1)
    return host


def create_glass_entry(
    parent,
    refs: list,
    *,
    bg: str = UI_BG,
    width: int = 480,
    secret: bool = False,
):
    import tkinter as tk

    host = tk.Frame(parent, bg=bg, height=44, highlightthickness=0, bd=0)
    host.pack_propagate(False)
    bg_lbl = tk.Label(host, bd=0, bg=bg)
    bg_lbl.place(relx=0, rely=0, relwidth=1, relheight=1)

    entry = tk.Entry(host, show="•" if secret else "")
    prepare_tk_input_widget(entry, secret=secret)
    entry.place(relx=0.06, rely=0.2, relwidth=0.88, relheight=0.62)

    def _repaint(_event=None) -> None:
        if not pil_available():
            return
        w = max(width, host.winfo_width(), 200)
        if w < 24:
            return
        panel = glass_panel_image(w, 44, radius=18)
        if panel is None:
            return
        ph = ImageTk.PhotoImage(panel)
        refs.append(ph)
        bg_lbl.configure(image=ph)
        bg_lbl.image = ph  # type: ignore[attr-defined]

    def _focus_entry(_event=None) -> None:
        entry.focus_set()

    bg_lbl.bind("<Button-1>", _focus_entry)
    host.bind("<Button-1>", _focus_entry)
    host.bind("<Configure>", lambda e: host.after_idle(_repaint) if e.widget is host else None)
    host.after(80, _repaint)
    return host, entry


def create_glass_button(
    parent,
    text: str,
    command,
    refs: list,
    *,
    bg: str = UI_BG,
    tone: str = "deep",
    outline: bool = False,
):
    import tkinter as tk

    if pil_available():
        w = max(130, min(260, 8 + len(text) * 13))
        img = glass_pill_image(w, 46, tone=tone, outline=outline)
        if img is not None:
            ph = ImageTk.PhotoImage(img)
            refs.append(ph)
            fg = TONE_LABEL.get(tone, UI_ENTRY_FG) if outline else TONE_LABEL["deep"]
            return tk.Button(
                parent,
                text=text,
                command=command,
                image=ph,
                compound="center",
                bd=0,
                highlightthickness=0,
                font=("AppleSDGothicNeo", 12, "bold" if tone == "deep" and not outline else "normal"),
                fg=fg,
                activeforeground="#ffffff" if tone == "deep" and not outline else fg,
                bg=bg,
                activebackground=bg,
                cursor="hand2",
            )
    return tk.Button(
        parent,
        text=text,
        command=command,
        font=("AppleSDGothicNeo", 12, "bold" if tone == "deep" else "normal"),
        bg="#2f86bc" if tone == "deep" and not outline else "#c5e4f6",
        fg="#f0f8ff" if tone == "deep" and not outline else "#1e5474",
        relief="flat",
        padx=12,
        pady=8,
        cursor="hand2",
    )


def create_glass_card_host(parent, refs: list, *, width: int, height: int, bg: str = UI_BG):
    import tkinter as tk

    if pil_available():
        card = glass_card_image(width, height, radius=28)
        if card is not None:
            ph = ImageTk.PhotoImage(card)
            refs.append(ph)
            host = tk.Label(parent, image=ph, bd=0, bg=bg)
            return host, host
    inner = tk.Frame(parent, bg=bg, highlightthickness=1, highlightbackground="#5bafdc")
    return inner, inner
