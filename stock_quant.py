"""
J.A.R.V.I.S — 금융 퀀트 분석 에이전트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
14개 기술적 지표 + 재무 펀더멘탈 + 하이브리드 스코어링
+ 예수금 기반 포트폴리오 사이징

사용법:
  result = analyze_stock_full_package("005930.KS", 5_000_000)
  result = analyze_stock_full_package("AAPL", 10_000)

의존성: pip install yfinance pandas numpy
"""

from __future__ import annotations

import difflib
import json
import logging
import math
import unicodedata
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

try:
    import yfinance as yf
    YF_AVAILABLE = True
    # 야후 조회 실패(404 / delisted) 시 터미널을 도배하는 노이즈 로그 억제
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
except ImportError:
    YF_AVAILABLE = False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  0. 매크로 마켓 가드레일
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MACRO_WATCH_TICKERS = {
    "^TNX": {"name": "미국 10년물 금리", "unit": "%", "danger": 4.8, "warning": 4.5},
    "DX-Y.NYB": {"name": "달러 인덱스", "unit": "pt", "danger": 106.0, "warning": 103.0},
    "^VIX": {"name": "VIX 공포지수", "unit": "pt", "danger": 25.0, "warning": 20.0},
    "USDKRW=X": {"name": "원달러 환율", "unit": "원", "danger": 1400.0, "warning": 1350.0},
}

MACRO_RISK_MULTIPLIER = {
    "안정": 1.0,
    "경계": 0.75,
    "위험": 0.50,
}

_MACRO_CACHE = {"ts": None, "data": None}
_MACRO_CACHE_TTL_SECONDS = 300


def _fetch_macro_indicator(ticker: str) -> dict | None:
    """매크로 지표의 현재값과 최근 1개월 변화율을 가져온다."""
    if not YF_AVAILABLE:
        return None
    try:
        hist = yf.Ticker(ticker).history(period="1mo", auto_adjust=True)
        if hist is None or hist.empty:
            return None
        close = hist["Close"].dropna()
        if close.empty:
            return None
        current = float(close.iloc[-1])
        base = float(close.iloc[0])
        change_pct = ((current / base - 1) * 100) if base else 0.0
        return {
            "ticker": ticker,
            "name": MACRO_WATCH_TICKERS[ticker]["name"],
            "value": round(current, 2),
            "unit": MACRO_WATCH_TICKERS[ticker]["unit"],
            "change_pct_1m": round(change_pct, 2),
        }
    except Exception:
        return None


def _classify_macro_risk(indicators: dict) -> tuple[str, list[str]]:
    """매크로 지표 기반 시장 위험 등급(안정/경계/위험)과 사유 산출."""
    reasons = []
    risk_points = 0

    vix = indicators.get("^VIX", {}).get("value")
    usdkrw = indicators.get("USDKRW=X", {}).get("value")
    tnx = indicators.get("^TNX", {}).get("value")
    dxy = indicators.get("DX-Y.NYB", {}).get("value")

    # 명시적 위험 가이드라인: VIX>25 또는 USDKRW>1400이면 즉시 위험.
    if vix is not None and vix > MACRO_WATCH_TICKERS["^VIX"]["danger"]:
        reasons.append(f"VIX {vix:.1f}로 공포 구간 진입")
        risk_points += 3
    elif vix is not None and vix > MACRO_WATCH_TICKERS["^VIX"]["warning"]:
        reasons.append(f"VIX {vix:.1f}로 변동성 확대")
        risk_points += 1

    if usdkrw is not None and usdkrw > MACRO_WATCH_TICKERS["USDKRW=X"]["danger"]:
        reasons.append(f"원달러 환율 {usdkrw:,.0f}원으로 1400원 상회")
        risk_points += 3
    elif usdkrw is not None and usdkrw > MACRO_WATCH_TICKERS["USDKRW=X"]["warning"]:
        reasons.append(f"원달러 환율 {usdkrw:,.0f}원으로 달러 강세")
        risk_points += 1

    if tnx is not None and tnx > MACRO_WATCH_TICKERS["^TNX"]["danger"]:
        reasons.append(f"미 10년물 금리 {tnx:.2f}%로 고금리 부담")
        risk_points += 2
    elif tnx is not None and tnx > MACRO_WATCH_TICKERS["^TNX"]["warning"]:
        reasons.append(f"미 10년물 금리 {tnx:.2f}%로 경계 구간")
        risk_points += 1

    if dxy is not None and dxy > MACRO_WATCH_TICKERS["DX-Y.NYB"]["danger"]:
        reasons.append(f"달러 인덱스 {dxy:.1f}로 위험자산 압박")
        risk_points += 2
    elif dxy is not None and dxy > MACRO_WATCH_TICKERS["DX-Y.NYB"]["warning"]:
        reasons.append(f"달러 인덱스 {dxy:.1f}로 달러 강세")
        risk_points += 1

    if risk_points >= 3:
        return "위험", reasons or ["매크로 위험 지표가 위험 구간입니다."]
    if risk_points >= 1:
        return "경계", reasons
    return "안정", ["주요 매크로 위험 지표가 안정권입니다."]


def get_macro_market_status(force_refresh: bool = False) -> dict:
    """
    ^TNX, DX-Y.NYB, ^VIX, USDKRW=X 매크로 스냅샷을 수집하고
    시장 위험 등급과 포지션 배분 감축 배수를 계산한다.
    """
    now = datetime.now()
    if (not force_refresh and _MACRO_CACHE["data"] is not None and _MACRO_CACHE["ts"] and
            (now - _MACRO_CACHE["ts"]).total_seconds() < _MACRO_CACHE_TTL_SECONDS):
        return _MACRO_CACHE["data"]

    if not YF_AVAILABLE:
        return {
            "success": False,
            "message": "yfinance 미설치로 매크로 지표를 수집할 수 없습니다.",
            "risk_level": "안정",
            "allocation_multiplier": 1.0,
            "guardrail_active": False,
            "indicators": {},
        }

    indicators = {}
    for ticker in MACRO_WATCH_TICKERS:
        item = _fetch_macro_indicator(ticker)
        if item:
            indicators[ticker] = item

    if not indicators:
        return {
            "success": False,
            "message": "매크로 지표 수집 실패. 기존 투자 로직을 유지합니다.",
            "risk_level": "안정",
            "allocation_multiplier": 1.0,
            "guardrail_active": False,
            "indicators": {},
        }

    risk_level, reasons = _classify_macro_risk(indicators)
    multiplier = MACRO_RISK_MULTIPLIER[risk_level]
    guardrail_active = multiplier < 1.0

    rows = []
    for ticker, meta in MACRO_WATCH_TICKERS.items():
        item = indicators.get(ticker)
        if not item:
            rows.append([meta["name"], "수집실패", "-", "="])
            continue
        unit = item["unit"]
        value = item["value"]
        value_str = f"{value:,.0f}원" if unit == "원" else f"{value:.2f}{unit if unit == '%' else ''}"
        chg = item["change_pct_1m"]
        sig = "▼" if value >= meta["danger"] else ("-" if value >= meta["warning"] else "=")
        rows.append([meta["name"], value_str, f"{chg:+.2f}%", sig])

    overlay_table = {
        "type": "table",
        "title": f"MACRO GUARDRAIL | {risk_level}",
        "headers": ["지표", "현재", "1M", "위험"],
        "rows": rows,
    }

    if guardrail_active:
        briefing = (
            "현재 공포 지수 변동성 확대로 인해 매크로 가드레일 안전장치가 가동되었습니다. "
            f"리스크 관리를 위해 권장 진입 비중을 {int((1 - multiplier) * 100)}% 하향 조정합니다."
        )
    else:
        briefing = "현재 주요 매크로 지표는 안정권입니다. 매크로 가드레일은 정상 감시 모드로 유지됩니다."

    data = {
        "success": True,
        "risk_level": risk_level,
        "allocation_multiplier": multiplier,
        "guardrail_active": guardrail_active,
        "reasons": reasons,
        "indicators": indicators,
        "overlay_table": overlay_table,
        "briefing": briefing,
    }
    _MACRO_CACHE["ts"] = now
    _MACRO_CACHE["data"] = data
    return data


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. 14개 기술적 지표 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def calc_sma(series: pd.Series, window: int) -> pd.Series:
    """단순 이동평균"""
    return series.rolling(window=window, min_periods=1).mean()


def calc_ema(series: pd.Series, span: int) -> pd.Series:
    """지수 이동평균"""
    return series.ewm(span=span, adjust=False).mean()


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI (Relative Strength Index)"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_macd(close: pd.Series, fast=12, slow=26, signal=9):
    """MACD (이동평균 수렴/확산)"""
    ema_fast = calc_ema(close, fast)
    ema_slow = calc_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calc_stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
                    k_period=14, d_period=3):
    """스토캐스틱 오실레이터"""
    lowest = low.rolling(k_period, min_periods=1).min()
    highest = high.rolling(k_period, min_periods=1).max()
    denom = (highest - lowest).replace(0, np.nan)
    k = 100 * (close - lowest) / denom
    d = k.rolling(d_period, min_periods=1).mean()
    return k, d


def calc_bollinger(close: pd.Series, window=20, num_std=2):
    """볼린저 밴드"""
    sma = calc_sma(close, window)
    std = close.rolling(window=window, min_periods=1).std()
    upper = sma + num_std * std
    lower = sma - num_std * std
    return upper, sma, lower


def calc_ichimoku(high: pd.Series, low: pd.Series, close: pd.Series):
    """일목균형표 (전환선, 기준선, 선행스팬A/B, 후행스팬)"""
    tenkan = (high.rolling(9, min_periods=1).max() + low.rolling(9, min_periods=1).min()) / 2
    kijun  = (high.rolling(26, min_periods=1).max() + low.rolling(26, min_periods=1).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(26)
    senkou_b = ((high.rolling(52, min_periods=1).max() + low.rolling(52, min_periods=1).min()) / 2).shift(26)
    chikou = close.shift(-26)
    return tenkan, kijun, senkou_a, senkou_b, chikou


def calc_adx(high: pd.Series, low: pd.Series, close: pd.Series, period=14):
    """ADX (Average Directional Index)"""
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    mask = plus_dm < minus_dm
    plus_dm[mask] = 0
    mask2 = minus_dm < plus_dm
    minus_dm[mask2] = 0

    tr = calc_atr(high, low, close, period) * period  # True Range sum
    atr_s = calc_atr(high, low, close, period)

    plus_di = 100 * calc_ema(plus_dm, period) / atr_s.replace(0, np.nan)
    minus_di = 100 * calc_ema(minus_dm, period) / atr_s.replace(0, np.nan)
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
    adx = calc_ema(dx, period)
    return adx, plus_di, minus_di


def calc_parabolic_sar(high: pd.Series, low: pd.Series, af_start=0.02, af_step=0.02, af_max=0.2):
    """Parabolic SAR"""
    length = len(high)
    sar = np.zeros(length)
    trend = np.ones(length)  # 1=상승, -1=하락
    ep = np.zeros(length)
    af = np.full(length, af_start)

    sar[0] = low.iloc[0]
    ep[0] = high.iloc[0]

    for i in range(1, length):
        sar[i] = sar[i-1] + af[i-1] * (ep[i-1] - sar[i-1])

        if trend[i-1] == 1:
            sar[i] = min(sar[i], low.iloc[i-1])
            if i >= 2:
                sar[i] = min(sar[i], low.iloc[i-2])
            if low.iloc[i] < sar[i]:
                trend[i] = -1
                sar[i] = ep[i-1]
                ep[i] = low.iloc[i]
                af[i] = af_start
            else:
                trend[i] = 1
                if high.iloc[i] > ep[i-1]:
                    ep[i] = high.iloc[i]
                    af[i] = min(af[i-1] + af_step, af_max)
                else:
                    ep[i] = ep[i-1]
                    af[i] = af[i-1]
        else:
            sar[i] = max(sar[i], high.iloc[i-1])
            if i >= 2:
                sar[i] = max(sar[i], high.iloc[i-2])
            if high.iloc[i] > sar[i]:
                trend[i] = 1
                sar[i] = ep[i-1]
                ep[i] = high.iloc[i]
                af[i] = af_start
            else:
                trend[i] = -1
                if low.iloc[i] < ep[i-1]:
                    ep[i] = low.iloc[i]
                    af[i] = min(af[i-1] + af_step, af_max)
                else:
                    ep[i] = ep[i-1]
                    af[i] = af[i-1]

    return pd.Series(sar, index=high.index), pd.Series(trend, index=high.index)


def calc_momentum(close: pd.Series, period=10) -> pd.Series:
    """모멘텀"""
    return close - close.shift(period)


def calc_williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period=14):
    """Williams %R"""
    highest = high.rolling(period, min_periods=1).max()
    lowest = low.rolling(period, min_periods=1).min()
    denom = (highest - lowest).replace(0, np.nan)
    return -100 * (highest - close) / denom


