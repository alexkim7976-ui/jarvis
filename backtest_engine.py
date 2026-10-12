"""
J.A.R.V.I.S — 전략 백테스팅 엔진
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
사용자가 음성으로 제안한 투자 규칙을 Gemini 로 파싱하여 과거 N개년
데이터에 대입, 누적 수익률 / 벤치마크(Buy&Hold) / 최대 낙폭(MDD) /
거래 횟수를 산출하고 [자산 성장 곡선 vs 주가] 차트를 PNG 로 저장한다.

stock_quant.py 의 14개 기술적 지표 계산 함수를 그대로 재사용한다.

흐름:
  음성 규칙 텍스트 → Gemini 파싱(JSON 조건 트리) → 시그널 함수 생성
        → run_backtest (Pandas 시뮬레이터) → 지표/차트/표/브리핑

의존성: pip install yfinance pandas numpy matplotlib google-generativeai
"""
from __future__ import annotations

from speech_style import USER_DISPLAY_NAME

import os
import re
import json
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

# matplotlib — GUI 없는 Agg 백엔드 (메인 OpenCV/제스처 루프와 충돌 방지)
# 캐시 디렉터리를 워크스페이스 내부로 고정해 권한/속도 경고 방지
os.environ.setdefault("MPLCONFIGDIR",
                      str(Path(os.getenv("AUDIO_SAVE_DIR", "audio_cache")) / ".mplcache"))
Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False

# stock_quant 의 지표 계산 함수 + 티커 변환 재사용
from stock_quant import (
    _resolve_ticker,
    calc_rsi, calc_macd, calc_stochastic, calc_bollinger, calc_ichimoku,
    calc_adx, calc_parabolic_sar, calc_momentum, calc_williams_r,
    calc_cci, calc_obv, calc_vwma, calc_atr, calc_sma,
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
AUDIO_DIR      = Path(os.getenv("AUDIO_SAVE_DIR", "audio_cache"))
AUDIO_DIR.mkdir(exist_ok=True)
CHART_PATH     = AUDIO_DIR / "backtest_chart.png"
BACKTEST_MODEL = "gemini-1.5-flash"

_KR_NAME_MAP = {
    "005930.KS": "삼성전자", "000660.KS": "SK하이닉스", "035420.KS": "네이버",
    "035720.KS": "카카오", "005380.KS": "현대차", "000270.KS": "기아",
}


def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] 📈 [백테스트] {msg}")


def _display_name(ticker: str) -> str:
    return _KR_NAME_MAP.get(ticker.upper(), ticker)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. 지표 타임라인 프레임 (stock_quant 함수 재사용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def compute_indicator_frame(df: pd.DataFrame) -> pd.DataFrame:
    """OHLCV DataFrame → 14개 지표가 컬럼으로 붙은 전체 타임라인 프레임"""
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]

    macd_line, macd_signal, macd_hist = calc_macd(c)
    stoch_k, stoch_d = calc_stochastic(h, l, c)
    bb_upper, bb_mid, bb_lower = calc_bollinger(c)
    tenkan, kijun, senkou_a, senkou_b, chikou = calc_ichimoku(h, l, c)
    adx, plus_di, minus_di = calc_adx(h, l, c)
    sar, sar_trend = calc_parabolic_sar(h, l)

    out = pd.DataFrame(index=df.index)
    out["close"] = c
    out["high"] = h
    out["low"] = l
    out["volume"] = v
    out["rsi"] = calc_rsi(c)
    out["macd"] = macd_line
    out["macd_signal"] = macd_signal
    out["macd_hist"] = macd_hist
    out["stoch_k"] = stoch_k
    out["stoch_d"] = stoch_d
    out["bb_upper"] = bb_upper
    out["bb_mid"] = bb_mid
    out["bb_lower"] = bb_lower
    out["tenkan"] = tenkan
    out["kijun"] = kijun
    out["adx"] = adx
    out["plus_di"] = plus_di
    out["minus_di"] = minus_di
    out["sar"] = sar
    out["sar_trend"] = sar_trend
    out["momentum"] = calc_momentum(c)
    out["williams_r"] = calc_williams_r(h, l, c)
    out["cci"] = calc_cci(h, l, c)
    out["obv"] = calc_obv(c, v)
    out["vwma"] = calc_vwma(c, v)
    out["atr"] = calc_atr(h, l, c)
    out["sma_5"] = calc_sma(c, 5)
    out["sma_20"] = calc_sma(c, 20)
    out["sma_50"] = calc_sma(c, 50)
    out["sma_200"] = calc_sma(c, 200)
    return out


