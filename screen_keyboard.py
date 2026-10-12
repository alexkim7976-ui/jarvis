"""
J.A.R.V.I.S — 화면 키보드 입력

OpenCV 창에 명령 줄을 띄우고, 맥 입력기가 영어일 때 두벌식으로 한글을 조합한다.
Enter(또는 /)로 열고, Enter로 전송, Esc로 취소, Tab으로 한/영, Backspace로 지우기.
"""
from __future__ import annotations

import cv2

from hangul_input import HangulComposer
from korean_font import put_text

_PANEL = (15, 12, 8)
_CYAN = (255, 220, 0)
_DIM = (180, 140, 0)
_GREEN = (80, 255, 120)


class ScreenKeyboard:
    def __init__(self) -> None:
        self.active = False
        self.hangul = True
        self._composer = HangulComposer()

    def open(self) -> None:
        self.active = True
        self._composer.clear()

    def close(self) -> None:
        self.active = False
        self._composer.clear()

    def buffer(self) -> str:
        return self._composer.text()

    def feed(self, key: int) -> str | None:
        """
        None: 계속 입력.
        \"\": 취소 또는 빈 전송.
        그 외: 전송할 명령.
        """
        if not self.active or key in (0, 255):
            return None
        if key == 27:
            self.close()
            return ""
        if key in (13, 10):
            text = self._composer.text().strip()
            self.close()
            return text
        if key in (8, 127):
            self._composer.backspace()
            return None
        if key == 9:
            self._composer.commit()
            self.hangul = not self.hangul
            return None
        if 32 <= key <= 126:
            ch = chr(key)
            if self.hangul:
                self._composer.type_key(ch)
            else:
                self._composer.append(ch)
            return None
        return None

    def draw(self, frame):
        """입력 중이 아니면 짧은 안내, 입력 중이면 명령 줄을 화면 하단에 그린다."""
        h, w = frame.shape[:2]
        if self.active:
            top = h - 112
            ov = frame.copy()
            cv2.rectangle(ov, (0, top), (w, h - 62), _PANEL, -1)
            cv2.addWeighted(ov, 0.82, frame, 0.18, 0, frame)
            cv2.rectangle(frame, (8, top + 4), (w - 8, h - 66), _CYAN, 1, cv2.LINE_AA)
            mode = "한글" if self.hangul else "영문"
            put_text(frame, f"⌨ {mode}", (16, top + 30), size=16, color=_CYAN, bold=True)
            shown = self.buffer()
            if len(shown) > 32:
                shown = "…" + shown[-32:]
            color = _GREEN if self.buffer() else _DIM
            put_text(frame, (shown or "명령을 입력하세요") + "_", (100, top + 30), size=18, color=color)
            put_text(
                frame, "입력기 영어 · Tab 한/영 · Enter 전송 · Esc 취소",
                (16, top + 48), size=13, color=_DIM,
            )
            return frame

        put_text(
            frame, "Enter 키보드 명령  ·  검지 유지=말하기  ·  브이 유지=전송",
            (16, h - 78), size=13, color=_DIM,
        )
        return frame
