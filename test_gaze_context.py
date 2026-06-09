"""
시선 기반 상황 인지(Gaze-Triggered Context) 회귀 테스트.

검증 항목:
  A. gaze_context: 영역 등록/히트, 3초 dwell 디바운스, 이벤트 발행, TTL 만료
  B. stock_quant._resolve_ticker: 지시어/빈 입력 폴백 + 기존 티커 해석 무변(회귀 방지)
실행: .venv/bin/python test_gaze_context.py
"""
import sys

from gaze_context import GazeContext, get_gaze_context

PASS, FAIL = 0, 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")


print("[A] gaze_context — dwell 디바운스 / 이벤트 / TTL")
ctx = GazeContext(dwell_seconds=3.0, ttl_seconds=90.0)
events = []
ctx.subscribe(lambda p: events.append(p))
ctx.register_region("panel_samsung", (1000, 60, 1320, 300), "005930.KS", "삼성전자")

# 가상 시계로 결정론적 dwell 테스트
t = 1000.0
check("응시 직후(0s)에는 미확정", ctx.update_gaze(1100, 120, now=t) is None)
check("2.9초에는 아직 미확정", ctx.update_gaze(1100, 120, now=t + 2.9) is None)
fired = ctx.update_gaze(1100, 120, now=t + 3.0)
check("3.0초 응시 → 포커스 확정 반환", fired == "005930.KS")
check("current_focused_ticker == 005930.KS",
      ctx.get_focused_ticker(now=t + 3.0) == "005930.KS")
check("GazeFocusedTickerChanged 이벤트 1회 발행", len(events) == 1)
check("이벤트 페이로드 ticker 정확", events and events[0]["ticker"] == "005930.KS")
check("이벤트 타입 정확",
      events and events[0]["event"] == "GazeFocusedTickerChanged")

# 영역 밖 응시는 포커스를 즉시 지우지 않음(TTL 까지 유지)
ctx.update_gaze(10, 10, now=t + 4.0)
check("영역 밖 응시 후에도 포커스 유지(TTL 내)",
      ctx.get_focused_ticker(now=t + 4.0) == "005930.KS")

# 같은 종목 재응시는 중복 이벤트를 만들지 않음
ctx.register_region("panel_samsung", (1000, 60, 1320, 300), "005930.KS", "삼성전자")
ctx.update_gaze(1100, 120, now=t + 5.0)
ctx.update_gaze(1100, 120, now=t + 9.0)
check("동일 종목 재확정은 이벤트 중복 없음", len(events) == 1)

# 다른 종목으로 전환
ctx.register_region("panel_hynix", (600, 60, 920, 300), "000660.KS", "SK하이닉스")
ctx.update_gaze(700, 120, now=t + 20.0)
ctx.update_gaze(700, 120, now=t + 23.0)
check("다른 패널 3초 응시 → 종목 전환",
      ctx.get_focused_ticker(now=t + 23.0) == "000660.KS")
check("전환 시 새 이벤트 발행", len(events) == 2)

# TTL 만료
check("TTL 초과(now=+200s) 이면 만료로 None",
      ctx.get_focused_ticker(now=t + 223.0) is None)

# 영역 미등록 좌표는 None
ctx2 = GazeContext()
check("빈 레지스트리에서 update_gaze None", ctx2.update_gaze(5, 5) is None)

print("\n[B] stock_quant._resolve_ticker — 폴백 & 회귀")
from stock_quant import _resolve_ticker, _is_deictic  # noqa: E402

g = get_gaze_context()  # 프로세스 전역 싱글톤 (stock_quant 가 참조)
g.clear_focus()

# 포커스 없을 때: 폴백 미발동 → 기존 해석 경로 그대로(빈 입력은 빈 문자열)
check("포커스 없을 때 빈 입력 → '' (폴백 미발동)", _resolve_ticker("") == "")
check("포커스 없을 때 지시어도 크래시 없이 str 반환",
      isinstance(_resolve_ticker("이거"), str))
check("정상 티커 'AAPL' → 'AAPL'", _resolve_ticker("AAPL") == "AAPL")

# 포커스 설정
g.set_focused_ticker("005930.KS", "삼성전자", source="test")
check("지시어 '이거' → 포커스 005930.KS", _resolve_ticker("이거") == "005930.KS")
check("지시어 '이 종목'(공백) → 포커스", _resolve_ticker("이 종목") == "005930.KS")
check("빈 입력 '' → 포커스", _resolve_ticker("") == "005930.KS")
check("영어 지시어 'this' → 포커스", _resolve_ticker("this") == "005930.KS")
# 회귀: 구체적 종목명/티커는 포커스가 있어도 절대 영향받지 않아야 함
check("회귀: 'AAPL' 은 포커스 무시하고 'AAPL'", _resolve_ticker("AAPL") == "AAPL")
check("회귀: '005930.KS' 는 그대로", _resolve_ticker("005930.KS") == "005930.KS")
check("회귀: '000660.KS' 는 그대로", _resolve_ticker("000660.KS") == "000660.KS")
check("회귀: 지수 '^TNX' 그대로", _resolve_ticker("^TNX") == "^TNX")

check("_is_deictic('이거') True", _is_deictic("이거") is True)
check("_is_deictic('삼성전자') False", _is_deictic("삼성전자") is False)
check("_is_deictic('AAPL') False", _is_deictic("AAPL") is False)

g.clear_focus()

print(f"\n결과: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