def calc_cci(high: pd.Series, low: pd.Series, close: pd.Series, period=20):
    """CCI (Commodity Channel Index)"""
    tp = (high + low + close) / 3
    sma_tp = calc_sma(tp, period)
    mad = tp.rolling(period, min_periods=1).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    return (tp - sma_tp) / (0.015 * mad.replace(0, np.nan))


def calc_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """OBV (On-Balance Volume)"""
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


def calc_vwma(close: pd.Series, volume: pd.Series, period=20) -> pd.Series:
    """VWMA (Volume Weighted Moving Average)"""
    vp = close * volume
    return vp.rolling(period, min_periods=1).sum() / volume.rolling(period, min_periods=1).sum()


def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series, period=14) -> pd.Series:
    """ATR (Average True Range)"""
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=1).mean()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. 데이터 수집
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 한글 종목명 → 야후 티커 정적 매핑 (자주 쓰는 종목)
_KR_TICKER_MAP = {
    "삼성전자": "005930.KS", "SK하이닉스": "000660.KS",
    "네이버": "035420.KS", "NAVER": "035420.KS", "카카오": "035720.KS",
    "현대차": "005380.KS", "현대자동차": "005380.KS", "기아": "000270.KS",
    "LG에너지솔루션": "373220.KS", "셀트리온": "068270.KS",
    "삼성SDI": "006400.KS", "포스코홀딩스": "005490.KS", "POSCO홀딩스": "005490.KS",
    "삼성바이오로직스": "207940.KS", "LG화학": "051910.KS",
    "현대모비스": "012330.KS", "KB금융": "105560.KS",
    "신한지주": "055550.KS", "삼성물산": "028260.KS",
    "하나금융지주": "086790.KS", "삼성생명": "032830.KS",
    "LG전자": "066570.KS", "한국전력": "015760.KS",
    "카카오뱅크": "323410.KS", "카카오페이": "377300.KS",
    "크래프톤": "259960.KS", "엔씨소프트": "036570.KS", "넷마블": "251270.KS",
    "두산에너빌리티": "034020.KS", "한화에어로스페이스": "012450.KS",
    "삼성전기": "009150.KS", "LG디스플레이": "034220.KS",
    "SK이노베이션": "096770.KS", "S-Oil": "010950.KS", "에스오일": "010950.KS",
    "기업은행": "024110.KS", "우리금융지주": "316140.KS",
    "메리츠금융지주": "138040.KS", "미래에셋증권": "006800.KS",
    "삼성증권": "016360.KS", "키움증권": "039490.KS", "NH투자증권": "005940.KS",
    "한국금융지주": "071050.KS", "한국투자증권": "071050.KS",
    "에코프로": "086520.KQ", "에코프로비엠": "247540.KQ",
    "알테오젠": "196170.KQ", "HLB": "028300.KQ", "리노공업": "058470.KQ",
    "JYP": "035900.KQ", "JYP엔터": "035900.KQ", "에스엠": "041510.KQ",
    "하이브": "352820.KS", "HYBE": "352820.KS",
    # 자주 부르는 해외 종목 한글명 (지정가 알림/음성 호출용)
    "테슬라": "TSLA", "애플": "AAPL", "엔비디아": "NVDA", "마이크로소프트": "MSFT",
    "마소": "MSFT", "구글": "GOOGL", "알파벳": "GOOGL", "아마존": "AMZN",
    "메타": "META", "넷플릭스": "NFLX", "팔란티어": "PLTR", "브로드컴": "AVGO",
    "인텔": "INTC", "디즈니": "DIS", "코카콜라": "KO", "스타벅스": "SBUX",
}

# 음성인식(STT)이 자주 잘못 듣는 종목명 보정
_KR_STT_ALIASES = {
    "기융중권": "키움증권", "기윰증권": "키움증권", "키움중권": "키움증권",
    "키움전권": "키움증권", "키움증원": "키움증권",
    "삼송전자": "삼성전자", "삼성전저": "삼성전자",
    "하이닉스": "SK하이닉스", "에스케이하이닉스": "SK하이닉스",
    "네이바": "네이버", "카카오뱅": "카카오뱅크",
}


def _is_hangul(text: str) -> bool:
    return any("\uac00" <= ch <= "\ud7a3" for ch in text)


def _phonetic_ratio(a: str, b: str) -> float:
    """한글 자모(NFD) 단위 유사도 — 음성인식 오류 보정용"""
    na = unicodedata.normalize("NFD", a)
    nb = unicodedata.normalize("NFD", b)
    plain = difflib.SequenceMatcher(None, a, b).ratio()
    jamo = difflib.SequenceMatcher(None, na, nb).ratio()
    return max(plain, jamo)


def _fuzzy_kr_name(query: str, cutoff: float = 0.6) -> str | None:
    """정적 매핑 키 중에서 발음이 가장 가까운 종목명을 찾는다."""
    best_name, best_score = None, 0.0
    for name in _KR_TICKER_MAP:
        if not _is_hangul(name):
            continue
        score = _phonetic_ratio(query, name)
        if score > best_score:
            best_name, best_score = name, score
    return best_name if best_score >= cutoff else None


# yfinance 검색 결과 캐시 (질의 → 티커)
_SEARCH_CACHE: dict[str, str | None] = {}


def _resolve_via_search(query: str) -> str | None:
    """yfinance 퍼지 검색으로 임의의 종목명을 티커로 해석한다."""
    if not YF_AVAILABLE:
        return None
    if query in _SEARCH_CACHE:
        return _SEARCH_CACHE[query]

    symbol = None
    try:
        quotes = yf.Search(query, max_results=8, enable_fuzzy_query=True,
                           news_count=0, lists_count=0).quotes
        is_kr = _is_hangul(query)
        if is_kr:
            # 한글 질의면 한국거래소(.KS/.KQ) 종목을 우선
            for q in quotes:
                sym = q.get("symbol", "")
                if sym.endswith(".KS") or sym.endswith(".KQ"):
                    symbol = sym
                    break
        if symbol is None and quotes:
            symbol = quotes[0].get("symbol") or None
    except Exception:
        symbol = None

    _SEARCH_CACHE[query] = symbol
    return symbol


# 구체적 종목명 없이 "지금 보는 그것"을 가리키는 지시대명사(STT 변형 포함).
# 공백 제거·소문자화 후 정확 일치로만 판정 → 일반 티커/종목명과 충돌하지 않음.
_DEICTIC_TERMS = frozenset({
    "이거", "이거어때", "이종목", "이주식", "이회사", "요거", "요종목",
    "그거", "저거", "방금거", "방금그거", "지금보는거", "지금보는종목",
    "현재종목", "이것", "이것좀", "화면", "this", "thisone", "it",
})


def _is_deictic(query: str) -> bool:
    """입력이 '구체적 종목명 없는 지시어'인지 판정."""
    norm = (query or "").strip().lower().replace(" ", "")
    return norm in _DEICTIC_TERMS


