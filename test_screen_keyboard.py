"""두벌식 조합과 화면 키보드 입력."""
from hangul_input import HangulComposer
from screen_keyboard import ScreenKeyboard


def _type(keys: str) -> str:
    c = HangulComposer()
    for ch in keys:
        c.type_key(ch)
    return c.text()


def test_annyeong():
    assert _type("dkssud") == "안녕"


def test_hangul_word():
    assert _type("gksrmf") == "한글"


def test_apple():
    # 사과 = ㅅㅏ + ㄱㅗㅏ
    assert _type("tkrhk") == "사과"


def test_jong_carry():
    # 간 + ㅏ → 가나
    assert _type("rks k") == "간 가" or _type("rksk") == "가나"
    assert _type("rksk") == "가나"


def test_backspace_removes_jamo():
    c = HangulComposer()
    for ch in "dkssud":
        c.type_key(ch)
    assert c.text() == "안녕"
    c.backspace()  # ㅇ 종성 제거 → 녀
    assert c.text() == "안녀"
    c.backspace()  # ㅕ 제거 → ㄴ
    assert c.text() == "안ㄴ"


def test_english_and_space():
    c = HangulComposer()
    c.type_key("d")
    c.type_key("k")
    c.append(" ")
    c.append("A")
    assert c.text() == "아 A"


def test_screen_keyboard_submits_hangul():
    kb = ScreenKeyboard()
    kb.open()
    for ch in "dkssud":
        assert kb.feed(ord(ch)) is None
    assert kb.buffer() == "안녕"
    assert kb.feed(13) == "안녕"
    assert kb.active is False


def test_screen_keyboard_escape_cancels():
    kb = ScreenKeyboard()
    kb.open()
    kb.feed(ord("a"))
    assert kb.feed(27) == ""
    assert kb.active is False
    assert kb.buffer() == ""


def test_screen_keyboard_tab_switches_to_english():
    kb = ScreenKeyboard()
    kb.open()
    assert kb.feed(9) is None
    assert kb.hangul is False
    kb.feed(ord("H"))
    kb.feed(ord("i"))
    assert kb.buffer() == "Hi"