# 지표 이름 별칭 → 프레임 컬럼명
_INDICATOR_ALIASES = {
    "RSI": "rsi", "MACD": "macd", "MACD_LINE": "macd",
    "SIGNAL": "macd_signal", "MACD_SIGNAL": "macd_signal", "MACD_HIST": "macd_hist",
    "STOCH": "stoch_k", "STOCH_K": "stoch_k", "STOCH_D": "stoch_d",
    "BB_UPPER": "bb_upper", "BB_LOWER": "bb_lower", "BB_MID": "bb_mid",
    "PRICE": "close", "CLOSE": "close", "ADX": "adx",
    "PLUS_DI": "plus_di", "MINUS_DI": "minus_di",
    "CCI": "cci", "WILLIAMS": "williams_r", "WILLIAMS_R": "williams_r",
    "MOMENTUM": "momentum", "OBV": "obv", "VWMA": "vwma",
    "TENKAN": "tenkan", "KIJUN": "kijun", "SAR": "sar",
    "SMA5": "sma_5", "SMA20": "sma_20", "SMA50": "sma_50", "SMA200": "sma_200",
    "MA5": "sma_5", "MA20": "sma_20", "MA50": "sma_50", "MA200": "sma_200",
}


def _col_for(name: str) -> str | None:
    if not name:
        return None
    key = str(name).strip().upper().replace(" ", "_")
    return _INDICATOR_ALIASES.get(key, key.lower() if key.lower() else None)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. 조건 평가기 (Gemini JSON 조건 트리 → bool)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _eval_condition(cond: dict, ind: pd.DataFrame, i: int) -> bool:
    """단일 조건 {indicator, op, value|target} 을 i 번째 시점에서 평가"""
    col = _col_for(cond.get("indicator", ""))
    if col is None or col not in ind.columns:
        return False
    op = str(cond.get("op", "")).lower().strip()

    cur = ind[col].iloc[i]
    if pd.isna(cur):
        return False

    # 비교 대상: 다른 지표(target) 또는 상수(value)
    target = cond.get("target")
    if target:
        tcol = _col_for(target)
        if tcol is None or tcol not in ind.columns:
            return False
        tgt_cur = ind[tcol].iloc[i]
        if pd.isna(tgt_cur):
            return False
        prev_self = ind[col].iloc[i - 1] if i > 0 else np.nan
        prev_tgt = ind[tcol].iloc[i - 1] if i > 0 else np.nan
    else:
        try:
            tgt_cur = float(cond.get("value"))
        except (TypeError, ValueError):
            return False
        prev_self = ind[col].iloc[i - 1] if i > 0 else np.nan
        prev_tgt = tgt_cur

    # 연산자 처리
    if op in ("<", "lt", "below", "under"):
        return cur < tgt_cur
    if op in (">", "gt", "above", "over"):
        return cur > tgt_cur
    if op in ("<=", "le"):
        return cur <= tgt_cur
    if op in (">=", "ge"):
        return cur >= tgt_cur
    if op in ("==", "eq"):
        return abs(cur - tgt_cur) < 1e-9
    # 골든크로스: 직전 아래 → 현재 위로 돌파
    if op in ("cross_above", "golden", "golden_cross", "crossup"):
        if pd.isna(prev_self) or pd.isna(prev_tgt):
            return False
        return prev_self <= prev_tgt and cur > tgt_cur
    # 데드크로스
    if op in ("cross_below", "dead", "dead_cross", "crossdown"):
        if pd.isna(prev_self) or pd.isna(prev_tgt):
            return False
        return prev_self >= prev_tgt and cur < tgt_cur
    return False


