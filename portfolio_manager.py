"""
J.A.R.V.I.S — 가상 포트폴리오 관리자 (로컬 JSON DB + 실시간 자산 평가)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
portfolio.json 에 가상 예수금과 보유 주식을 영구 저장하고,
yfinance 실시간 현재가 + 원달러 환율로 총자산/수익률을 집계한다.

데이터 구조(portfolio.json):
  {
    "available_cash_krw": 10000000,
    "holdings": {
      "AAPL":      {"shares": 10, "avg_price_usd": 175.5},
      "005930.KS": {"shares": 50, "avg_price_krw": 72000}
    }
  }

의존성: pip install yfinance
"""
from __future__ import annotations

from speech_style import USER_DISPLAY_NAME

import os
import json
import threading
from pathlib import Path
from datetime import datetime

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False

# 한글 종목명 → 야후 티커 변환 재사용 (stock_quant 와 동일 컨벤션)
try:
    from stock_quant import _resolve_ticker
except Exception:  # 단독 실행 등 import 실패 시 폴백
    def _resolve_ticker(q): return q


# ──────────────────────────────────────────────
#  설정
# ──────────────────────────────────────────────
PORTFOLIO_FILE   = Path(os.getenv("PORTFOLIO_FILE", "portfolio.json"))
FALLBACK_USDKRW  = float(os.getenv("USDKRW_FALLBACK", "1380"))   # 환율 조회 실패 시 안전값
EXRATE_CACHE_SEC = 600                                          # 환율 캐시 유효 시간(초)

# 티커 → 표시용 한글 이름 (없으면 yfinance shortName 또는 티커 그대로)
_KR_NAME_MAP = {
    "005930.KS": "삼성전자", "000660.KS": "SK하이닉스",
    "035420.KS": "네이버", "035720.KS": "카카오",
    "005380.KS": "현대차", "000270.KS": "기아",
    "373220.KS": "LG에너지솔루션", "207940.KS": "삼성바이오로직스",
    "051910.KS": "LG화학", "006400.KS": "삼성SDI",
}

# 기본 시드 데이터 (파일이 없을 때 생성됨 — 자유롭게 portfolio.json 편집 가능)
_DEFAULT_PORTFOLIO = {
    "available_cash_krw": 10_000_000,
    "holdings": {
        "AAPL":      {"shares": 10, "avg_price_usd": 175.5},
        "005930.KS": {"shares": 50, "avg_price_krw": 72000},
    },
}


def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] 💰 [포트폴리오] {msg}")


def _display_name(ticker: str) -> str:
    return _KR_NAME_MAP.get(ticker.upper(), ticker)


def _is_krw_ticker(ticker: str) -> bool:
    t = ticker.upper()
    return t.endswith(".KS") or t.endswith(".KQ")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. 환율 / 현재가 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_exrate_cache = {"rate": FALLBACK_USDKRW, "ts": 0.0}
_exrate_lock = threading.Lock()


def get_usdkrw_rate() -> float:
    """실시간 원/달러 환율 (yfinance 'USDKRW=X'). 실패 시 안전값 폴백 + 캐시."""
    import time
    with _exrate_lock:
        if time.time() - _exrate_cache["ts"] < EXRATE_CACHE_SEC:
            return _exrate_cache["rate"]

    rate = FALLBACK_USDKRW
    if YF_AVAILABLE:
        try:
            fx = yf.Ticker("USDKRW=X")
            price = 0.0
            try:
                price = float(fx.fast_info.get("last_price", 0) or 0)
            except Exception:
                price = 0.0
            if price <= 0:
                hist = fx.history(period="5d")
                if not hist.empty:
                    price = float(hist["Close"].dropna().iloc[-1])
            if price and price > 0:
                rate = round(price, 2)
        except Exception as e:
            _log(f"환율 조회 실패 → 폴백 {FALLBACK_USDKRW}원 사용 ({e})")

    with _exrate_lock:
        _exrate_cache["rate"] = rate
        _exrate_cache["ts"] = time.time()
    return rate


