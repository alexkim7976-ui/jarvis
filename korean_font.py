from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import ImageFont, ImageDraw, Image

# ──────────────────────────────────────────────
#  폰트 검색 (macOS → Linux → Windows → 폴백)
# ──────────────────────────────────────────────
_FONT_SEARCH = [
    # macOS
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    # Linux
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    # Windows
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/NanumGothic.ttf",
    "C:/Windows/Fonts/arial.ttf",
]

_font_cache = {}


def _find_font_path():
    """사용 가능한 한글 폰트 경로 찾기"""
    for path in _FONT_SEARCH:
        if Path(path).exists():
            return path
    return None


_FONT_PATH = _find_font_path()


def _get_font(size):
    """폰트 캐시에서 가져오거나 새로 생성"""
    if size in _font_cache:
        return _font_cache[size]

    try:
        if _FONT_PATH:
            font = ImageFont.truetype(_FONT_PATH, size)
        else:
            font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    _font_cache[size] = font
    return font


def put_text(frame, text, pos, size=16, color=(255, 255, 255), bold=False):
    """
    OpenCV 프레임에 한글 텍스트를 렌더링

    Args:
        frame: OpenCV BGR 프레임 (np.ndarray)
        text: 표시할 텍스트 (한글/영문 모두 가능)
        pos: (x, y) 좌표 — cv2.putText와 동일하게 좌하단 기준
        size: 폰트 크기 (픽셀)
        color: BGR 색상 튜플
        bold: 굵게 표시 여부
    """
    if not text:
        return frame

    x, y = pos
    # cv2.putText는 좌하단 기준이므로, PIL용으로 좌상단으로 변환
    y_top = y - size

    # BGR → RGB 변환
    rgb_color = (color[2], color[1], color[0])

    # OpenCV 프레임 → PIL 이미지
    pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)
    font = _get_font(size)

    # 텍스트 그리기
    draw.text((x, y_top), text, font=font, fill=rgb_color)

    # 굵게 (1px 오프셋으로 중복 그리기)
    if bold:
        draw.text((x + 1, y_top), text, font=font, fill=rgb_color)

    # PIL → OpenCV 프레임
    result = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    np.copyto(frame, result)

    return frame


def text_width(text, size=16):
    """텍스트의 픽셀 너비 계산"""
    font = _get_font(size)
    try:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]
    except Exception:
        return len(text) * size // 2


def wrap_text(text, max_width_px, size=16):
    """텍스트를 max_width_px 이내로 줄바꿈"""
    font = _get_font(size)
    words = text.split()
    lines = []
    current = ""

    for word in words:
        test = current + " " + word if current else word
        try:
            w = font.getbbox(test)[2]
        except Exception:
            w = len(test) * size // 2

        if w > max_width_px and current:
            lines.append(current)
            current = word
        else:
            current = test

    if current:
        lines.append(current)

    return lines if lines else [text]