def _make_signal_func(conditions: list, logic: str = "AND"):
    """조건 리스트 → (ind, i) -> bool 시그널 함수"""
    logic = (logic or "AND").upper()

    def _func(ind: pd.DataFrame, i: int) -> bool:
        if not conditions:
            return False
        results = [_eval_condition(c, ind, i) for c in conditions if isinstance(c, dict)]
        if not results:
            return False
        return all(results) if logic == "AND" else any(results)

    return _func


def build_signal_funcs(spec: dict):
    """
    파싱된 전략 spec → (buy_signal_func, sell_signal_func, take_profit, stop_loss)
    """
    buy_conf = spec.get("buy_conditions", []) or []
    sell_conf = spec.get("sell_conditions", []) or []
    buy_logic = spec.get("buy_logic", "AND")
    sell_logic = spec.get("sell_logic", "OR")

    buy_func = _make_signal_func(buy_conf, buy_logic)
    sell_func = _make_signal_func(sell_conf, sell_logic) if sell_conf else None

    tp = spec.get("take_profit_pct")
    sl = spec.get("stop_loss_pct")
    try:
        tp = float(tp) if tp is not None else None
    except (TypeError, ValueError):
        tp = None
    try:
        sl = float(sl) if sl is not None else None
    except (TypeError, ValueError):
        sl = None
    return buy_func, sell_func, tp, sl


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. Gemini 음성 규칙 → 조건 트리 파싱
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_PARSE_SYSTEM_INSTRUCTION = """당신은 퀀트 트레이딩 전략 컴파일러입니다.
사용자가 자연어로 말한 매매 규칙을 백테스팅 엔진이 실행할 수 있는 JSON 조건 트리로 변환하십시오.

[사용 가능 지표 이름] (indicator 필드에 사용)
RSI, MACD, SIGNAL(=MACD 시그널선), MACD_HIST, STOCH_K, STOCH_D,
BB_UPPER, BB_LOWER, BB_MID, PRICE(=종가), SMA5, SMA20, SMA50, SMA200,
ADX, PLUS_DI, MINUS_DI, CCI, WILLIAMS_R, MOMENTUM, OBV, VWMA, TENKAN, KIJUN, SAR

[연산자 op]
"<", ">", "<=", ">=", "==" : 상수 value 또는 다른 지표 target 과 비교
"cross_above" : 골든크로스(직전엔 아래, 현재 위로 돌파)
"cross_below" : 데드크로스

[조건 형식]
- 상수 비교:   {"indicator": "RSI", "op": "<", "value": 30}
- 지표간 비교: {"indicator": "MACD", "op": "cross_above", "target": "SIGNAL"}
- 가격-이평:   {"indicator": "PRICE", "op": ">", "target": "SMA20"}

[출력 스키마 — 매우 중요]
설명/마크다운/코드블록 없이 "오직 아래 JSON 객체만" 반환하십시오.
- take_profit_pct: 익절 수익률(%) 숫자 또는 null
- stop_loss_pct: 손절 손실률(%) 양수 또는 null
- sell_conditions 가 비어 있으면 익절/손절로만 매도합니다.
{
  "buy_conditions": [{"indicator":"RSI","op":"<","value":30},{"indicator":"MACD","op":"cross_above","target":"SIGNAL"}],
  "buy_logic": "AND",
  "sell_conditions": [],
  "sell_logic": "OR",
  "take_profit_pct": 10,
  "stop_loss_pct": null
}
"""


