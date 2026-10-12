"""화면 위 사용법. H키로 열거나 닫는다."""
from __future__ import annotations

import cv2

from korean_font import put_text

_PANEL = (15, 12, 8)
_CYAN = (255, 220, 0)
_DIM = (190, 160, 40)

_HELP = """손짓 명령  (G키 마우스 제어는 꺼 둔 상태)
검지를 잠깐 유지하면 녹음이 시작됩니다.
자비스라고 부르지 않아도 됩니다.
검지와 중지로 브이를 유지하면
그 말을 명령으로 보냅니다.
손을 내리고 3초간 조용히 있어도
같은 방식으로 전송됩니다.

화면 키보드
Enter 또는 / 를 누르면
화면 아래에 입력 줄이 열립니다.
맥 입력기는 영어(ABC)로 두고,
키 자리는 한글 두벌식으로 칩니다.
예를 들어 dkssud 를 치면 안녕이 됩니다.
Tab은 한글/영문 전환, Enter는 전송,
Esc는 취소입니다.
맥 한글 입력기 그대로 치려면
T키 팝업을 쓰면 됩니다.

백그라운드
B키로 관심 미리받기, 뉴스, 지정가,
고래, 소셜, 주가, 캠퍼스, 깃허브를
켜거나 끌 수 있습니다.
관심 추가 엔비디아
뉴스 백그라운드 꺼

H키로 이 안내를 닫거나 다시 엽니다."""


def draw_help(frame, visible: bool):
    h, w = frame.shape[:2]
    if not visible:
        put_text(frame, "H  사용법", (w - 110, 36), size=14, color=_DIM)
        return frame

    x, y = max(16, w - 520), 58
    pw = min(500, w - x - 12)
    ph = min(560, h - y - 80)
    ov = frame.copy()
    cv2.rectangle(ov, (x, y), (x + pw, y + ph), _PANEL, -1)
    cv2.addWeighted(ov, 0.86, frame, 0.14, 0, frame)
    cv2.rectangle(frame, (x, y), (x + pw, y + ph), _CYAN, 1, cv2.LINE_AA)
    put_text(frame, "사용법", (x + 16, y + 28), size=18, color=_CYAN, bold=True)
    put_text(frame, _HELP, (x + 16, y + 52), size=15, color=(230, 240, 245))
    return frame