def _fetch_current_price(ticker: str) -> float:
    """yfinance 로 종목 현재가(native currency)를 가져온다. 실패 시 0."""
    if not YF_AVAILABLE:
        return 0.0
    try:
        tk = yf.Ticker(ticker)
        try:
            price = float(tk.fast_info.get("last_price", 0) or 0)
        except Exception:
            price = 0.0
        if price <= 0:
            hist = tk.history(period="5d")
            if not hist.empty:
                price = float(hist["Close"].dropna().iloc[-1])
        return round(price, 2) if price and price > 0 else 0.0
    except Exception as e:
        _log(f"현재가 조회 실패({ticker}): {e}")
        return 0.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. 포트폴리오 매니저 (로컬 JSON DB)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PortfolioManager:
    """
    portfolio.json 기반 가상 계좌 관리자.
    - 파일이 없으면 기본 시드로 자동 생성/로드
    - 예수금/보유종목 영구 저장
    - 실시간 자산 평가 리포트 생성 (get_portfolio_report)
    """

    def __init__(self, path: Path = PORTFOLIO_FILE):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.data = self._load_or_init()

    # ── 파일 I/O ──
    def _load_or_init(self) -> dict:
        if not self.path.exists():
            _log(f"{self.path} 없음 → 기본 포트폴리오 생성")
            self._write(_DEFAULT_PORTFOLIO)
            return json.loads(json.dumps(_DEFAULT_PORTFOLIO))  # deep copy
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 스키마 보정
            data.setdefault("available_cash_krw", 0)
            data.setdefault("holdings", {})
            _log(f"포트폴리오 로드: 예수금 {data['available_cash_krw']:,}원, "
                 f"보유종목 {len(data['holdings'])}개")
            return data
        except Exception as e:
            _log(f"로드 실패({e}) → 기본값으로 초기화")
            self._write(_DEFAULT_PORTFOLIO)
            return json.loads(json.dumps(_DEFAULT_PORTFOLIO))

    def _write(self, data: dict):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            _log(f"저장 실패: {e}")

    def save(self):
        with self._lock:
            self._write(self.data)

    # ── 계좌 조작 ──
    def deposit_cash(self, amount_krw: float) -> dict:
        """예수금 입출금 (음수면 출금)"""
        with self._lock:
            self.data["available_cash_krw"] += amount_krw
            self._write(self.data)
        return {"success": True, "available_cash_krw": self.data["available_cash_krw"]}

    def record_trade(self, ticker: str, shares: float, price: float,
                     currency: str = "") -> dict:
        """
        매수/매도 기록 (shares 양수=매수, 음수=매도). 평단가는 자동 가중평균.
        currency 미지정 시 티커 접미사로 KRW/USD 자동 판별.
        """
        resolved = _resolve_ticker(ticker)
        is_krw = (currency.upper() == "KRW") or (not currency and _is_krw_ticker(resolved))
        price_key = "avg_price_krw" if is_krw else "avg_price_usd"

        with self._lock:
            holdings = self.data.setdefault("holdings", {})
            h = holdings.get(resolved)

            if shares > 0:  # 매수
                if h and h.get("shares", 0) > 0:
                    old_sh = h["shares"]; old_avg = h.get(price_key, price)
                    new_sh = old_sh + shares
                    new_avg = (old_sh * old_avg + shares * price) / new_sh
                    h["shares"] = new_sh
                    h[price_key] = round(new_avg, 4)
                else:
                    holdings[resolved] = {"shares": shares, price_key: round(price, 4)}
            else:  # 매도
                if not h:
                    return {"success": False, "message": f"{resolved} 보유 없음"}
                h["shares"] = max(0, h.get("shares", 0) + shares)
                if h["shares"] == 0:
                    holdings.pop(resolved, None)

            self._write(self.data)

        return {"success": True, "ticker": resolved,
                "holdings": self.data["holdings"].get(resolved, "청산됨")}

    # ── 자율 리밸런싱 (원격 승인 → 위험 헤어컷 매도로 현금 확보) ──
    def apply_rebalance(self, haircut_pct: float, reason: str = "",
                        tickers: list | None = None) -> dict:
        """위험 신호(매크로/시나리오 MDD)에 따른 자율 리밸런싱.
        보유 종목을 haircut_pct(%) 만큼 현재가로 매도해 예수금으로 현금을 확보한다.

        Args:
          haircut_pct: 감축 비율(0~100). 예: 10 → 각 종목 수량의 10% 매도.
          reason:      감사/브리핑용 사유.
          tickers:     특정 종목만 대상(미지정 시 전 종목).
        Returns: {success, haircut_pct, raised_cash_krw, trades[], cash_after_krw, briefing}
        """
        pct = max(0.0, min(100.0, float(haircut_pct))) / 100.0
        if pct <= 0:
            return {"success": False, "message": "haircut_pct 가 0 이하입니다.", "raised_cash_krw": 0}

        # 1) 현재 보유 스냅샷
        with self._lock:
            holdings = {t: dict(h) for t, h in self.data.get("holdings", {}).items()}
        target = set(_resolve_ticker(t) for t in tickers) if tickers else None

        usdkrw = get_usdkrw_rate()
        plans = []  # (ticker, sell_shares, cur_price, is_krw, proceeds_krw)
        for ticker, h in holdings.items():
            if target is not None and ticker not in target:
                continue
            shares = float(h.get("shares", 0) or 0)
            if shares <= 0:
                continue
            sell = shares * pct
            if sell <= 0:
                continue
            is_krw = "avg_price_krw" in h or _is_krw_ticker(ticker)
            cur = _fetch_current_price(ticker)
            if cur <= 0:
                cur = float(h.get("avg_price_krw" if is_krw else "avg_price_usd",
                                  h.get("avg_price_usd", h.get("avg_price_krw", 0))) or 0)
            fx = 1.0 if is_krw else usdkrw
            proceeds_krw = sell * cur * fx
            plans.append((ticker, sell, cur, is_krw, proceeds_krw))

        if not plans:
            return {"success": False, "message": "감축할 보유 종목이 없습니다.",
                    "raised_cash_krw": 0, "haircut_pct": haircut_pct}

        # 2) 매도 반영 + 현금 확보 (단일 락 구간)
        trades = []
        raised = 0.0
        with self._lock:
            hd = self.data.setdefault("holdings", {})
            for ticker, sell, cur, is_krw, proceeds_krw in plans:
                cur_h = hd.get(ticker)
                if not cur_h:
                    continue
                remaining = max(0.0, float(cur_h.get("shares", 0)) - sell)
                if remaining <= 1e-9:
                    hd.pop(ticker, None)
                else:
                    cur_h["shares"] = round(remaining, 6)
                self.data["available_cash_krw"] = self.data.get("available_cash_krw", 0) + proceeds_krw
                raised += proceeds_krw
                trades.append({"ticker": ticker, "name": _display_name(ticker),
                               "sold_shares": round(sell, 4), "price": cur,
                               "currency": "KRW" if is_krw else "USD",
                               "proceeds_krw": round(proceeds_krw, 0)})
            cash_after = self.data["available_cash_krw"]
            self._write(self.data)

        _log(f"자율 리밸런싱: {len(trades)}종목 {haircut_pct:.0f}% 감축 → "
             f"현금 {raised:,.0f}원 확보 (사유: {reason or '위험 대응'})")

        names = ", ".join(t["name"] for t in trades[:3])
        briefing = (f"원격 승인에 따라 포트폴리오 리밸런싱을 실행했습니다. "
                    f"{names} 등 {len(trades)}종목을 {haircut_pct:.0f}% 축소하여 "
                    f"{raised:,.0f}원의 현금을 확보했습니다. "
                    f"현재 예수금은 {cash_after:,.0f}원입니다.")
        return {"success": True, "haircut_pct": haircut_pct, "reason": reason,
                "raised_cash_krw": round(raised, 0), "trades": trades,
                "cash_after_krw": round(cash_after, 0), "briefing": briefing}

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    #  3. 실시간 자산 평가 + 수익률 연산
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    def get_portfolio_report(self) -> dict:
        """
        보유 종목 실시간 현재가 + 환율을 적용해 원화 기준 자산을 통합 집계한다.

        Returns:
          {
            "success", "cash_krw", "usdkrw",
            "positions": [{ticker,name,shares,avg,current,currency,
                           eval_krw,cost_krw,return_pct}],
            "stock_eval_krw", "stock_cost_krw", "stock_return_pct",
            "total_assets_krw", "total_return_pct",
            "display_data": {...}, "briefing": "..."
          }
        """
        with self._lock:
            cash = float(self.data.get("available_cash_krw", 0))
            holdings = dict(self.data.get("holdings", {}))

        usdkrw = get_usdkrw_rate()

        positions = []
        stock_eval_krw = 0.0
        stock_cost_krw = 0.0

        for ticker, h in holdings.items():
            shares = float(h.get("shares", 0))
            if shares <= 0:
                continue

            is_krw = "avg_price_krw" in h or _is_krw_ticker(ticker)
            avg = float(h.get("avg_price_krw" if is_krw else "avg_price_usd",
                              h.get("avg_price_usd", h.get("avg_price_krw", 0))))
            cur = _fetch_current_price(ticker)
            if cur <= 0:
                cur = avg  # 현재가 조회 실패 시 평단가로 대체(수익률 0 처리)

            # 원화 환산
            fx = 1.0 if is_krw else usdkrw
            eval_krw = shares * cur * fx
            cost_krw = shares * avg * fx
            return_pct = ((cur - avg) / avg * 100) if avg > 0 else 0.0

            stock_eval_krw += eval_krw
            stock_cost_krw += cost_krw

            positions.append({
                "ticker": ticker,
                "name": _display_name(ticker),
                "shares": shares,
                "avg": avg,
                "current": cur,
                "currency": "KRW" if is_krw else "USD",
                "eval_krw": eval_krw,
                "cost_krw": cost_krw,
                "return_pct": round(return_pct, 2),
            })

        stock_return_pct = (((stock_eval_krw - stock_cost_krw) / stock_cost_krw * 100)
                            if stock_cost_krw > 0 else 0.0)
        total_assets = cash + stock_eval_krw
        # 총수익률 = 주식 평가손익 / (현금+주식원가) 기준
        invested_base = cash + stock_cost_krw
        total_return_pct = (((total_assets - invested_base) / invested_base * 100)
                            if invested_base > 0 else 0.0)

        report = {
            "success": True,
            "cash_krw": cash,
            "usdkrw": usdkrw,
            "positions": positions,
            "stock_eval_krw": stock_eval_krw,
            "stock_cost_krw": stock_cost_krw,
            "stock_return_pct": round(stock_return_pct, 2),
            "total_assets_krw": total_assets,
            "total_return_pct": round(total_return_pct, 2),
        }
        report["display_data"] = self._build_display_data(report)
        report["briefing"] = self._build_briefing(report)
        return report

    # ── HUD 표 데이터 (screen_overlay display_table 호환) ──
    def _build_display_data(self, report: dict) -> dict:
        """
        screen_overlay 의 type=table 로 바로 투사 가능한 딕셔너리.
        한글 '?' 깨짐을 막기 위해 통화는 '원'/'$' 만 사용(₩ 미사용),
        수익률은 +/- 부호로 표기해 오버레이가 녹/적색 자동 처리.
        """
        rows = []
        for p in report["positions"]:
            if p["currency"] == "KRW":
                avg_s = f"{p['avg']:,.0f}원"
                cur_s = f"{p['current']:,.0f}원"
            else:
                avg_s = f"${p['avg']:,.2f}"
                cur_s = f"${p['current']:,.2f}"
            eval_s = f"{p['eval_krw']:,.0f}원"
            ret_s = f"{p['return_pct']:+.2f}%"   # +/- → 색상 자동
            rows.append([p["name"], f"{p['shares']:g}주", avg_s, cur_s, eval_s, ret_s])

        # 합계 행
        rows.append([
            "총계",
            f"{len(report['positions'])}종목",
            "-",
            "-",
            f"{report['total_assets_krw']:,.0f}원",
            f"{report['total_return_pct']:+.2f}%",
        ])

        total_s = f"{report['total_assets_krw']:,.0f}"
        ret_s = f"{report['total_return_pct']:+.2f}"
        return {
            "table": {
                "type": "table",
                "title": f"PORTFOLIO | 총자산 {total_s}원 ({ret_s}%)",
                "headers": ["종목", "수량", "평단", "현재가", "평가금액", "수익률"],
                "rows": rows,
            },
            "summary": {
                "available_cash_krw": f"{report['cash_krw']:,.0f}원",
                "stock_eval_krw": f"{report['stock_eval_krw']:,.0f}원",
                "total_assets_krw": f"{report['total_assets_krw']:,.0f}원",
                "stock_return_pct": f"{report['stock_return_pct']:+.2f}%",
                "total_return_pct": f"{report['total_return_pct']:+.2f}%",
                "usdkrw": f"{report['usdkrw']:,.1f}원",
            },
        }

    def _build_briefing(self, report: dict) -> str:
        """차분한 어조의 음성 브리핑 텍스트"""
        total = report["total_assets_krw"]
        ret = report["stock_return_pct"]
        cash = report["cash_krw"]
        n = len(report["positions"])
        msg = (f"{USER_DISPLAY_NAME}의 현재 총자산은 {total:,.0f}원이며, "
               f"주식 평가 수익률은 {ret:+.2f}%를 기록 중입니다.")
        if n > 0:
            best = max(report["positions"], key=lambda x: x["return_pct"])
            msg += (f" 보유 {n}종목 중 가장 양호한 종목은 {best['name']}, "
                    f"수익률 {best['return_pct']:+.2f}%입니다. "
                    f"예수금은 {cash:,.0f}원 남아 있습니다.")
        return msg


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. LLM 도구 정의
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PORTFOLIO_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "report_portfolio_status",
            "description": (
                "사용자의 가상 계좌(예수금 + 보유 주식)의 실시간 자산 평가 리포트를 생성합니다. "
                "현재가와 원달러 환율을 적용해 총자산과 수익률을 집계합니다. "
                "'내 계좌 상태 보고해', '내 자산 얼마야', '수익률 어때', '포트폴리오 알려줘' 같은 요청에 사용하세요."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_stock_trade",
            "description": (
                "가상 계좌에 매수/매도를 기록하고 평단가를 자동 갱신합니다. "
                "'애플 10주 175달러에 샀어', '삼성전자 20주 7만원에 매수', '엔비디아 5주 팔아' 같은 요청에 사용."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "종목 티커/이름 (예: AAPL, 삼성전자, 005930.KS)"},
                    "shares": {"type": "number", "description": "수량. 매수는 양수, 매도는 음수."},
                    "price": {"type": "number", "description": "체결 단가 (해당 종목 통화 기준: 미국=달러, 한국=원)"},
                    "currency": {"type": "string", "description": "통화 KRW 또는 USD (미지정 시 티커로 자동판별)", "enum": ["KRW", "USD"]},
                },
                "required": ["ticker", "shares", "price"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "deposit_portfolio_cash",
            "description": "가상 계좌의 예수금을 입금(양수) 또는 출금(음수)합니다. '예수금 500만원 입금해' 등.",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount_krw": {"type": "integer", "description": "입출금액(원). 출금은 음수."},
                },
                "required": ["amount_krw"],
            },
        },
    },
]