def _strip_json_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def _fallback_parse(text: str) -> dict:
    """Gemini 미사용/실패 시 간단한 규칙 기반 파서 (대표 패턴만 지원)"""
    t = text or ""
    buy = []
    # RSI 30 미만 류
    m = re.search(r"rsi[^0-9]{0,6}(\d{1,3})\s*(미만|이하|under|below|<)", t, re.I)
    if m:
        buy.append({"indicator": "RSI", "op": "<", "value": int(m.group(1))})
    m2 = re.search(r"rsi[^0-9]{0,6}(\d{1,3})\s*(초과|이상|over|above|>)", t, re.I)
    if m2:
        buy.append({"indicator": "RSI", "op": ">", "value": int(m2.group(1))})
    # MACD 골든크로스
    if re.search(r"macd.*(골든|golden|cross\s*above|상향)", t, re.I):
        buy.append({"indicator": "MACD", "op": "cross_above", "target": "SIGNAL"})
    # 익절 %
    tp = None
    m3 = re.search(r"(\d{1,3})\s*%?\s*(수익|익절|profit)", t, re.I)
    if m3:
        tp = int(m3.group(1))
    # 손절 %
    sl = None
    m4 = re.search(r"(\d{1,3})\s*%?\s*(손실|손절|loss)", t, re.I)
    if m4:
        sl = int(m4.group(1))
    return {
        "buy_conditions": buy or [{"indicator": "RSI", "op": "<", "value": 30}],
        "buy_logic": "AND",
        "sell_conditions": [],
        "sell_logic": "OR",
        "take_profit_pct": tp if tp is not None else 10,
        "stop_loss_pct": sl,
    }


def parse_strategy_with_gemini(text: str) -> dict:
    """자연어 매매 규칙 → 조건 트리 dict"""
    if not text:
        return _fallback_parse("")
    if not GEMINI_API_KEY:
        _log("GEMINI_API_KEY 없음 → 규칙 기반 폴백 파서 사용")
        return _fallback_parse(text)

    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(model_name=BACKTEST_MODEL,
                                      system_instruction=_PARSE_SYSTEM_INSTRUCTION)
        resp = model.generate_content(
            f"다음 매매 규칙을 JSON 조건 트리로 변환하십시오:\n\n{text}",
            generation_config=genai.types.GenerationConfig(
                temperature=0.1, response_mime_type="application/json"),
        )
        spec = json.loads(_strip_json_fence(resp.text or ""))
        if not isinstance(spec, dict) or not spec.get("buy_conditions"):
            _log("Gemini 파싱 결과 비정상 → 폴백")
            return _fallback_parse(text)
        spec.setdefault("buy_logic", "AND")
        spec.setdefault("sell_conditions", [])
        spec.setdefault("sell_logic", "OR")
        spec.setdefault("take_profit_pct", None)
        spec.setdefault("stop_loss_pct", None)
        return spec
    except Exception as e:
        _log(f"Gemini 파싱 실패({e}) → 폴백")
        return _fallback_parse(text)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. 백테스팅 코어 엔진
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fetch_history(ticker: str, start_date: str, end_date: str) -> pd.DataFrame | None:
    if not YF_AVAILABLE:
        return None
    try:
        df = yf.Ticker(ticker).history(start=start_date, end=end_date, auto_adjust=True)
        return df if df is not None and not df.empty else None
    except Exception as e:
        _log(f"데이터 수집 실패({ticker}): {e}")
        return None


