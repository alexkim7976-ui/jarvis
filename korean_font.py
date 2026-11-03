from __future__ import annotations

from collections import OrderedDict
import os
import re
from pathlib import Path

import cv2
import numpy as np
from PIL import ImageFont, ImageDraw, Image

# ──────────────────────────────────────────────
#  다국어 폰트 검색 (EN / 中文 / 한국어 / 日本語 / Español)
# ──────────────────────────────────────────────
_FONT_BY_LANG: dict[str, list[str]] = {
    "zh": [
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ],
    "ja": [
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ],
    "ko": [
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "C:/Windows/Fonts/malgun.ttf",
    ],
    "default": [
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/malgun.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ],
}

_HAN_RE = re.compile(r"[\u4e00-\u9fff]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_HANGUL_RE = re.compile(r"[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]")

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}
_resolved_paths: dict[str, str | None] = {}

# LRU caches to avoid repeated PIL rasterization and text wrapping on every frame
_MASK_CACHE_MAX = 512
_mask_cache: OrderedDict[tuple[str, str, int, bool], np.ndarray] = OrderedDict()
_WRAP_CACHE_MAX = 256
_wrap_cache: OrderedDict[tuple[str, str, int, int], list[str]] = OrderedDict()


def _find_font_for_bucket(bucket: str) -> str | None:
    if bucket in _resolved_paths:
        return _resolved_paths[bucket]
    candidates = _FONT_BY_LANG.get(bucket, []) + _FONT_BY_LANG["default"]
    for path in candidates:
        if Path(path).exists():
            _resolved_paths[bucket] = path
            return path
    _resolved_paths[bucket] = None
    return None


def _pick_bucket(text: str = "") -> str:
    lang = (os.getenv("JARVIS_LANG") or "en").strip().lower()[:2]
    if text:
        if _HANGUL_RE.search(text):
            return "ko"
        if _KANA_RE.search(text):
            return "ja"
        if _HAN_RE.search(text):
            return "ja" if lang == "ja" else "zh"
    if lang in ("zh", "ja", "ko"):
        return lang
    return "default"


def _get_font(size: int, text: str = ""):
    bucket = _pick_bucket(text)
    key = (bucket, size)
    if key in _font_cache:
        return _font_cache[key]

    font_path = _find_font_for_bucket(bucket)
    try:
        if font_path:
            font = ImageFont.truetype(font_path, size)
        else:
            font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    _font_cache[key] = font
    return font


def _get_text_mask(text: str, size: int, bold: bool) -> np.ndarray | None:
    bucket = _pick_bucket(text)
    key = (bucket, text, size, bold)
    cached = _mask_cache.get(key)
    if cached is not None:
        _mask_cache.move_to_end(key)
        return cached

    font = _get_font(size, text)
    try:
        bbox = font.getbbox(text)
        tw = max(1, (bbox[2] - bbox[0]) + (4 if bold else 2))
        th = max(size + 6, (bbox[3] - bbox[1]) + 8)
    except Exception:
        tw = max(1, len(text) * size + 4)
        th = size + 10

    mask_img = Image.new("L", (tw, th), 0)
    draw = ImageDraw.Draw(mask_img)
    draw.text((1, 2), text, font=font, fill=255)
    if bold:
        draw.text((2, 2), text, font=font, fill=255)

    mask = np.array(mask_img, dtype=np.uint16)[..., np.newaxis]
    _mask_cache[key] = mask
    if len(_mask_cache) > _MASK_CACHE_MAX:
        _mask_cache.popitem(last=False)
    return mask