def _resolve_ticker(query: str) -> str:
    """종목명/티커 → 야후 티커 변환 (한글명·오타·음성인식 오류 보정 포함)

    0순위 폴백: 입력에 구체적 종목명이 없고(빈 입력 또는 지시대명사) 시선 컨텍스트에
    current_focused_ticker 가 매핑돼 있으면 이를 최우선으로 사용한다(시선 기반 상황 인지).
    """
    q = (query or "").strip()

    # 0) 시선 컨텍스트 폴백 — 구체적 종목명이 없을 때만 발동(기존 티커 해석 경로 불변)
    if (not q) or _is_deictic(q):
        try:
            from gaze_context import get_gaze_context
            focused = get_gaze_context().get_focused_ticker()
        except Exception:
            focused = None
        if focused:
            return focused

    if not q:
        return q

    # 1) 지수(^TNX 등)·이미 형식을 갖춘 티커는 그대로
    if q.startswith("^") or "." in q or "=" in q or "-" in q:
        return q

    # 2) 6자리 숫자 종목코드 → 한국거래소 티커
    if q.isdigit() and len(q) == 6:
        return f"{q}.KS"

    # 3) STT 오인식 보정
    q = _KR_STT_ALIASES.get(q, q)

    # 4) 정적 매핑 정확 일치
    if q in _KR_TICKER_MAP:
        return _KR_TICKER_MAP[q]

    # 5) 영문 티커(공백 없는 ASCII 알파벳)는 대문자로 정규화 후 사용
    if q.isascii() and q.replace(" ", "").isalpha() and " " not in q and len(q) <= 6:
        return q.upper()

    # 6) 한글명이면 발음 유사도로 정적 매핑 재시도(음성인식 오류 보정)
    if _is_hangul(q):
        fuzzy = _fuzzy_kr_name(q)
        if fuzzy:
            return _KR_TICKER_MAP[fuzzy]

    # 7) 야후 퍼지 검색으로 동적 해석
    found = _resolve_via_search(q)
    if found:
        return found

    # 8) 끝까지 못 찾으면 원본 반환(상위에서 친절한 오류 메시지 처리)
    return q


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  종목명 오인식 보정 — 후보 추천 / 신뢰도 판정 (지정가 알림용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _preferred_names() -> dict:
    """동일 티커에 여러 표시명이 걸린 경우(NAVER/네이버 등) 한글명을 우선 노출."""
    pref = {}
    for name, ticker in _KR_TICKER_MAP.items():
        cur = pref.get(ticker)
        if cur is None or (not _is_hangul(cur) and _is_hangul(name)):
            pref[ticker] = name
    return pref


def suggest_stock_candidates(query: str, n: int = 5) -> list:
    """발음 유사도 상위 n개 종목 후보 반환. 항목: {name, ticker, score}. 동일 티커는 1회만."""
    q = (query or "").strip()
    if not q:
        return []
    q = _KR_STT_ALIASES.get(q, q)
    pref = _preferred_names()
    best = {}  # ticker → 최고 점수
    for name, ticker in _KR_TICKER_MAP.items():
        score = _phonetic_ratio(q, name)
        if score > best.get(ticker, -1.0):
            best[ticker] = score
    ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
    return [{"name": pref.get(t, t), "ticker": t, "score": round(s, 3)} for t, s in ranked[:n]]


def resolve_alert_query(query: str, strong_cutoff: float = 0.86) -> dict:
    """지정가 알림용 종목 해석. 신뢰 가능하면 바로, 모호하면 후보 목록 제공.
    반환: {confident: bool, ticker: str|None, name: str|None, candidates: [..]}"""
    q = (query or "").strip()
    if not q:
        return {"confident": False, "ticker": None, "name": None, "candidates": []}
    pref = _preferred_names()

    # 1) 형식을 갖춘 티커·지수·종목코드 → 신뢰
    if q.startswith("^") or "." in q or "=" in q or (q.isdigit() and len(q) == 6):
        t = _resolve_ticker(q)
        return {"confident": True, "ticker": t, "name": pref.get(t, t), "candidates": []}
    # 2) 영문 티커 → 신뢰
    if q.isascii() and q.replace(" ", "").isalpha() and len(q) <= 6:
        t = q.upper()
        return {"confident": True, "ticker": t, "name": pref.get(t, t), "candidates": []}
    # 3) STT 별칭/정확 매핑 → 신뢰
    qa = _KR_STT_ALIASES.get(q, q)
    if qa in _KR_TICKER_MAP:
        t = _KR_TICKER_MAP[qa]
        return {"confident": True, "ticker": t, "name": pref.get(t, qa), "candidates": []}
    # 4) 한글명이면 발음 유사도 — 강한 일치만 신뢰, 아니면 후보 제시
    if _is_hangul(qa):
        cands = suggest_stock_candidates(qa, n=5)
        if cands and cands[0]["score"] >= strong_cutoff:
            top = cands[0]
            return {"confident": True, "ticker": top["ticker"], "name": top["name"], "candidates": []}
        # 정적 매핑에 약하게만 걸리면 야후 검색으로 해외/신규 종목 확인
        found = _resolve_via_search(qa)
        if found and not any(c["ticker"] == found for c in cands):
            cands = [{"name": qa, "ticker": found, "score": 1.0}] + cands[:4]
        return {"confident": False, "ticker": None, "name": None, "candidates": cands}
    # 5) 영문/기타 → 야후 검색 시도
    found = _resolve_via_search(qa)
    if found:
        return {"confident": True, "ticker": found, "name": pref.get(found, qa), "candidates": []}
    return {"confident": False, "ticker": None, "name": None,
            "candidates": suggest_stock_candidates(qa, n=5)}


def fetch_stock_data(ticker: str, period="6mo"):
    """yfinance로 주가 + 재무 데이터 수집"""
    if not YF_AVAILABLE:
        return None, None, "yfinance 미설치. pip install yfinance"

    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period, auto_adjust=True)

        if df.empty:
            hint = ""
            if _is_hangul(ticker):
                near = difflib.get_close_matches(
                    ticker, list(_KR_TICKER_MAP.keys()), n=3, cutoff=0.3
                )
                if near:
                    hint = f" 혹시 다음 종목을 말씀하셨나요? {', '.join(near)}"
            return None, None, f"'{ticker}' 종목을 찾지 못했습니다. 종목명이나 티커를 확인해 주세요.{hint}"

        # 재무 정보
        info = stock.info or {}
        fundamentals = {
            "name": info.get("shortName", info.get("longName", ticker)),
            "currency": info.get("currency", ""),
            "price": info.get("currentPrice", info.get("regularMarketPrice", 0)),
            "market_cap": info.get("marketCap", 0),
            "per": info.get("trailingPE", info.get("forwardPE", None)),
            "pbr": info.get("priceToBook", None),
            "roe": (info.get("returnOnEquity", 0) or 0) * 100,
            "eps": info.get("trailingEps", None),
            "dividend_yield": (info.get("dividendYield", 0) or 0) * 100,
            "debt_to_equity": info.get("debtToEquity", None),
            "revenue_growth": (info.get("revenueGrowth", 0) or 0) * 100,
            "profit_margin": (info.get("profitMargins", 0) or 0) * 100,
            "52w_high": info.get("fiftyTwoWeekHigh", 0),
            "52w_low": info.get("fiftyTwoWeekLow", 0),
            "avg_volume": info.get("averageVolume", 0),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            # 로컬 DCF·Graham 입력
            "fcf": info.get("freeCashflow"),
            "total_debt": info.get("totalDebt"),
            "cash": info.get("totalCash") or info.get("cash"),
            "shares_outstanding": info.get("sharesOutstanding"),
            "fcf_growth": (info.get("revenueGrowth", 0) or 0) * 100,
            "earnings_growth": (info.get("earningsGrowth", 0) or 0) * 100
            if info.get("earningsGrowth") is not None else None,
        }

        return df, fundamentals, None

    except Exception as e:
        return None, None, f"데이터 수집 오류: {e}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. 기술적 지표 스냅샷 분석
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def compute_technical_snapshot(df: pd.DataFrame) -> dict:
    """최신 날짜 기준 14개 지표 계산 및 시그널 판별"""
    c = df["Close"]
    h = df["High"]
    l = df["Low"]
    v = df["Volume"]

    # ── 지표 계산 ──
    rsi = calc_rsi(c)
    macd_line, macd_signal, macd_hist = calc_macd(c)
    stoch_k, stoch_d = calc_stochastic(h, l, c)
    bb_upper, bb_mid, bb_lower = calc_bollinger(c)
    tenkan, kijun, senkou_a, senkou_b, chikou = calc_ichimoku(h, l, c)
    adx, plus_di, minus_di = calc_adx(h, l, c)
    sar, sar_trend = calc_parabolic_sar(h, l)
    momentum = calc_momentum(c)
    williams = calc_williams_r(h, l, c)
    cci = calc_cci(h, l, c)
    obv = calc_obv(c, v)
    vwma = calc_vwma(c, v)
    atr = calc_atr(h, l, c)
    sma_20 = calc_sma(c, 20)
    sma_50 = calc_sma(c, 50)
    sma_200 = calc_sma(c, 200)

    # ── 최신값 추출 ──
    idx = -1
    latest = {
        "price": _safe(c, idx),
        "rsi": _safe(rsi, idx),
        "macd": _safe(macd_line, idx),
        "macd_signal": _safe(macd_signal, idx),
        "macd_hist": _safe(macd_hist, idx),
        "macd_prev_hist": _safe(macd_hist, -2),
        "stoch_k": _safe(stoch_k, idx),
        "stoch_d": _safe(stoch_d, idx),
        "bb_upper": _safe(bb_upper, idx),
        "bb_lower": _safe(bb_lower, idx),
        "bb_mid": _safe(bb_mid, idx),
        "adx": _safe(adx, idx),
        "plus_di": _safe(plus_di, idx),
        "minus_di": _safe(minus_di, idx),
        "sar": _safe(sar, idx),
        "sar_trend": _safe(sar_trend, idx),
        "momentum": _safe(momentum, idx),
        "williams_r": _safe(williams, idx),
        "cci": _safe(cci, idx),
        "obv": _safe(obv, idx),
        "obv_prev": _safe(obv, -2),
        "vwma": _safe(vwma, idx),
        "atr": _safe(atr, idx),
        "sma_20": _safe(sma_20, idx),
        "sma_50": _safe(sma_50, idx),
        "sma_200": _safe(sma_200, idx),
        "tenkan": _safe(tenkan, idx),
        "kijun": _safe(kijun, idx),
    }

    # ── 시그널 판별 ──
    signals = {}
    price = latest["price"]

    # RSI
    if latest["rsi"] < 30:
        signals["RSI"] = ("과매도", "BUY")
    elif latest["rsi"] > 70:
        signals["RSI"] = ("과매수", "SELL")
    else:
        signals["RSI"] = ("중립", "NEUTRAL")

    # MACD 크로스
    if latest["macd_hist"] > 0 and latest["macd_prev_hist"] <= 0:
        signals["MACD"] = ("골든크로스", "BUY")
    elif latest["macd_hist"] < 0 and latest["macd_prev_hist"] >= 0:
        signals["MACD"] = ("데드크로스", "SELL")
    elif latest["macd_hist"] > 0:
        signals["MACD"] = ("상승세", "BUY")
    else:
        signals["MACD"] = ("하락세", "SELL")

    # 스토캐스틱
    if latest["stoch_k"] < 20:
        signals["Stochastic"] = ("과매도", "BUY")
    elif latest["stoch_k"] > 80:
        signals["Stochastic"] = ("과매수", "SELL")
    else:
        signals["Stochastic"] = ("중립", "NEUTRAL")

    # 볼린저 밴드
    if price <= latest["bb_lower"]:
        signals["Bollinger"] = ("하단 터치", "BUY")
    elif price >= latest["bb_upper"]:
        signals["Bollinger"] = ("상단 터치", "SELL")
    else:
        signals["Bollinger"] = ("밴드 내", "NEUTRAL")

    # 일목균형표
    if latest["tenkan"] > latest["kijun"]:
        signals["Ichimoku"] = ("전환>기준", "BUY")
    else:
        signals["Ichimoku"] = ("기준>전환", "SELL")

    # ADX
    if latest["adx"] > 25 and latest["plus_di"] > latest["minus_di"]:
        signals["ADX"] = (f"강한 상승 ({latest['adx']:.0f})", "BUY")
    elif latest["adx"] > 25 and latest["minus_di"] > latest["plus_di"]:
        signals["ADX"] = (f"강한 하락 ({latest['adx']:.0f})", "SELL")
    else:
        signals["ADX"] = (f"약세장 ({latest['adx']:.0f})", "NEUTRAL")

    # Parabolic SAR
    if latest["sar_trend"] == 1:
        signals["SAR"] = ("주가>SAR 상승", "BUY")
    else:
        signals["SAR"] = ("주가<SAR 하락", "SELL")

    # 모멘텀
    signals["Momentum"] = ("상승" if latest["momentum"] > 0 else "하락",
                           "BUY" if latest["momentum"] > 0 else "SELL")

    # Williams %R
    if latest["williams_r"] < -80:
        signals["Williams%R"] = ("과매도", "BUY")
    elif latest["williams_r"] > -20:
        signals["Williams%R"] = ("과매수", "SELL")
    else:
        signals["Williams%R"] = ("중립", "NEUTRAL")

    # CCI
    if latest["cci"] < -100:
        signals["CCI"] = ("과매도", "BUY")
    elif latest["cci"] > 100:
        signals["CCI"] = ("과매수", "SELL")
    else:
        signals["CCI"] = ("중립", "NEUTRAL")

    # OBV
    signals["OBV"] = ("상승" if latest["obv"] > latest["obv_prev"] else "하락",
                       "BUY" if latest["obv"] > latest["obv_prev"] else "SELL")

    # VWMA
    signals["VWMA"] = ("주가>VWMA" if price > latest["vwma"] else "주가<VWMA",
                        "BUY" if price > latest["vwma"] else "SELL")

    # 이동평균
    if price > latest["sma_20"] > latest["sma_50"]:
        signals["MA"] = ("정배열", "BUY")
    elif price < latest["sma_20"] < latest["sma_50"]:
        signals["MA"] = ("역배열", "SELL")
    else:
        signals["MA"] = ("혼조", "NEUTRAL")

    return {"values": latest, "signals": signals}