def run_backtest(ticker: str, start_date: str, end_date: str,
                 initial_capital: float, buy_signal_func,
                 sell_signal_func=None, take_profit_pct=None,
                 stop_loss_pct=None, fee_pct: float = 0.0015) -> dict:
    """
    Pandas 기반 롱-온리 백테스팅 시뮬레이터.

    Args:
        ticker: 종목 티커 (한글/영문 모두 가능)
        start_date, end_date: "YYYY-MM-DD"
        initial_capital: 초기 자산
        buy_signal_func: (ind_df, i) -> bool  매수 시그널
        sell_signal_func: (ind_df, i) -> bool  매도 시그널(선택)
        take_profit_pct: 익절 수익률(%) (선택)
        stop_loss_pct: 손절 손실률(%) 양수 (선택)
        fee_pct: 거래 수수료+세금 비율 (편도)

    Returns:
        dict — success, ticker, name, timeline(DataFrame),
               cumulative_return, benchmark_return, mdd, total_trades, ...
    """
    resolved = _resolve_ticker(ticker)
    df = _fetch_history(resolved, start_date, end_date)
    if df is None:
        return {"success": False, "message": f"'{ticker}' 과거 데이터를 가져오지 못했습니다."}

    ind = compute_indicator_frame(df)
    n = len(ind)
    if n < 30:
        return {"success": False, "message": "백테스트에 필요한 데이터가 부족합니다."}

    closes = ind["close"].values
    dates = ind.index

    cash = float(initial_capital)
    shares = 0.0
    in_pos = False
    entry_price = 0.0
    trades = 0

    total_values = np.zeros(n)
    asset_curve = np.zeros(n)

    warmup = 35  # 장기 지표 안정화 구간
    for i in range(n):
        price = closes[i]

        if i >= warmup and price > 0:
            # ── 매도 판단 (보유 중) ──
            if in_pos:
                do_sell = False
                if sell_signal_func is not None and sell_signal_func(ind, i):
                    do_sell = True
                if take_profit_pct is not None and entry_price > 0:
                    if (price - entry_price) / entry_price * 100 >= take_profit_pct:
                        do_sell = True
                if stop_loss_pct is not None and entry_price > 0:
                    if (price - entry_price) / entry_price * 100 <= -abs(stop_loss_pct):
                        do_sell = True
                if do_sell:
                    cash = shares * price * (1 - fee_pct)
                    shares = 0.0
                    in_pos = False
                    trades += 1
            # ── 매수 판단 (현금 보유) ──
            elif buy_signal_func(ind, i):
                shares = (cash * (1 - fee_pct)) / price
                entry_price = price
                cash = 0.0
                in_pos = True

        total_values[i] = cash + shares * price
        asset_curve[i] = total_values[i]

    final_value = total_values[-1]
    cumulative_return = (final_value / initial_capital - 1) * 100

    first_valid = closes[warmup] if closes[warmup] > 0 else closes[0]
    benchmark_return = (closes[-1] / first_valid - 1) * 100

    # 최대 낙폭(MDD)
    series = pd.Series(total_values)
    running_max = series.cummax()
    drawdown = (series - running_max) / running_max * 100
    mdd = float(drawdown.min())

    timeline = pd.DataFrame({
        "date": dates,
        "close": closes,
        "total_value": total_values,
    }).set_index("date")

    return {
        "success": True,
        "ticker": resolved,
        "name": _display_name(resolved),
        "start_date": str(dates[0].date()),
        "end_date": str(dates[-1].date()),
        "initial_capital": float(initial_capital),
        "final_value": float(final_value),
        "cumulative_return": round(cumulative_return, 2),
        "benchmark_return": round(benchmark_return, 2),
        "excess_return": round(cumulative_return - benchmark_return, 2),
        "mdd": round(mdd, 2),
        "total_trades": trades,
        "timeline": timeline,
        "is_krw": resolved.upper().endswith(".KS") or resolved.upper().endswith(".KQ"),
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  5. 차트 렌더링 (자산 성장 곡선 vs 주가)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def render_backtest_chart(result: dict, out_path: Path = CHART_PATH) -> str | None:
    """자산 성장 곡선 vs 주가 추이 비교 그래프를 PNG 로 저장 (라벨은 폰트 안전용 영문)"""
    try:
        tl = result["timeline"]
        fig, ax1 = plt.subplots(figsize=(7.2, 4.2), dpi=110)
        fig.patch.set_facecolor("#0a0a0a")
        ax1.set_facecolor("#0a0a0a")

        # 자산 성장 곡선
        ax1.plot(tl.index, tl["total_value"], color="#00d4ff", lw=2.0,
                 label="Strategy Asset")
        ax1.set_ylabel("Asset Value", color="#00d4ff", fontsize=9)
        ax1.tick_params(axis="y", colors="#00d4ff", labelsize=7)
        ax1.tick_params(axis="x", colors="#888888", labelsize=7)
        for s in ax1.spines.values():
            s.set_color("#333333")

        # 주가(우측 축)
        ax2 = ax1.twinx()
        ax2.plot(tl.index, tl["close"], color="#ffaa00", lw=1.2, alpha=0.8,
                 label="Price (Buy&Hold)")
        ax2.set_ylabel("Price", color="#ffaa00", fontsize=9)
        ax2.tick_params(axis="y", colors="#ffaa00", labelsize=7)
        for s in ax2.spines.values():
            s.set_color("#333333")

        cur = result["cumulative_return"]
        bm = result["benchmark_return"]
        mdd = result["mdd"]
        title = (f"{result['ticker']}  Strategy {cur:+.1f}%  vs  "
                 f"B&H {bm:+.1f}%   MDD {mdd:.1f}%")
        ax1.set_title(title, color="#ffffff", fontsize=10, pad=10)

        l1, lab1 = ax1.get_legend_handles_labels()
        l2, lab2 = ax2.get_legend_handles_labels()
        ax1.legend(l1 + l2, lab1 + lab2, loc="upper left", fontsize=7,
                   facecolor="#1a1a1a", edgecolor="#333333", labelcolor="#dddddd")

        fig.tight_layout()
        out_path = Path(out_path)
        fig.savefig(str(out_path), facecolor=fig.get_facecolor())
        plt.close(fig)
        _log(f"차트 저장: {out_path}")
        return str(out_path)
    except Exception as e:
        _log(f"차트 렌더링 실패: {e}")
        try:
            plt.close("all")
        except Exception:
            pass
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  6. 표/브리핑 가공
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fmt_money(v: float, is_krw: bool) -> str:
    return f"{v:,.0f}원" if is_krw else f"${v:,.2f}"


def build_report_payload(result: dict, chart_path: str | None,
                         strategy_text: str = "") -> dict:
    """screen_overlay 투사용 표/이미지 데이터 + 음성 브리핑 텍스트 생성"""
    is_krw = result["is_krw"]
    metric_rows = [
        ["누적 수익률", f"{result['cumulative_return']:+.2f}%"],
        ["벤치마크(B&H)", f"{result['benchmark_return']:+.2f}%"],
        ["초과 수익", f"{result['excess_return']:+.2f}%"],
        ["최대 낙폭(MDD)", f"{result['mdd']:.2f}%"],
        ["총 거래 횟수", f"{result['total_trades']}회"],
        ["기간", f"{result['start_date']} ~ {result['end_date']}"],
        ["초기자산", _fmt_money(result["initial_capital"], is_krw)],
        ["최종자산", _fmt_money(result["final_value"], is_krw)],
    ]

    # 우측 반투명 이미지 팝업 (차트 + 핵심 지표 표를 함께 렌더)
    overlay_image = {
        "type": "image",
        "image_path": chart_path or "",
        "title": f"백테스트 | {result['name']}",
        "rows": metric_rows[:6],
    }

    # 텍스트 표 (image 미지원 환경 폴백 + LLM 반환용)
    overlay_table = {
        "type": "table",
        "title": f"백테스트 결과 | {result['name']}",
        "headers": ["항목", "값"],
        "rows": metric_rows,
    }

    cur = result["cumulative_return"]
    excess = result["excess_return"]
    briefing = (
        f"{USER_DISPLAY_NAME}께서 제안하신 전략의 3개년 백테스팅 결과, "
        f"누적 수익률은 {cur:+.2f}%이며 시장 대비 {excess:+.2f}% 초과 수익을 "
        f"{'달성했습니다' if excess >= 0 else '기록했습니다'}. "
        f"최대 낙폭은 {result['mdd']:.2f}%, 총 {result['total_trades']}회 거래되었습니다."
    )

    return {
        "overlay_image": overlay_image,
        "overlay_table": overlay_table,
        "briefing": briefing,
        "metrics": {
            "cumulative_return": result["cumulative_return"],
            "benchmark_return": result["benchmark_return"],
            "excess_return": result["excess_return"],
            "mdd": result["mdd"],
            "total_trades": result["total_trades"],
        },
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  7. 통합 실행 (음성 규칙 → 결과 패키지)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def backtest_from_text(ticker: str, strategy_text: str,
                       initial_capital: float = 10_000_000,
                       years: int = 3) -> dict:
    """음성 규칙 텍스트로부터 파싱→백테스트→차트→표/브리핑까지 일괄 수행"""
    spec = parse_strategy_with_gemini(strategy_text)
    buy_func, sell_func, tp, sl = build_signal_funcs(spec)

    end = datetime.now()
    start = end - timedelta(days=int(years) * 365 + 40)  # 워밍업 여유
    result = run_backtest(
        ticker=ticker,
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
        initial_capital=initial_capital,
        buy_signal_func=buy_func,
        sell_signal_func=sell_func,
        take_profit_pct=tp,
        stop_loss_pct=sl,
    )
    if not result.get("success"):
        return result

    chart_path = render_backtest_chart(result)
    payload = build_report_payload(result, chart_path, strategy_text)

    return {
        "success": True,
        "ticker": result["ticker"],
        "name": result["name"],
        "strategy_spec": spec,
        "chart_path": chart_path,
        **payload,
        "cumulative_return": result["cumulative_return"],
        "benchmark_return": result["benchmark_return"],
        "excess_return": result["excess_return"],
        "mdd": result["mdd"],
        "total_trades": result["total_trades"],
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  8. LLM 도구 정의
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BACKTEST_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_strategy_backtest",
            "description": (
                "사용자가 자연어로 제안한 커스텀 투자 전략을 과거 N개년 데이터에 대입해 "
                "백테스팅합니다. 누적 수익률, 벤치마크(Buy&Hold) 대비 초과 수익, 최대 낙폭(MDD), "
                "거래 횟수를 계산하고 자산 성장 곡선 차트를 생성합니다. "
                "'RSI 30 미만에서 매수하고 10% 수익나면 매도하는 전략 백테스트 해줘' 같은 요청에 사용하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "종목 티커/이름 (예: AAPL, NVDA, 삼성전자, 005930.KS)",
                    },
                    "strategy": {
                        "type": "string",
                        "description": "사용자가 말한 매수/매도 규칙 원문 (예: 'RSI가 30 미만이고 MACD가 시그널을 골든크로스할 때 매수, 10% 수익 시 매도')",
                    },
                    "initial_capital_krw": {
                        "type": "integer",
                        "description": "초기 투자 자산(원). 미언급 시 10000000.",
                    },
                    "years": {
                        "type": "integer",
                        "description": "백테스트 기간(년). 미언급 시 3.",
                    },
                },
                "required": ["ticker", "strategy"],
            },
        },
    },
]