_pf_instance: PortfolioManager | None = None


def set_portfolio_instance(inst: PortfolioManager):
    global _pf_instance
    _pf_instance = inst


def execute_portfolio_tool(tool_name, arguments):
    global _pf_instance
    if not _pf_instance:
        return {"success": False, "message": "포트폴리오 매니저 없음"}
    try:
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
    except Exception:
        arguments = {}

    if tool_name == "report_portfolio_status":
        report = _pf_instance.get_portfolio_report()
        if not report.get("success"):
            return report
        return {
            "success": True,
            "briefing": report["briefing"],
            "total_assets_krw": report["total_assets_krw"],
            "stock_return_pct": report["stock_return_pct"],
            "total_return_pct": report["total_return_pct"],
            "usdkrw": report["usdkrw"],
            # 메인 루프가 자동 투사할 표
            "overlay_table": report["display_data"]["table"],
            "display_summary": report["display_data"]["summary"],
        }

    if tool_name == "record_stock_trade":
        ticker = (arguments.get("ticker") or "").strip()
        shares = float(arguments.get("shares", 0) or 0)
        price = float(arguments.get("price", 0) or 0)
        currency = (arguments.get("currency") or "").strip()
        if not ticker or shares == 0 or price <= 0:
            return {"success": False, "message": "ticker, shares, price 가 필요합니다."}
        res = _pf_instance.record_trade(ticker, shares, price, currency)
        if res.get("success"):
            verb = "매수" if shares > 0 else "매도"
            res["message"] = f"{ticker} {abs(shares):g}주 {verb} 기록 완료, {USER_DISPLAY_NAME}."
        return res

    if tool_name == "deposit_portfolio_cash":
        amount = float(arguments.get("amount_krw", 0) or 0)
        if amount == 0:
            return {"success": False, "message": "amount_krw 가 필요합니다."}
        res = _pf_instance.deposit_cash(amount)
        verb = "입금" if amount > 0 else "출금"
        res["message"] = f"예수금 {abs(amount):,.0f}원 {verb} 완료. 현재 예수금 {res['available_cash_krw']:,.0f}원입니다."
        return res

    return {"success": False, "message": f"알 수 없는 도구: {tool_name}"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  단독 실행 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    pm = PortfolioManager()
    print("\n=== 실시간 포트폴리오 리포트 ===")
    rep = pm.get_portfolio_report()
    print("환율(USDKRW):", rep["usdkrw"])
    for p in rep["positions"]:
        print(f"  {p['name']:<14} {p['shares']:g}주 평단 {p['avg']} → 현재 {p['current']} "
              f"({p['return_pct']:+.2f}%) 평가 {p['eval_krw']:,.0f}원")
    print(f"\n총자산: {rep['total_assets_krw']:,.0f}원 | 주식수익률 {rep['stock_return_pct']:+.2f}%")
    print("\n[브리핑]", rep["briefing"])