def _safe(series, idx):
    """안전하게 시리즈 값 추출"""
    try:
        v = series.iloc[idx]
        if pd.isna(v) or np.isinf(v):
            return 0
        return round(float(v), 2)
    except:
        return 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. 하이브리드 스코어링
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def compute_hybrid_score(fundamentals: dict, tech: dict) -> dict:
    """기본+기술 통합 점수 (0~100점)"""
    score = 50  # 기본 점수
    reasons = []
    signals = tech["signals"]
    vals = tech["values"]

    # ── 펀더멘탈 가점/감점 ──
    roe = fundamentals.get("roe", 0)
    per = fundamentals.get("per")
    pbr = fundamentals.get("pbr")
    div_yield = fundamentals.get("dividend_yield", 0)
    debt_equity = fundamentals.get("debt_to_equity")
    rev_growth = fundamentals.get("revenue_growth", 0)
    profit_margin = fundamentals.get("profit_margin", 0)

    if roe >= 20:
        score += 12; reasons.append(f"ROE 우수 ({roe:.1f}%) +12")
    elif roe >= 15:
        score += 10; reasons.append(f"ROE 양호 ({roe:.1f}%) +10")
    elif roe >= 8:
        score += 3; reasons.append(f"ROE 보통 ({roe:.1f}%) +3")
    elif roe < 0:
        score -= 10; reasons.append(f"ROE 적자 ({roe:.1f}%) -10")

    if per is not None and per > 0:
        if per <= 10:
            score += 12; reasons.append(f"PER 저평가 ({per:.1f}) +12")
        elif per <= 15:
            score += 10; reasons.append(f"PER 양호 ({per:.1f}) +10")
        elif per <= 25:
            score += 3; reasons.append(f"PER 적정 ({per:.1f}) +3")
        elif per > 40:
            score -= 8; reasons.append(f"PER 고평가 ({per:.1f}) -8")

    if pbr is not None and 0 < pbr < 1:
        score += 5; reasons.append(f"PBR 저평가 ({pbr:.2f}) +5")

    if div_yield >= 3:
        score += 5; reasons.append(f"배당수익률 우수 ({div_yield:.1f}%) +5")

    if debt_equity is not None and debt_equity > 200:
        score -= 8; reasons.append(f"부채비율 위험 ({debt_equity:.0f}%) -8")

    if rev_growth > 20:
        score += 5; reasons.append(f"매출 고성장 ({rev_growth:.1f}%) +5")
    elif rev_growth < -10:
        score -= 5; reasons.append(f"매출 역성장 ({rev_growth:.1f}%) -5")

    if profit_margin > 20:
        score += 3; reasons.append(f"이익률 우수 ({profit_margin:.1f}%) +3")

    # ── 기술적 가점/감점 ──
    # RSI
    rsi_sig = signals.get("RSI", ("", "NEUTRAL"))[1]
    if rsi_sig == "BUY":
        score += 10; reasons.append(f"RSI 과매도 ({vals['rsi']:.0f}) +10")
    elif rsi_sig == "SELL":
        score -= 8; reasons.append(f"RSI 과매수 ({vals['rsi']:.0f}) -8")

    # MACD
    macd_sig = signals.get("MACD", ("", "NEUTRAL"))
    if macd_sig[0] == "골든크로스":
        score += 10; reasons.append("MACD 골든크로스 +10")
    elif macd_sig[0] == "데드크로스":
        score -= 10; reasons.append("MACD 데드크로스 -10")
    elif macd_sig[1] == "BUY":
        score += 3; reasons.append("MACD 상승세 +3")

    # SAR
    if signals.get("SAR", ("", "NEUTRAL"))[1] == "BUY":
        score += 5; reasons.append("SAR 상승추세 +5")
    else:
        score -= 3; reasons.append("SAR 하락추세 -3")

    # 볼린저
    bb_sig = signals.get("Bollinger", ("", "NEUTRAL"))[1]
    if bb_sig == "BUY":
        score += 5; reasons.append("볼린저 하단 터치 +5")
    elif bb_sig == "SELL":
        score -= 5; reasons.append("볼린저 상단 터치 -5")

    # ADX
    adx_sig = signals.get("ADX", ("", "NEUTRAL"))[1]
    if adx_sig == "BUY":
        score += 5; reasons.append("ADX 강한상승 +5")
    elif adx_sig == "SELL":
        score -= 5; reasons.append("ADX 강한하락 -5")

    # 일목균형표
    if signals.get("Ichimoku", ("", "NEUTRAL"))[1] == "BUY":
        score += 3; reasons.append("일목 전환>기준 +3")

    # 스토캐스틱
    stoch_sig = signals.get("Stochastic", ("", "NEUTRAL"))[1]
    if stoch_sig == "BUY":
        score += 5; reasons.append("스토캐스틱 과매도 +5")
    elif stoch_sig == "SELL":
        score -= 5; reasons.append("스토캐스틱 과매수 -5")

    # 이동평균 정배열
    if signals.get("MA", ("", "NEUTRAL"))[1] == "BUY":
        score += 5; reasons.append("MA 정배열 +5")
    elif signals.get("MA", ("", "NEUTRAL"))[1] == "SELL":
        score -= 5; reasons.append("MA 역배열 -5")

    # CCI
    if signals.get("CCI", ("", "NEUTRAL"))[1] == "BUY":
        score += 3; reasons.append("CCI 과매도 +3")

    # Williams %R
    if signals.get("Williams%R", ("", "NEUTRAL"))[1] == "BUY":
        score += 3; reasons.append("Williams%R 과매도 +3")

    # 클램프 0~100
    score = max(0, min(100, score))

    # 등급
    if score >= 75:
        grade = "STRONG BUY"
    elif score >= 60:
        grade = "BUY"
    elif score >= 45:
        grade = "HOLD"
    else:
        grade = "UNDERPERFORM"

    return {
        "score": score,
        "grade": grade,
        "reasons": reasons,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  5. 포트폴리오 사이징
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def compute_position_size(price: float, score: int, grade: str,
                          available_cash: float, currency: str = "",
                          macro_status: dict | None = None) -> dict:
    """등급별 자산 배분 + 매수 수량 계산"""
    # 리스크 기반 배분 비율
    alloc_map = {
        "STRONG BUY": 0.30,   # 30%
        "BUY": 0.15,          # 15%
        "HOLD": 0.0,          # 0%
        "UNDERPERFORM": 0.0,  # 0%
    }
    base_alloc_ratio = alloc_map.get(grade, 0.0)
    macro_multiplier = 1.0
    macro_risk_level = "안정"
    if macro_status:
        macro_multiplier = float(macro_status.get("allocation_multiplier", 1.0) or 1.0)
        macro_risk_level = macro_status.get("risk_level", "안정")

    alloc_ratio = base_alloc_ratio * macro_multiplier
    allocated = available_cash * alloc_ratio

    if price <= 0:
        shares = 0
        entry_cost = 0
    else:
        shares = int(allocated / price)
        entry_cost = shares * price

    is_krw = ".KS" in currency or ".KQ" in currency or currency == "KRW"

    return {
        "available_cash": f"{available_cash:,.0f}{'원' if is_krw else ''}",
        "base_allocation_pct": f"{base_alloc_ratio*100:.0f}%",
        "allocation_pct": f"{alloc_ratio*100:.0f}%",
        "allocation_multiplier": macro_multiplier,
        "macro_risk_level": macro_risk_level,
        "guardrail_active": macro_multiplier < 1.0 and base_alloc_ratio > 0,
        "allocated_amount": f"{allocated:,.0f}{'원' if is_krw else ''}",
        "recommended_shares": shares,
        "entry_cost": f"{entry_cost:,.0f}{'원' if is_krw else ''}",
        "remaining_cash": f"{available_cash - entry_cost:,.0f}{'원' if is_krw else ''}",
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  6. 메인 분석 함수
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def analyze_stock_full_package(ticker: str, available_cash: float = 0) -> dict:
    """
    종목 전체 분석 패키지

    Args:
        ticker: 종목코드 (한글 가능: "삼성전자", 또는 "AAPL")
        available_cash: 투자 가능 예수금

    Returns:
        dict with keys: success, name, ticker, fundamentals, tech_signals,
                        scoring, portfolio, briefing, display_data
    """
    resolved = _resolve_ticker(ticker)

    # 데이터 수집
    df, fund, error = fetch_stock_data(resolved)
    if error:
        return {"success": False, "message": error}

    price = fund.get("price", 0)
    if price <= 0 and not df.empty:
        price = float(df["Close"].iloc[-1])
        fund["price"] = price

    name = fund.get("name", resolved)

    # 기술적 분석
    tech = compute_technical_snapshot(df)

    # 하이브리드 스코어링
    scoring = compute_hybrid_score(fund, tech)

    # 매크로 가드레일 (종목 점수 자체는 보존, 진입 비중만 동적 감축)
    macro_status = get_macro_market_status()

    # 포트폴리오 사이징
    portfolio = {}
    if available_cash > 0:
        portfolio = compute_position_size(
            price, scoring["score"], scoring["grade"],
            available_cash, resolved, macro_status
        )

    # ── 시나리오 스트레스 테스트 (예상 MDD / 리스크 가중치) ──
    # 이미 수집한 df·macro_status·scoring 을 재사용하므로 추가 API 호출이 없고,
    # 내부 5분 TTL 캐시로 시뮬레이션 오버헤드를 억제한다(매트릭스 캐시 정책과 동일 철학).
    stress = {}
    try:
        from portfolio_stress import estimate_position_stress
        stress = estimate_position_stress(resolved, df=df, macro_status=macro_status,
                                          score=scoring.get("score"))
    except Exception as _e:
        stress = {}

    # ── 몬테카를로 기반 서사형 예측 가이드(forecast_briefing) — 경계/위험에서 트리거 ──
    forecast_briefing = ""
    risk_reweight = {}
    macro_risk = (macro_status or {}).get("risk_level", "안정")
    if macro_risk in ("경계", "위험"):
        try:
            import risk_engine as _re
            base_ratio = {"STRONG BUY": 0.30, "BUY": 0.15}.get(scoring.get("grade"), 0.0)
            if base_ratio > 0:
                _assess = _re.assess(resolved, df.get("Close"), macro_status,
                                     score=scoring.get("score"), base_ratio=base_ratio)
                risk_reweight = _assess.get("reweight", {})
                final_ratio = max(0.0, base_ratio - risk_reweight.get("haircut_pct", 0) / 100.0)
                forecast_briefing = _re.build_forecast_briefing(
                    name, _assess.get("stress", {}), risk_reweight,
                    base_pct=base_ratio * 100, final_pct=final_ratio * 100, macro_level=macro_risk,
                    high_vol_ticker=name if _assess.get("stress", {}).get("high_volatility") else "",
                )
        except Exception:
            forecast_briefing = ""

    # ── 브리핑 텍스트 생성 ──
    briefing = _generate_briefing(name, resolved, price, fund, scoring, portfolio, macro_status)
    if stress.get("summary_kr"):
        briefing = f"{briefing} {stress['summary_kr']}"

    # ── 화면 표시용 데이터 ──
    display_data = _generate_display_data(name, resolved, price, fund, tech, scoring, portfolio, macro_status)

    # 지표 표 음성 요약을 브리핑에 합쳐 "표를 말로 요약"하도록
    vs = display_data.get("voice_summary")
    if vs:
        briefing = f"{briefing} {vs}"

    return {
        "success": True,
        "name": name,
        "ticker": resolved,
        "price": price,
        "fundamentals": fund,
        "tech_signals": tech["signals"],
        "scoring": scoring,
        "portfolio": portfolio,
        "macro_status": macro_status,
        "stress_test": stress,
        "risk_reweight": risk_reweight,
        "forecast_briefing": forecast_briefing,
        "briefing": briefing,
        "display_data": display_data,
    }


def _generate_briefing(name, ticker, price, fund, scoring, portfolio, macro_status=None) -> str:
    """자비스 음성 브리핑 텍스트 생성 (문장 종결어미 포함)"""
    score = scoring["score"]
    grade = scoring["grade"]
    grade_kr = {"STRONG BUY": "강력 매수", "BUY": "매수",
                "HOLD": "관망", "UNDERPERFORM": "부진"}.get(grade, grade)

    # 가격 포맷
    is_kr = ".KS" in ticker or ".KQ" in ticker
    p_str = f"{price:,.0f}원" if is_kr else f"${price:,.2f}"

    parts = [f"선생님, {name} 종합 분석 결과를 보고드립니다."]
    parts.append(f"현재가 {p_str}, 투자 점수 100점 만점에 {score}점으로 {grade_kr} 등급입니다.")

    # 핵심 근거 (상위 3개)
    top_reasons = scoring["reasons"][:3]
    if top_reasons:
        parts.append(f"주요 근거로 {', '.join(r.split(')')[0]+')' if ')' in r else r for r in top_reasons)} 입니다.")

    # 포트폴리오 추천
    if portfolio and portfolio.get("recommended_shares", 0) > 0:
        shares = portfolio["recommended_shares"]
        cost = portfolio["entry_cost"]
        if portfolio.get("guardrail_active"):
            parts.append(
                f"다만 매크로 가드레일이 작동하여 기본 배분 {portfolio.get('base_allocation_pct')}를 "
                f"{portfolio.get('allocation_pct')}로 낮췄습니다. 추천 매수 수량은 {shares}주, 진입 비용 {cost}입니다."
            )
        else:
            parts.append(f"예수금 기준 추천 매수 수량은 {shares}주, 진입 비용 {cost}입니다.")
    elif portfolio:
        parts.append(f"현재 등급에서는 매수를 권하지 않습니다. 관망하시는 것이 좋겠습니다.")

    if macro_status and macro_status.get("guardrail_active"):
        parts.append("현재 공포 지수 변동성 확대로 인해 매크로 가드레일 안전장치가 가동 중입니다.")

    return " ".join(parts)


# 지표 코드 → 음성 친화 한글 표기 (TTS가 자연스럽게 읽도록)
_IND_KR = {
    "RSI": "RSI", "MACD": "MACD", "Stochastic": "스토캐스틱",
    "Bollinger": "볼린저밴드", "Ichimoku": "일목균형표", "ADX": "ADX",
    "SAR": "SAR", "Momentum": "모멘텀", "Williams%R": "윌리엄스 R",
    "CCI": "CCI", "OBV": "OBV", "VWMA": "거래량가중이평", "MA": "이동평균",
}


def _generate_display_data(name, ticker, price, fund, tech, scoring, portfolio, macro_status=None) -> dict:
    """screen_overlay display_table 호환 데이터 생성"""
    is_kr = ".KS" in ticker or ".KQ" in ticker
    p_str = f"{price:,.0f}" if is_kr else f"${price:,.2f}"

    # 시그널 요약 테이블
    sig_rows = []
    buy_count = sell_count = neutral_count = 0
    buy_inds, sell_inds = [], []
    for ind_name, (desc, direction) in tech["signals"].items():
        emoji = "+" if direction == "BUY" else ("-" if direction == "SELL" else "=")
        sig_rows.append([ind_name, desc, emoji])
        if direction == "BUY":
            buy_count += 1; buy_inds.append(_IND_KR.get(ind_name, ind_name))
        elif direction == "SELL":
            sell_count += 1; sell_inds.append(_IND_KR.get(ind_name, ind_name))
        else:
            neutral_count += 1

    # 음성용 표 요약 — 지표 표를 한두 문장으로 풀어 말해주기 위한 텍스트
    total_sig = buy_count + sell_count + neutral_count
    lean = "매수 우위" if buy_count > sell_count else ("매도 우위" if sell_count > buy_count else "혼조세")
    vs_parts = [f"기술적 지표 {total_sig}개 중 매수 신호 {buy_count}개, 매도 신호 {sell_count}개, "
                f"중립 {neutral_count}개로 {lean}입니다."]
    clauses = []
    if buy_inds:  clauses.append(f"매수 신호는 {', '.join(buy_inds[:3])}")
    if sell_inds: clauses.append(f"매도 신호는 {', '.join(sell_inds[:3])}")
    if clauses:   vs_parts.append("이며, ".join(clauses) + "입니다.")
    voice_summary = " ".join(vs_parts)

    # 요약 정보
    grade_kr = {"STRONG BUY": "강력매수", "BUY": "매수",
                "HOLD": "관망", "UNDERPERFORM": "부진"}.get(scoring["grade"], scoring["grade"])

    summary_items = {
        "종목": f"{name} ({ticker})",
        "현재가": p_str + ("원" if is_kr else ""),
        "투자점수": f"{scoring['score']}/100",
        "투자등급": grade_kr,
        "매수시그널": f"{buy_count}개",
        "매도시그널": f"{sell_count}개",
        "중립시그널": f"{neutral_count}개",
    }

    if fund.get("per"):
        summary_items["PER"] = f"{fund['per']:.1f}"
    if fund.get("roe"):
        summary_items["ROE"] = f"{fund['roe']:.1f}%"
    if fund.get("pbr"):
        summary_items["PBR"] = f"{fund['pbr']:.2f}"

    if portfolio and portfolio.get("recommended_shares", 0) > 0:
        summary_items["추천수량"] = f"{portfolio['recommended_shares']}주"
        summary_items["진입비용"] = portfolio["entry_cost"]
        summary_items["배분비율"] = portfolio["allocation_pct"]
        if portfolio.get("guardrail_active"):
            summary_items["기본배분"] = portfolio.get("base_allocation_pct", "")
            summary_items["가드레일"] = f"{portfolio.get('macro_risk_level','위험')} / {portfolio['allocation_multiplier']:.2f}x"

    if macro_status:
        summary_items["시장위험"] = macro_status.get("risk_level", "안정")

    return {
        "summary": summary_items,
        "voice_summary": voice_summary,
        "signals_table": {
            "headers": ["지표", "상태", "방향"],
            "rows": sig_rows,
        },
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  8. 종합 투자 매트릭스 엔진 (Multi-Dimensional Investment Matrix)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_MATRIX_CACHE: dict = {}
_MATRIX_CACHE_TTL: int = 300       # 5분 캐시 TTL (초)
_WHALE_TICKER_CACHE: dict = {}
_WHALE_CACHE_TTL: int = 3600       # 고래 데이터 1시간 캐시


def _compute_tech_layer_score(signals: dict) -> tuple[int, str]:
    """
    14개 기술적 지표 시그널 딕셔너리 → 0-50점 퀀트 레이어 점수.
    기존 compute_technical_snapshot() 출력을 래핑하며, 원본 시그니처를 변경하지 않는다.
    """
    score = 22  # neutral baseline: 중립 조합 시 ≈25점 기대값

    for sig_name, (label, direction) in signals.items():
        if sig_name == "MACD":
            if "골든크로스" in label:
                score += 6
            elif "데드크로스" in label:
                score -= 5
            elif direction == "BUY":
                score += 3
            elif direction == "SELL":
                score -= 2
        elif sig_name == "RSI":
            score += 5 if direction == "BUY" else (-4 if direction == "SELL" else 0)
        elif sig_name == "MA":
            score += 5 if direction == "BUY" else (-4 if direction == "SELL" else 0)
        elif sig_name in ("Bollinger", "ADX", "Stochastic"):
            score += 4 if direction == "BUY" else (-3 if direction == "SELL" else 0)
        elif sig_name in ("SAR", "Ichimoku", "Momentum", "OBV", "VWMA"):
            score += 3 if direction == "BUY" else -2
        elif sig_name in ("Williams%R", "CCI"):
            score += 3 if direction == "BUY" else (-2 if direction == "SELL" else 0)

    buy_n = sum(1 for _, d in signals.values() if d == "BUY")
    sell_n = sum(1 for _, d in signals.values() if d == "SELL")
    return max(0, min(50, score)), f"매수 {buy_n}개/매도 {sell_n}개"


def _fetch_whale_data_cached(person_key: str) -> dict:
    """고래 트레이드 데이터를 1시간 로컬 캐시로 조회 (반복 네트워크 호출 방지)."""
    from whale_tracker import fetch_whale_trades
    now = datetime.now()
    cached = _WHALE_TICKER_CACHE.get(person_key)
    if cached and (now - cached["ts"]).total_seconds() < _WHALE_CACHE_TTL:
        return cached["data"]
    data = fetch_whale_trades(person_key, max_positions=20)
    _WHALE_TICKER_CACHE[person_key] = {"ts": now, "data": data}
    return data


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  국내 수급(외국인·기관·개인 순매매) — 네이버 금융 스크레이프
#  pykrx 투자자 엔드포인트가 1.0.51에서 깨져(KeyError '거래대금') 무의존 폴백 채택.
#  requests/pandas는 이미 의존성 → 추가 패키지 없음.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_KR_FLOW_CACHE: dict = {}
_KR_FLOW_TTL = 300   # 5분 (매트릭스 캐시와 동일)


def _fetch_kr_investor_flow(ticker: str, days: int = 5) -> dict | None:
    """
    네이버 금융 외인/기관 매매동향에서 최근 `days` 거래일 순매매를 누적한다.
    반환: 외인·기관·개인 순매매(주), 거래량, 거래량比 비중, 기간 등락률.
    실패 시 None → 호출부가 중립(12점)으로 폴백.
    """
    import time as _time
    code = ticker.upper().split(".")[0]
    if not (code.isdigit() and len(code) == 6):
        return None

    cached = _KR_FLOW_CACHE.get(code)
    if cached and _time.time() - cached["ts"] < _KR_FLOW_TTL:
        return cached["data"]

    try:
        import requests
        from io import StringIO

        url = f"https://finance.naver.com/item/frgn.naver?code={code}&page=1"
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                 "AppleWebKit/537.36 (KHTML, like Gecko)"}
        resp = requests.get(url, headers=headers, timeout=8)
        resp.encoding = "euc-kr"

        flow_tbl = None
        for tbl in pd.read_html(StringIO(resp.text)):
            cols = [str(c) for c in tbl.columns]
            if tbl.shape[1] >= 7 and any("기관" in c for c in cols) and any("외국인" in c for c in cols):
                flow_tbl = tbl
                break
        if flow_tbl is None:
            return None

        # 컬럼 위치: 0 날짜 · 1 종가 · 4 거래량 · 5 기관순매매 · 6 외국인순매매
        df = flow_tbl[pd.to_numeric(flow_tbl.iloc[:, 1], errors="coerce").notna()]
        if df.empty:
            return None
        df = df.head(days)

        close = pd.to_numeric(df.iloc[:, 1], errors="coerce")
        volume = pd.to_numeric(df.iloc[:, 4], errors="coerce").fillna(0)
        inst_net = float(pd.to_numeric(df.iloc[:, 5], errors="coerce").fillna(0).sum())
        foreign_net = float(pd.to_numeric(df.iloc[:, 6], errors="coerce").fillna(0).sum())
        smart_net = inst_net + foreign_net          # 외인+기관 = 스마트머니
        total_vol = float(volume.sum()) or 1.0
        price_last = float(close.iloc[0])           # 최신 거래일
        price_first = float(close.iloc[-1])         # 기간 시작
        chg = (price_last / price_first - 1.0) * 100 if price_first else 0.0

        data = {
            "code": code,
            "days": int(len(df)),
            "inst_net": inst_net,
            "foreign_net": foreign_net,
            "smart_net": smart_net,
            "retail_net": -smart_net,               # 개인 ≈ -(외인+기관)
            "total_vol": total_vol,
            "smart_share": smart_net / total_vol,
            "retail_share": -smart_net / total_vol,
            "price_change_pct": chg,
            "latest_date": str(df.iloc[0, 0]),
        }
        _KR_FLOW_CACHE[code] = {"ts": _time.time(), "data": data}
        return data

    except Exception:
        return None


def _compute_whale_layer_score(ticker: str) -> tuple[int, str, list]:
    """
    고래/스마트머니 레이어: 0-25점 + 상태 문자열 + 근거 목록.
    오류/데이터 없음 시 중립 기준선(12점)으로 안전 폴백.
    """
    base = 12   # neutral midpoint (데이터 없을 때 기본 점수)
    adj = 0
    notes: list = []

    # ── 국내 종목: 외국인·기관 순매수(스마트머니) 실데이터 ──
    if ".KS" in ticker or ".KQ" in ticker:
        flow = _fetch_kr_investor_flow(ticker, days=5)
        if flow is None:
            notes.append("국내 수급 데이터 조회 실패 → 중립")
            return base, "중립", notes
        share = flow["smart_share"]                 # (외인+기관 순매수) / 거래량
        adj = int(max(-13, min(13, round(share * 100 * 1.3))))
        f, i = flow["foreign_net"], flow["inst_net"]
        lead = "외국인" if abs(f) >= abs(i) else "기관"
        notes.append(
            f"{flow['days']}일 외인 {f:+,.0f}주 · 기관 {i:+,.0f}주 "
            f"(거래량比 {share * 100:+.1f}%, {lead} 주도)"
        )
        score = max(0, min(25, base + adj))
        if adj >= 8:
            status = f"외인·기관 강력 매집 (+{adj}pt)"
        elif adj >= 3:
            status = f"외인·기관 순매수 (+{adj}pt)"
        elif adj <= -8:
            status = f"외인·기관 강력 매도 ({adj}pt)"
        elif adj <= -3:
            status = f"외인·기관 순매도 ({adj}pt)"
        else:
            status = "수급 중립"
        return score, status, notes

    try:
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(days=90)  # 3개월 이내
        t_sym = ticker.upper().split(".")[0]

        for person_key in ("buffett", "pelosi"):
            try:
                data = _fetch_whale_data_cached(person_key)
                display = data.get("person", person_key)

                for trade in data.get("trades", []):
                    if trade.get("ticker", "").upper().split(".")[0] != t_sym:
                        continue
                    side = trade.get("side", "")
                    # 날짜 파싱 실패 시 보수적으로 recent=True
                    recent = True
                    try:
                        date_str = (trade.get("date") or "")
                        if date_str:
                            recent = (
                                datetime.strptime(date_str[:10], "%Y-%m-%d") >= cutoff
                            )
                    except Exception:
                        pass

                    if side == "BUY" and recent:
                        adj += 13  # 엘리트 3개월 이내 매수: +10~15
                        notes.append(f"{display} 3개월 이내 매수 포착")
                    elif side == "SELL":
                        adj -= 15  # 대량 매도/분산: -15
                        notes.append(f"{display} 매도·분산 포착 (헤징/청산)")
                    break   # 인물당 해당 티커의 첫 트레이드만 반영

                # 버크셔 13F 포트폴리오 비중 보너스
                if "buffett" in person_key:
                    for p in data.get("portfolio", []):
                        if p.get("ticker", "").upper() == t_sym:
                            w = float(p.get("weight_pct", 0))
                            if w >= 1.0:
                                bonus = min(int(w * 0.5), 5)
                                adj += bonus
                                notes.append(f"버크셔 13F {w:.1f}% 보유 (+{bonus}pt)")
                            break

            except Exception as e:
                notes.append(f"{person_key} 조회 건너뜀: {str(e)[:40]}")

    except ImportError:
        notes.append("whale_tracker 미로드 → 중립 적용")
        adj = 0
    except Exception as e:
        notes.append(f"고래 데이터 오류 → 중립: {str(e)[:60]}")
        adj = 0

    score = max(0, min(25, base + adj))
    if adj >= 8:
        status = f"엘리트 매수 (+{adj}pt)"
    elif adj <= -8:
        status = f"기관 매도 ({adj}pt)"
    else:
        status = "중립"
    return score, status, notes


def _compute_social_layer_score(ticker: str) -> tuple[int, str, list]:
    """
    소셜 센티먼트 & 과열 레이어: 0-25점 + 상태 문자열 + 근거 목록.
    백그라운드 SocialSpikeDetector._spike_cache → baseline 순으로 조회.
    오류 시 중립 기준선(12점)으로 안전 폴백.
    """
    base = 12   # neutral midpoint
    adj = 0
    notes: list = []
    t_sym = ticker.upper().split(".")[0]

    # ── 국내 종목: 개인 순매수 + 주가 = 리테일 과열(FOMO) 프록시 ──
    if ".KS" in ticker or ".KQ" in ticker:
        flow = _fetch_kr_investor_flow(ticker, days=5)
        if flow is None:
            return base, "중립", ["국내 수급 데이터 조회 실패 → 중립"]
        retail_share = flow["retail_share"]         # (개인 순매수) / 거래량
        chg = flow["price_change_pct"]
        if retail_share >= 0.05 and chg >= 7:
            adj = -15 if (retail_share >= 0.10 or chg >= 15) else -10
            notes.append(
                f"개인 순매수 거래량比 {retail_share * 100:+.1f}% · {flow['days']}일 {chg:+.1f}% "
                f"→ 리테일 과열 ({adj}pt)"
            )
        elif retail_share <= -0.05 and chg >= 5:
            adj = 8
            notes.append(
                f"외인·기관 주도 상승 (개인 거래량比 {retail_share * 100:+.1f}%, {chg:+.1f}%) "
                f"→ 건전 수급 (+{adj}pt)"
            )
        elif retail_share >= 0.05 and chg <= -5:
            adj = -5
            notes.append(
                f"개인 순매수 거래량比 {retail_share * 100:+.1f}% · 주가 {chg:+.1f}% "
                f"(역추세 매수) → 경계 ({adj}pt)"
            )
        else:
            notes.append(
                f"수급 균형 (개인 거래량比 {retail_share * 100:+.1f}%, {flow['days']}일 {chg:+.1f}%)"
            )
        score = max(0, min(25, base + adj))
        if adj >= 8:
            status = "건전 수급 (외인 주도)"
        elif adj <= -10:
            status = f"리테일 과열 ({adj}pt)"
        elif adj <= -3:
            status = "수급 경계"
        else:
            status = "중립"
        return score, status, notes

    try:
        import time as _time
        from social_detector import _detector_instance, _load_baseline

        # ── 백그라운드 워커 스파이크 캐시 우선 조회 ──
        spike_data = None
        if _detector_instance is not None and hasattr(_detector_instance, "_spike_cache"):
            cached = _detector_instance._spike_cache.get(t_sym)
            if cached and _time.time() - float(cached.get("ts", 0)) < 3600:
                spike_data = cached

        if spike_data:
            ratio = float(spike_data.get("spike_ratio", 1.0))
            risk_label = spike_data.get("risk_label", "")
            risk_level = spike_data.get("risk_level", "중")

            if ratio >= 5.0 and ("위험도 상" in risk_label or risk_level == "상"):
                # 투기성 펌핑: 리테일 유동성 함정 → 과열 페널티
                penalty = -17 if ratio >= 10.0 else -15
                adj += penalty
                notes.append(
                    f"레딧 언급 {ratio:.1f}x 급증 | 투기 수급 유입 (위험도 상) → {penalty}pt"
                )
            elif ratio >= 5.0 and "위험도 하" in risk_label:
                # 건전한 촉매제(실적/뉴스 기반)
                adj += 10
                notes.append(
                    f"레딧 언급 {ratio:.1f}x 급증 | 실적·뉴스 기반 (위험도 하) → +10pt"
                )
            else:
                # 중간 위험도
                adj -= 5
                notes.append(f"레딧 언급 {ratio:.1f}x 급증 | 위험도 중 (-5pt)")
        else:
            # 스파이크 캐시 없음 → baseline 평균 언급량으로 소폭 보정
            baseline = _load_baseline()
            avg = float(baseline.get(t_sym, {}).get("avg_hourly_mentions", 0))
            if avg >= 5.0:
                adj += 3
                notes.append(f"지속 소셜 관심 (기준선 {avg:.1f}회/h → +3pt)")
            else:
                notes.append("소셜 데이터 없음 → 중립")

    except ImportError:
        notes.append("social_detector 미로드 → 중립 적용")
        adj = 0
    except Exception as e:
        notes.append(f"소셜 데이터 오류 → 중립: {str(e)[:60]}")
        adj = 0

    score = max(0, min(25, base + adj))
    if adj >= 8:
        status = "건전 촉매 (위험도 하)"
    elif adj <= -10:
        status = f"투기 과열 (위험도 상) {adj}pt"
    elif adj <= -3:
        status = "소셜 과열 경계 (위험도 중)"
    else:
        status = "중립"
    return score, status, notes


def _generate_matrix_briefing(
    name: str, ticker: str, price: float,
    tech_score: int, tech_signals: dict,
    whale_score: int, whale_status: str, whale_notes: list,
    social_score: int, social_status: str, social_notes: list,
    total_score: int, grade: str,
    base_ratio: float, final_ratio: float,
    macro_risk: str, macro_critical: bool,
) -> str:
    """종합 투자 매트릭스 이중 TTS 파이프라인용 음성 브리핑 텍스트 생성."""
    is_kr = ".KS" in ticker or ".KQ" in ticker
    p_str = f"{price:,.0f}원" if is_kr else f"${price:,.2f}"

    grade_kr = {
        "DYNAMIC STRONG BUY": "강력 매수",
        "CAUTIOUS BUY": "신중 매수",
        "HOLD": "관망",
        "AVOID / RISK LIQUIDATION": "위험 청산",
    }.get(grade, grade)

    # 핵심 기술 시그널 최대 2개 추출
    macd_label = tech_signals.get("MACD", ("", "NEUTRAL"))[0]
    rsi_dir = tech_signals.get("RSI", ("", "NEUTRAL"))[1]
    ma_dir = tech_signals.get("MA", ("", "NEUTRAL"))[1]

    key_tech: list = []
    if "골든크로스" in macd_label:
        key_tech.append("MACD 골든크로스로 강한 매수 신호")
    elif "데드크로스" in macd_label:
        key_tech.append("MACD 데드크로스로 약세 경고")
    elif tech_signals.get("MACD", ("", "NEUTRAL"))[1] == "BUY":
        key_tech.append("MACD 상승 흐름")

    if rsi_dir == "BUY":
        key_tech.append("RSI 과매도 구간")
    elif rsi_dir == "SELL":
        key_tech.append("RSI 과매수 구간")

    if ma_dir == "BUY":
        key_tech.append("이동평균 정배열")
    elif ma_dir == "SELL":
        key_tech.append("이동평균 역배열")

    tech_desc = "·".join(key_tech[:2]) if key_tech else "복합 기술 지표 혼조"

    parts = [f"선생님, {name} 종합 투자 매트릭스 분석 결과를 보고드립니다."]
    parts.append(
        f"기술적 지표는 {tech_desc}를 보이고 있으며, "
        f"퀀트 레이어 점수 {tech_score}점입니다."
    )

    # 고래 레이어 브리핑
    if whale_notes and not any(kw in whale_notes[0] for kw in ("오류", "미로드", "건너뜀")):
        parts.append(
            f"고래 추적 시스템에서는 {whale_notes[0]}으로 수급 레이어 {whale_score}점입니다."
        )
    else:
        parts.append(f"고래 수급 데이터는 중립으로 {whale_score}점 적용 중입니다.")

    # 소셜 레이어 브리핑
    if "투기 과열" in social_status or "위험도 상" in social_status:
        parts.append(
            f"레딧 소셜 과열도가 상위 구간으로 투기성 물량 진입 경보가 발동하여 "
            f"소셜 레이어 {social_score}점으로 하향 조정되었습니다."
        )
    elif "건전 촉매" in social_status:
        parts.append(
            f"소셜 급증이 건전한 펀더멘탈 기반으로 확인되어 "
            f"소셜 레이어 {social_score}점을 획득했습니다."
        )
    else:
        parts.append(f"소셜 센티먼트는 중립으로 {social_score}점이 적용되었습니다.")

    base_pct = int(base_ratio * 100)
    final_pct = round(final_ratio * 100, 1)

    parts.append(
        f"3개 레이어 종합 점수 {total_score}점으로 {grade_kr} 등급이 산출되었습니다."
    )

    if grade == "AVOID / RISK LIQUIDATION":
        parts.append(
            "수급 주체 불균형 리스크를 반영하여 즉각적인 포지션 정리 및 신규 진입 금지를 강력 권고합니다."
        )
    elif grade == "HOLD":
        parts.append("현재는 관망을 유지하시고 추가 시그널을 확인하십시오.")
    elif macro_critical and base_pct > 15:
        parts.append(
            f"매크로 가드레일이 위험 단계로 가동 중입니다. "
            f"기본 배분 {base_pct}%에서 최대 15%로 진입 한도가 엄격히 제한되었습니다."
        )
    elif abs(final_pct - base_pct) > 2:
        parts.append(
            f"가드레일 작동으로 권장 진입 비중이 기존 {base_pct}%에서 {final_pct}%로 하향 조정했습니다."
        )
    else:
        parts.append(f"권장 포지션 진입 비중은 {final_pct}%입니다.")

    return " ".join(parts)


def get_comprehensive_matrix_score(ticker: str) -> dict:
    """
    종합 투자 매트릭스 엔진 (Multi-Dimensional Investment Matrix Engine).

    기존 14개 기술적 지표 연산 인프라(compute_technical_snapshot)를 기반으로,
    고래/스마트머니 플로우(whale_tracker)와 소셜 센티먼트(social_detector)를
    단일 포인트 기반 의사결정 노드로 통합한다.

    * 기존 analyze_stock_full_package()·compute_technical_snapshot() 시그니처 완전 보존.
    * 각 하위 모듈 호출은 독립 try-except로 감싸 데이터 없음/네트워크 오류 시
      중립 점수(0점 조정)로 폴백하며 메인 루프를 중단하지 않는다.
    * Apple M5 Silicon 최적화: 외부 호출은 로컬 dict 캐시 우선 사용.
    """
    resolved = _resolve_ticker(ticker)

    # 5분 캐시 히트 시 즉시 반환 (중복 API 호출 차단)
    now = datetime.now()
    cached = _MATRIX_CACHE.get(resolved)
    if cached and (now - cached["ts"]).total_seconds() < _MATRIX_CACHE_TTL:
        return cached["data"]

    # ── 1. 주가 데이터 수집 ──
    df, fund, error = fetch_stock_data(resolved)
    if error:
        return {"success": False, "message": error}

    price = fund.get("price", 0)
    if price <= 0 and not df.empty:
        price = float(df["Close"].iloc[-1])
        fund["price"] = price
    name = fund.get("name", resolved)

    # ── 2. 기술적 분석 (기존 함수 래핑 — 원본 호출 경로 보존) ──
    tech = compute_technical_snapshot(df)

    # ── 3. 레이어별 독립 점수 계산 ──
    tech_score, tech_summary = _compute_tech_layer_score(tech["signals"])

    try:
        whale_score, whale_status, whale_notes = _compute_whale_layer_score(resolved)
    except Exception as e:
        whale_score, whale_status, whale_notes = 12, "중립 (오류)", [str(e)[:60]]

    try:
        social_score, social_status, social_notes = _compute_social_layer_score(resolved)
    except Exception as e:
        social_score, social_status, social_notes = 12, "중립 (오류)", [str(e)[:60]]

    # ── 3.5 소셜 미시구조: 호가/거래량 프로파일로 FOMO·매집·숏스퀴즈 정밀 판별 ──
    #   (단순 가점이 아니라 세력성 물량 여부를 세부 등급으로 쪼개 반영)
    social_micro = {}
    try:
        import risk_engine as _re
        social_micro = _re.social_microstructure(resolved, df.get("Close"), df.get("Volume"))
        delta = social_micro.get("score_delta", 0)
        if delta:
            social_score = max(0, min(25, social_score + delta))   # 0-25 클램핑(하위호환)
            social_notes = list(social_notes) + social_micro.get("notes", [])
            if social_micro.get("subgrade"):
                social_status = f"{social_status} · {social_micro['subgrade']}"
    except Exception:
        social_micro = {}

    # ── 3.6 고래 인과관계: 버크셔/펠로시 변동의 '이유'를 공시·뉴스로 컨텍스트화 ──
    #   (무거운 news_analyzer 호출은 백그라운드+1시간 캐시 → 비블로킹)
    whale_causation_ctx = {}
    try:
        import risk_engine as _re
        whale_causation_ctx = _re.whale_causation(resolved, whale_status, whale_notes)
        if whale_causation_ctx.get("available") and whale_causation_ctx.get("why"):
            whale_notes = list(whale_notes) + [f"인과: {whale_causation_ctx['why'][:80]}"]
    except Exception:
        whale_causation_ctx = {}

    # ── 4. 총합 점수 (0-100 클램핑) ──
    total_score = max(0, min(100, tech_score + whale_score + social_score))

    # ── 5. 매크로 가드레일 연동 ──
    macro_status = get_macro_market_status()
    macro_risk = macro_status.get("risk_level", "안정")
    macro_critical = macro_risk == "위험"
    macro_multi = float(macro_status.get("allocation_multiplier", 1.0))

    # ── 6. 동적 자산 배분 등급 매핑 ──
    if total_score >= 90:
        grade = "DYNAMIC STRONG BUY"
        base_ratio = 0.30           # 최대 30%
    elif total_score >= 70:
        grade = "CAUTIOUS BUY"
        base_ratio = 0.175          # 15~20% 중간값
    elif total_score >= 50:
        grade = "HOLD"
        base_ratio = 0.0
    else:
        grade = "AVOID / RISK LIQUIDATION"
        base_ratio = 0.0

    # 매크로 CRITICAL 상태: 고래/소셜 점수와 무관하게 최대 15% 강제 상한
    if macro_critical and base_ratio > 0.15:
        final_ratio = 0.15
        macro_override = True
    else:
        final_ratio = base_ratio * macro_multi
        macro_override = False

    final_pct = round(final_ratio * 100, 1)
    base_pct = int(base_ratio * 100)

    # ── 6.5 몬테카를로 가상 스트레스 + 리스크 가중치 재조정 ──
    #   매크로가 '경계'/'위험'일 때만 10,000회 시뮬레이션을 트리거(안정 시 연산 절약).
    #   시나리오별 예상 MDD가 임계치를 초과하면 매트릭스 점수가 높아도 비중을 강제 하향.
    stress_assessment = {}
    risk_reweight = {}
    forecast_briefing = ""
    if macro_risk in ("경계", "위험") and base_ratio > 0:
        try:
            import risk_engine as _re
            stress_assessment = _re.assess(
                resolved, df.get("Close"), macro_status,
                score=total_score, base_ratio=base_ratio,
            )
            risk_reweight = stress_assessment.get("reweight", {})
            if risk_reweight.get("triggered"):
                haircut = risk_reweight.get("haircut_pct", 0) / 100.0
                final_ratio = max(0.0, final_ratio - haircut)
                final_pct = round(final_ratio * 100, 1)
                macro_override = True   # 시뮬레이션 기반 강제 재조정 발생
            forecast_briefing = _re.build_forecast_briefing(
                name, stress_assessment.get("stress", {}), risk_reweight,
                base_pct=base_pct, final_pct=final_pct, macro_level=macro_risk,
                high_vol_ticker=name if stress_assessment.get("stress", {}).get("high_volatility") else "",
            )
        except Exception as _e:
            stress_assessment = {}; risk_reweight = {}; forecast_briefing = ""

    grade_kr = {
        "DYNAMIC STRONG BUY": "강력매수",
        "CAUTIOUS BUY": "신중매수",
        "HOLD": "관망",
        "AVOID / RISK LIQUIDATION": "위험청산",
    }.get(grade, grade)

    # ── 7. HUD 오버레이 딕셔너리 (screen_overlay.py 호환 구조) ──
    overlay_table = {
        "type": "table",
        "title": f"INVESTMENT MATRIX | {name} | {total_score}/100 {grade_kr}",
        "headers": ["레이어", "점수/상태", "세부"],
        "rows": [
            ["QUANT TECH",     f"{tech_score}/50",   tech_summary],
            ["WHALE FLOW",     f"{whale_score}/25",  whale_status],
            ["SOCIAL HYPE",    f"{social_score}/25", social_status],
            ["COMPOSITE",      f"{total_score}/100", grade_kr],
            ["FINAL ALLOC",    f"{final_pct}%",
             f"매크로: {macro_risk}" + (" ⚠OVERRIDE" if macro_override else "")],
        ],
        # HUD 동적 테마/시나리오 연동 (screen_overlay 반응형 렌더)
        "theme": macro_risk,
    }
    if stress_assessment.get("stress"):
        _w = stress_assessment["stress"].get("worst", {})
        overlay_table["scenario_rows"] = [
            [s.get("label", s.get("key", "")), f"EST.MDD {s.get('est_mdd_pct', 0):.0f}%"]
            for s in stress_assessment["stress"].get("scenarios", [])
        ]
        if risk_reweight.get("triggered"):
            overlay_table["scenario_alert"] = (
                f"CRITICAL: {_w.get('en','SHOCK')} -> EST.MDD {_w.get('est_mdd_pct',0):.0f}% "
                f"| CUT {risk_reweight.get('haircut_pct',0):.0f}%"
            )

    # screen_overlay HUD row/value 구조 (반투명 매트릭스 투사용)
    hud_rows = ["QUANT TECH", "WHALE FLOW", "SOCIAL HYPE", "FINAL ALLOCATION"]
    hud_values = [
        f"{tech_score}/50",
        whale_status,
        social_status,
        f"{final_pct}%",
    ]

    # ── 8. 이중 TTS 파이프라인 음성 브리핑 ──
    briefing = _generate_matrix_briefing(
        name=name, ticker=resolved, price=price,
        tech_score=tech_score, tech_signals=tech["signals"],
        whale_score=whale_score, whale_status=whale_status, whale_notes=whale_notes,
        social_score=social_score, social_status=social_status, social_notes=social_notes,
        total_score=total_score, grade=grade,
        base_ratio=base_ratio, final_ratio=final_ratio,
        macro_risk=macro_risk, macro_critical=macro_critical,
    )

    result = {
        "success": True,
        "name": name,
        "ticker": resolved,
        "price": price,
        "tech_score": tech_score,
        "whale_score": whale_score,
        "social_score": social_score,
        "total_score": total_score,
        "grade": grade,
        "base_allocation_pct": f"{base_pct}%",
        "final_allocation_pct": f"{final_pct}%",
        "macro_critical_override": macro_override,
        "macro_risk_level": macro_risk,
        "hud_rows": hud_rows,
        "hud_values": hud_values,
        "overlay_table": overlay_table,
        "briefing": briefing,
        "tech_signals": tech["signals"],
        "whale_notes": whale_notes,
        "social_notes": social_notes,
        # ── 자율 시뮬레이션 에이전트 확장 필드 ──
        "stress_assessment": stress_assessment,
        "risk_reweight": risk_reweight,
        "forecast_briefing": forecast_briefing,
        "whale_causation": whale_causation_ctx,
        "social_micro": social_micro,
    }

    _MATRIX_CACHE[resolved] = {"ts": now, "data": result}
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  7. 경량 실시간 조회 함수 (현재가 / 주간 트렌드)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_stock_quote(ticker: str) -> dict:
    """현재가·등락률·거래량 빠른 조회 (yfinance 5d)"""
    resolved = _resolve_ticker(ticker)
    try:
        stock = yf.Ticker(resolved)
        info = stock.info or {}
        df = stock.history(period="5d", auto_adjust=True)
        if df.empty:
            return {"success": False, "message": f"'{ticker}' 데이터 없음. 티커를 확인하세요."}
        name = info.get("shortName", info.get("longName", ticker))
        currency = info.get("currency", "")
        curr  = float(df["Close"].iloc[-1])
        prev  = float(df["Close"].iloc[-2]) if len(df) >= 2 else curr
        chg   = curr - prev
        chg_p = (chg / prev * 100) if prev else 0.0
        vol   = int(df["Volume"].iloc[-1])
        avg_v = int(info.get("averageVolume", 0) or 0)
        v_ratio = round(vol / avg_v, 2) if avg_v else 1.0
        sign  = "▲" if chg >= 0 else "▼"
        csym  = "₩" if currency == "KRW" else "$"
        briefing = (
            f"{name} 현재가 {csym}{curr:,.0f}. "
            f"{sign} {abs(chg_p):.2f}% ({'+' if chg>=0 else ''}{chg:,.0f}). "
            f"거래량 {vol:,} (평균 대비 {v_ratio:.1f}배)."
        )
        return {"success": True, "ticker": resolved, "name": name,
                "price": curr, "change": round(chg, 2), "change_pct": round(chg_p, 2),
                "volume": vol, "avg_volume": avg_v, "volume_ratio": v_ratio,
                "currency": currency, "briefing": briefing}
    except Exception as e:
        return {"success": False, "message": f"현재가 조회 실패: {e}"}


def get_stock_trend(ticker: str) -> dict:
    """최근 7거래일 일봉 트렌드 요약"""
    resolved = _resolve_ticker(ticker)
    try:
        stock = yf.Ticker(resolved)
        info  = stock.info or {}
        df    = stock.history(period="1mo", auto_adjust=True)
        if df.empty or len(df) < 5:
            return {"success": False, "message": f"'{ticker}' 트렌드 데이터 부족"}
        name     = info.get("shortName", info.get("longName", ticker))
        currency = info.get("currency", "")
        csym     = "₩" if currency == "KRW" else "$"
        week     = df.tail(7)
        closes   = week["Close"].tolist()
        dates    = [d.strftime("%m/%d") for d in week.index]
        start, end = closes[0], closes[-1]
        w_chg_p  = (end - start) / start * 100 if start else 0.0
        w_high   = max(closes); w_low = min(closes)
        avg_vol  = int(week["Volume"].mean())
        # 최근 3일 방향
        recent3  = closes[-3:]
        direction = "상승" if recent3[-1] > recent3[0] else ("하락" if recent3[-1] < recent3[0] else "횡보")
        # 일봉 바 심볼
        bar = "".join("▲" if closes[i] > closes[i-1] else ("▼" if closes[i] < closes[i-1] else "─")
                       for i in range(1, len(closes)))
        sign = "+" if w_chg_p >= 0 else ""
        briefing = (
            f"{name} {len(closes)}거래일 흐름: {bar}. "
            f"주간 등락 {sign}{w_chg_p:.2f}%. "
            f"구간 고가 {csym}{w_high:,.0f} / 저가 {csym}{w_low:,.0f}. "
            f"최근 3일 추세: {direction}."
        )
        daily = [{"date": d, "close": round(p, 2)} for d, p in zip(dates, closes)]
        return {"success": True, "ticker": resolved, "name": name,
                "period": f"{dates[0]}~{dates[-1]}", "week_change_pct": round(w_chg_p, 2),
                "week_high": round(w_high, 2), "week_low": round(w_low, 2),
                "trend_direction": direction, "trend_bar": bar,
                "avg_daily_volume": avg_vol, "daily": daily,
                "currency": currency, "briefing": briefing}
    except Exception as e:
        return {"success": False, "message": f"트렌드 조회 실패: {e}"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  8. LLM 도구 정의
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STOCK_QUANT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "analyze_stock_investment",
            "description": (
                "주식 종목의 종합 투자 분석을 수행합니다. "
                "14개 기술적 지표(RSI,MACD,볼린저밴드,일목균형표,ADX,SAR,스토캐스틱 등)와 "
                "재무 펀더멘탈(PER,PBR,ROE,배당률 등)을 분석하여 "
                "0~100점 투자 점수와 매수 등급(STRONG BUY/BUY/HOLD/UNDERPERFORM)을 산출합니다. "
                "예수금(available_cash)을 입력하면 리스크 관리 기반 추천 매수 수량과 진입 비용도 계산합니다. "
                "주식 분석, 종목 추천, 투자 의견, 매수 추천 등을 요청받으면 이 도구를 사용하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "종목명 또는 티커 (한글 가능: 삼성전자, 네이버, 카카오 / 영문: AAPL, TSLA, MSFT / 한국거래소: 005930.KS)",
                    },
                    "available_cash_krw": {
                        "type": "integer",
                        "description": "사용자의 투자 가능 예수금 (정수, 통화는 원 기준. 예: 5000000). 사용자가 예수금을 언급하지 않으면 0.",
                    },
                },
                "required": ["ticker", "available_cash_krw"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_macro_market_status",
            "description": (
                "미국 10년물 국채금리(^TNX), 달러 인덱스(DX-Y.NYB), VIX 공포지수(^VIX), "
                "원달러 환율(USDKRW=X)을 실시간 확인하여 시장 위험 등급(안정/경계/위험)과 "
                "매크로 가드레일 작동 여부, 권장 진입 비중 감축 배수를 반환합니다. "
                "'지금 시장 상황 어때?', '매크로 위험도 알려줘', '공포지수 어때?' 같은 요청에 사용하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_comprehensive_matrix_score",
            "description": (
                "퀀트 기술 지표(0-50점) + 고래/스마트머니 플로우(0-25점) + "
                "소셜 센티먼트 과열도(0-25점)를 통합한 종합 투자 매트릭스 분석. "
                "결과는 0-100점 총점, DYNAMIC STRONG BUY/CAUTIOUS BUY/HOLD/AVOID 등급, "
                "최종 포지션 배분 비중으로 반환됩니다. "
                "사용자가 '종합 분석해줘', '투자 매트릭스 분석해줘', '멀티 레이어 분석해줘', "
                "'고래·소셜·기술 한번에 분석해줘', '[티커] 종합 분석해줘' 같이 "
                "통합 분석을 요청할 때 사용하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": (
                            "분석할 종목 티커 또는 종목명 "
                            "(예: AAPL, TSLA, NVDA, 삼성전자, 005930.KS)"
                        ),
                    },
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_quote",
            "description": (
                "특정 종목의 현재가, 전일 대비 등락률, 거래량을 즉시 조회합니다. "
                "'삼성전자 주가 알려줘', 'TSLA 지금 얼마야', '애플 현재가', "
                "'현대차 오늘 얼마 올랐어?' 같은 단순 현재가 질문에 사용하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "종목명 또는 티커 (예: 삼성전자, AAPL, TSLA, 005930.KS)",
                    },
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_trend",
            "description": (
                "특정 종목의 최근 7거래일 일봉 주가 흐름(트렌드)을 요약합니다. "
                "'이번 주 애플 주가 어땠어?', '삼성전자 최근 동향 알려줘', "
                "'지난주 TSLA 흐름은?', '최근 주가 추세 어때?' 같은 "
                "주간 트렌드·동향 질문에 사용하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "종목명 또는 티커 (예: 삼성전자, AAPL, TSLA, 005930.KS)",
                    },
                },
                "required": ["ticker"],
            },
        },
    },
]