def execute_backtest_tool(tool_name, arguments):
    if tool_name != "run_strategy_backtest":
        return {"success": False, "message": f"알 수 없는 도구: {tool_name}"}
    try:
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
    except Exception:
        arguments = {}

    ticker = (arguments.get("ticker") or "").strip()
    strategy = (arguments.get("strategy") or "").strip()
    if not ticker or not strategy:
        return {"success": False, "message": "ticker 와 strategy 가 필요합니다."}

    cap_raw = arguments.get("initial_capital_krw", 10_000_000)
    try:
        capital = float(cap_raw)
        if capital <= 0:
            capital = 10_000_000
    except (TypeError, ValueError):
        capital = 10_000_000

    years_raw = arguments.get("years", 3)
    try:
        years = int(years_raw)
        if years <= 0:
            years = 3
    except (TypeError, ValueError):
        years = 3

    return backtest_from_text(ticker, strategy, capital, years)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  단독 실행 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    import sys
    tk = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    rule = sys.argv[2] if len(sys.argv) > 2 else "RSI 30 미만에서 매수하고 10% 수익나면 매도"
    print(f"\n=== {tk} 백테스트: {rule} ===")
    res = backtest_from_text(tk, rule)
    if res.get("success"):
        print("전략 spec:", json.dumps(res["strategy_spec"], ensure_ascii=False))
        print("누적수익률:", res["cumulative_return"], "%")
        print("벤치마크:", res["benchmark_return"], "%")
        print("MDD:", res["mdd"], "%  거래:", res["total_trades"], "회")
        print("차트:", res["chart_path"])
        print("브리핑:", res["briefing"])
    else:
        print("실패:", res.get("message"))