def blend_rect(
    frame: np.ndarray,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    bgr_color: tuple[int, int, int],
    alpha: float,
) -> np.ndarray:
    """Blend a filled rectangle onto `frame` in-place using ROI slicing (zero full-frame copy)."""
    if alpha <= 0.0:
        return frame
    h, w = frame.shape[:2]
    rx1, ry1 = max(0, int(x1)), max(0, int(y1))
    rx2, ry2 = min(w, int(x2)), min(h, int(y2))
    if rx2 <= rx1 or ry2 <= ry1:
        return frame
    roi = frame[ry1:ry2, rx1:rx2]
    if alpha >= 0.999:
        roi[:] = bgr_color
        return frame
    a = int(max(0, min(256, round(alpha * 256))))
    inv_a = 256 - a
    col = np.array(bgr_color, dtype=np.uint16)
    roi[:] = ((roi.astype(np.uint16) * inv_a + col * a) >> 8).astype(np.uint8)
    return frame


def put_text(frame, text, pos, size=16, color=(255, 255, 255), bold=False):
    """
    OpenCV 프레임에 다국어(영어/중국어/한국어/일본어/스페인어) 텍스트를 깨짐 없이 고속 렌더링.
    캐시된 글리프 알파 마스크와 ROI 슬라이싱으로 PIL 재래스터화 비용을 제거한다.
    """
    if not text:
        return frame

    text = str(text)
    mask = _get_text_mask(text, int(size), bool(bold))
    if mask is None:
        return frame

    h, w = frame.shape[:2]
    mh, mw = mask.shape[:2]
    x = int(pos[0]) - 1
    y = int(pos[1]) - int(size) - 2

    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(w, x + mw)
    y1 = min(h, y + mh)
    if x1 <= x0 or y1 <= y0:
        return frame

    mx0 = x0 - x
    my0 = y0 - y
    mx1 = mx0 + (x1 - x0)
    my1 = my0 + (y1 - y0)

    sub_mask = mask[my0:my1, mx0:mx1]
    roi = frame[y0:y1, x0:x1]
    col = np.array((int(color[0]), int(color[1]), int(color[2])), dtype=np.uint16)
    roi[:] = ((roi.astype(np.uint16) * (255 - sub_mask) + col * sub_mask) // 255).astype(np.uint8)
    return frame


def text_width(text, size=16):
    """텍스트의 픽셀 너비 계산"""
    if not text:
        return 0
    text = str(text)
    mask = _get_text_mask(text, int(size), False)
    if mask is not None:
        return max(0, mask.shape[1] - 2)
    font = _get_font(size, text)
    try:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]
    except Exception:
        return len(text) * size // 2


def wrap_text(text, max_width_px, size=16):
    """텍스트를 max_width_px 이내로 줄바꿈 (LRU 캐시 적용 + 중문/일문 글자 단위 줄바꿈 지원)."""
    if not text:
        return [""]
    text = str(text)
    bucket = _pick_bucket(text)
    cache_key = (bucket, text, int(max_width_px), int(size))
    cached = _wrap_cache.get(cache_key)
    if cached is not None:
        _wrap_cache.move_to_end(cache_key)
        return list(cached)

    font = _get_font(size, text)
    lines: list[str] = []

    def _w(s: str) -> int:
        try:
            b = font.getbbox(s)
            return b[2] - b[0]
        except Exception:
            return len(s) * size

    for raw_line in text.splitlines() or [text]:
        if not raw_line.strip():
            lines.append("")
            continue

        tokens = re.findall(r"\S+\s*", raw_line)
        current = ""

        for tok in tokens:
            candidate = current + tok
            if _w(candidate.rstrip()) <= max_width_px:
                current = candidate
                continue

            if _w(tok.rstrip()) > max_width_px:
                for ch in tok:
                    if current and _w((current + ch).rstrip()) > max_width_px:
                        lines.append(current.rstrip())
                        current = ch
                    else:
                        current += ch
            else:
                if current:
                    lines.append(current.rstrip())
                current = tok

        if current.strip():
            lines.append(current.rstrip())

    res = lines if lines else [text]
    _wrap_cache[cache_key] = res
    if len(_wrap_cache) > _WRAP_CACHE_MAX:
        _wrap_cache.popitem(last=False)
    return list(res)