def execute_stock_tool(tool_name, arguments):
    """주식 분석 도구 실행"""
    if tool_name == "get_macro_market_status":
        macro = get_macro_market_status(force_refresh=True)
        return {
            "success": macro.get("success", False),
            "briefing": macro.get("briefing", macro.get("message", "")),
            "risk_level": macro.get("risk_level", "안정"),
            "allocation_multiplier": macro.get("allocation_multiplier", 1.0),
            "guardrail_active": macro.get("guardrail_active", False),
            "reasons": macro.get("reasons", []),
            "indicators": macro.get("indicators", {}),
            "overlay_table": macro.get("overlay_table"),
            "message": macro.get("message", ""),
        }

    if tool_name == "get_stock_quote":
        try:
            if isinstance(arguments, str): arguments = json.loads(arguments)
            ticker = (arguments.get("ticker") or "").strip()
            if not ticker: return {"success": False, "message": "ticker 파라미터가 필요합니다."}
            return get_stock_quote(ticker)
        except Exception as e:
            return {"success": False, "message": f"현재가 조회 오류: {e}"}

    if tool_name == "get_stock_trend":
        try:
            if isinstance(arguments, str): arguments = json.loads(arguments)
            ticker = (arguments.get("ticker") or "").strip()
            if not ticker: return {"success": False, "message": "ticker 파라미터가 필요합니다."}
            return get_stock_trend(ticker)
        except Exception as e:
            return {"success": False, "message": f"트렌드 조회 오류: {e}"}

    if tool_name == "get_comprehensive_matrix_score":
        try:
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            ticker = (arguments.get("ticker") or "").strip()
            if not ticker:
                return {"success": False, "message": "ticker 파라미터가 필요합니다."}
            return get_comprehensive_matrix_score(ticker)
        except Exception as e:
            return {"success": False, "message": f"매트릭스 분석 오류: {e}"}

    if tool_name != "analyze_stock_investment":
        return {"success": False, "message": f"알 수 없는 도구: {tool_name}"}

    try:
        if isinstance(arguments, str):
            arguments = json.loads(arguments)

        ticker = arguments.get("ticker", "")
        # available_cash_krw(신규) 우선, available_cash(구버전) 폴백
        cash_raw = arguments.get("available_cash_krw",
                                 arguments.get("available_cash", 0))

        # 숫자 파싱 (정수/실수/쉼표/만원 단위 등 모두 처리)
        if isinstance(cash_raw, (int, float)):
            cash = float(cash_raw)
        else:
            cash_str = str(cash_raw).replace(",", "").replace("원", "").replace("만", "0000")
            try:
                cash = float(cash_str)
            except ValueError:
                cash = 0

        result = analyze_stock_full_package(ticker, cash)

        if not result.get("success"):
            return result

        display = result["display_data"]

        # ── 웹캠 오버레이에 바로 투사 가능한 표 데이터 ──
        grade_kr = {"STRONG BUY": "강력매수", "BUY": "매수",
                    "HOLD": "관망", "UNDERPERFORM": "부진"}.get(
                        result["scoring"]["grade"], result["scoring"]["grade"])
        macro = result.get("macro_status", {}) or {}
        stress = result.get("stress_test", {}) or {}
        overlay_table = {
            "type": "table",
            "title": f"{result['name']} | {result['scoring']['score']}/100 {grade_kr}",
            "headers": display["signals_table"]["headers"],
            "rows": display["signals_table"]["rows"],
            # ── HUD 동적 연동 필드 ──
            "theme": macro.get("risk_level", "안정"),          # 매크로 등급 → HUD 테마 색
            "scenario_rows": stress.get("scenario_rows", []),  # 예상 MDD/리스크 가중치 섹션
            "scenario_alert": stress.get("alert_text"),        # "CRITICAL SCENARIO: FX SPIKE -> EST.MDD: -23%"
        }

        return {
            "success": True,
            "briefing": result["briefing"],
            "score": result["scoring"]["score"],
            "grade": result["scoring"]["grade"],
            "overlay_table": overlay_table,
            "macro_status": macro,
            "stress_test": stress,
            "estimated_mdd_pct": stress.get("estimated_mdd_pct"),
            "risk_weight": stress.get("risk_weight"),
            "forecast_briefing": result.get("forecast_briefing", ""),
            "risk_reweight": result.get("risk_reweight", {}),
            "display_summary": display["summary"],
            "display_signals": display["signals_table"],
            "portfolio": result["portfolio"],
            "reasons": result["scoring"]["reasons"][:5],
        }

    except Exception as e:
        return {"success": False, "message": f"분석 오류: {e}"}
