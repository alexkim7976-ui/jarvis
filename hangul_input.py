"""두벌식 한글 조합 — OpenCV는 한글 IME를 받지 못하므로 영문 키로 조합한다.

맥 입력기를 영어(ABC)로 둔 채 qwerty 자리를 두벌식으로 해석한다.
예: dkssud → 안녕, gksrmf → 한글
"""
from __future__ import annotations

CHOSEONG = [
    "ㄱ", "ㄲ", "ㄴ", "ㄷ", "ㄸ", "ㄹ", "ㅁ", "ㅂ", "ㅃ", "ㅅ",
    "ㅆ", "ㅇ", "ㅈ", "ㅉ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
]
JUNGSEONG = [
    "ㅏ", "ㅐ", "ㅑ", "ㅒ", "ㅓ", "ㅔ", "ㅕ", "ㅖ", "ㅗ", "ㅘ",
    "ㅙ", "ㅚ", "ㅛ", "ㅜ", "ㅝ", "ㅞ", "ㅟ", "ㅠ", "ㅡ", "ㅢ", "ㅣ",
]
JONGSEONG = [
    "", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ",
    "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ",
    "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
]

_COMPOUND_JUNG = {
    ("ㅗ", "ㅏ"): "ㅘ",
    ("ㅗ", "ㅐ"): "ㅙ",
    ("ㅗ", "ㅣ"): "ㅚ",
    ("ㅜ", "ㅓ"): "ㅝ",
    ("ㅜ", "ㅔ"): "ㅞ",
    ("ㅜ", "ㅣ"): "ㅟ",
    ("ㅡ", "ㅣ"): "ㅢ",
}
_SPLIT_JUNG = {v: k for k, v in _COMPOUND_JUNG.items()}

_COMPOUND_JONG = {
    ("ㄱ", "ㅅ"): "ㄳ",
    ("ㄴ", "ㅈ"): "ㄵ",
    ("ㄴ", "ㅎ"): "ㄶ",
    ("ㄹ", "ㄱ"): "ㄺ",
    ("ㄹ", "ㅁ"): "ㄻ",
    ("ㄹ", "ㅂ"): "ㄼ",
    ("ㄹ", "ㅅ"): "ㄽ",
    ("ㄹ", "ㅌ"): "ㄾ",
    ("ㄹ", "ㅍ"): "ㄿ",
    ("ㄹ", "ㅎ"): "ㅀ",
    ("ㅂ", "ㅅ"): "ㅄ",
}
_SPLIT_JONG = {v: k for k, v in _COMPOUND_JONG.items()}

_CONS = {
    "r": "ㄱ", "R": "ㄲ", "s": "ㄴ", "e": "ㄷ", "E": "ㄸ", "f": "ㄹ",
    "a": "ㅁ", "q": "ㅂ", "Q": "ㅃ", "t": "ㅅ", "T": "ㅆ", "d": "ㅇ",
    "w": "ㅈ", "W": "ㅉ", "c": "ㅊ", "z": "ㅋ", "x": "ㅌ", "v": "ㅍ", "g": "ㅎ",
}
_VOWELS = {
    "k": "ㅏ", "o": "ㅐ", "i": "ㅑ", "O": "ㅒ", "j": "ㅓ", "p": "ㅔ",
    "u": "ㅕ", "P": "ㅖ", "h": "ㅗ", "y": "ㅛ", "n": "ㅜ", "b": "ㅠ",
    "m": "ㅡ", "l": "ㅣ",
}
_JONG_OK = set(JONGSEONG) - {""}
_VOWEL_SET = set(JUNGSEONG)


def _syllable(cho: str, jung: str, jong: str = "") -> str:
    return chr(
        0xAC00
        + CHOSEONG.index(cho) * 588
        + JUNGSEONG.index(jung) * 28
        + JONGSEONG.index(jong)
    )


class HangulComposer:
    """완성된 문자열 + 조합 중인 음절 하나."""

    def __init__(self) -> None:
        self._done: list[str] = []
        self.cho = ""
        self.jung = ""
        self.jong = ""

    def clear(self) -> None:
        self._done.clear()
        self.cho = self.jung = self.jong = ""

    def text(self) -> str:
        cur = self._current()
        return "".join(self._done) + cur

    def _current(self) -> str:
        if self.cho and self.jung:
            return _syllable(self.cho, self.jung, self.jong)
        return self.cho

    def _flush(self) -> None:
        cur = self._current()
        if cur:
            self._done.append(cur)
        self.cho = self.jung = self.jong = ""

    def commit(self) -> None:
        """조합 중인 음절을 확정한다."""
        self._flush()

    def append(self, ch: str) -> None:
        """조합을 끊고 글자를 그대로 붙인다 (영문·숫자·공백)."""
        self._flush()
        if ch:
            self._done.append(ch)

    def type_key(self, ch: str) -> None:
        """두벌식 키 한 글자. 매핑되지 않으면 그대로 삽입."""
        if ch in _CONS:
            self._type_cons(_CONS[ch])
        elif ch in _VOWELS:
            self._type_vowel(_VOWELS[ch])
        else:
            self.append(ch)

    def _type_vowel(self, vowel: str) -> None:
        if self.cho and self.jung and self.jong:
            self._carry_jong(vowel)
            return
        if self.cho and self.jung:
            combo = _COMPOUND_JUNG.get((self.jung, vowel))
            if combo:
                self.jung = combo
            else:
                self._flush()
                self._done.append(vowel)
            return
        if self.cho:
            self.jung = vowel
            return
        self._flush()
        self._done.append(vowel)

    def _type_cons(self, cons: str) -> None:
        if self.cho and self.jung and self.jong:
            combo = _COMPOUND_JONG.get((self.jong, cons))
            if combo:
                self.jong = combo
            else:
                self._flush()
                self.cho = cons
            return
        if self.cho and self.jung:
            if cons in _JONG_OK:
                self.jong = cons
            else:
                self._flush()
                self.cho = cons
            return
        if self.cho:
            self._flush()
            self.cho = cons
            return
        self.cho = cons

    def _carry_jong(self, vowel: str) -> None:
        nxt = self.jong
        if nxt in _SPLIT_JONG:
            keep, nxt = _SPLIT_JONG[nxt]
            self.jong = keep
        else:
            self.jong = ""
        self._flush()
        self.cho = nxt
        self.jung = vowel

    def backspace(self) -> None:
        if self.jong:
            if self.jong in _SPLIT_JONG:
                self.jong = _SPLIT_JONG[self.jong][0]
            else:
                self.jong = ""
            return
        if self.jung:
            if self.jung in _SPLIT_JUNG:
                self.jung = _SPLIT_JUNG[self.jung][0]
            else:
                self.jung = ""
            return
        if self.cho:
            self.cho = ""
            return
        if not self._done:
            return
        ch = self._done.pop()
        code = ord(ch)
        if 0xAC00 <= code <= 0xD7A3:
            n = code - 0xAC00
            self.cho = CHOSEONG[n // 588]
            self.jung = JUNGSEONG[(n % 588) // 28]
            self.jong = JONGSEONG[n % 28]
            self.backspace()
