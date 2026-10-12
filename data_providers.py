"""
J.A.R.V.I.S — 통합 주식/공시 데이터 프로바이더
────────────────────────────────────────────────
한 곳에서 10개 데이터 소스를 캡슐화한다.

  ▸ AlphaVantageProvider   ALPHAVANTAGE_API_KEY (무료 티어)
  ▸ YahooFinanceProvider   (키 불필요 — yfinance 사용)
  ▸ FMPProvider            FMP_API_KEY (Financial Modeling Prep, 무료 티어)
  ▸ KISProvider            KIS_APP_KEY / KIS_APP_SECRET / KIS_ENV(real|virtual)
                           한국투자증권 OpenAPI — REST
  ▸ LSProvider             LS_APP_KEY / LS_APP_SECRET
                           LS증권 Open API — REST (XingAPI 후계)
  ▸ KiwoomProvider         KIWOOM_APP_KEY / KIWOOM_APP_SECRET / KIWOOM_ENV(real|mock)
                           키움증권 Next API — REST
                           ※ 레거시 영웅문 Open API+(ActiveX/COM)은 Windows 전용이라
                             macOS에서 동작 불가 → Next API(REST)만 지원.
  ▸ DARTProvider           DART_API_KEY (금감원 OpenDART, 무료)
  ▸ StooqProvider          (키 불필요 — 글로벌 시세 CSV)
  ▸ NaverProvider          (키 불필요 — 한국 시세·PER/PBR/EPS)
  ▸ FinnhubProvider        FINNHUB_API_KEY (무료 티어 60req/분)

설계 원칙
  · 키 누락 시 enabled=False 로 깔끔히 비활성 (예외 X)
  · 모든 응답: {"success": bool, ...payload} 형태로 통일
  · 토큰 발급 프로바이더는 만료 60초 전 자동 갱신 + 락
  · 실제 거래(매수/매도) 미구현 — 시세·공시 조회 전용
"""
from __future__ import annotations

import os
import io
import re
import time
import json
import hashlib
import threading
import zipfile
from datetime import datetime, timedelta

HTTP_TIMEOUT = 10

# 보안: stdlib xml.etree는 billion-laughs(엔티티 폭발)에 취약. defusedxml로 대체.
try:
    from defusedxml import ElementTree as ET   # type: ignore
    _XML_HARDENED = True
except ImportError:
    from xml.etree import ElementTree as ET    # 폴백 — 경고는 _load_corp_codes 호출 시
    _XML_HARDENED = False

try:
    import net as _net
    def _http_get(url, **kwargs):
        kwargs.setdefault("timeout", HTTP_TIMEOUT)
        return _net.get(url, **kwargs)
    def _http_post(url, **kwargs):
        kwargs.setdefault("timeout", HTTP_TIMEOUT)
        return _net.post(url, **kwargs)
except ImportError:
    import requests as _requests
    def _http_get(url, **kwargs):
        kwargs.setdefault("timeout", HTTP_TIMEOUT)
        return __http_get(url, **kwargs)
    def _http_post(url, **kwargs):
        kwargs.setdefault("timeout", HTTP_TIMEOUT)
        return __http_post(url, **kwargs)

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from secure_config import get_secret as _get_secret
except Exception:
    def _get_secret(key, default=""):
        return os.environ.get(key, default)


def _log(provider: str, msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] 📊 [{provider}] {msg}")


# ── 보안: API 키 / Bearer 토큰을 예외·로그 문자열에서 제거 ──────────
# requests.HTTPError는 "...for url: https://...?apikey=ABCD" 형태로 키를 노출함.
# 이 문자열이 _err(str(e)) → LLM 도구 결과 → chat_log → HUD/TTS 로 전파되면 키 유출.
_SECRET_QPARAM = re.compile(
    r"((?:apikey|api_key|crtfc_key|appkey|app_key|appsecret|app_secret|"
    r"secretkey|secret_key|appsecretkey|app_secret_key|access_token|token|"
    r"authkey|auth_key|servicekey|service_key|refresh_token|client_secret|"
    r"password|passwd|pwd|hashkey|hash_key|signature)=)"
    r"[^&\s'\"]*",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE)


def _redact(text):
    if not text:
        return text
    s = str(text)
    s = _SECRET_QPARAM.sub(r"\1***REDACTED***", s)
    s = _BEARER.sub(r"\1***REDACTED***", s)
    return s


def _err(msg):
    return {"success": False, "error": _redact(msg)}


# ── 보안: 사용자 입력을 URL path에 보간하기 전 형식 검증 (CWE-23) ──
_TICKER_RE = re.compile(r"^[A-Za-z0-9.\-^=:]{1,20}$")
_KR_CODE_RE = re.compile(r"^\d{6,8}$")   # 6자리(주식)·8자리(corp_code)


def _valid_ticker(s) -> bool:
    return isinstance(s, str) and ".." not in s and "/" not in s \
           and "\\" not in s and bool(_TICKER_RE.match(s))


def _valid_kr_code(s) -> bool:
    return isinstance(s, str) and bool(_KR_CODE_RE.match(s))


# ══════════════════════════════════════════════════════════════
#  Alpha Vantage — 글로벌 시세 / 펀더멘털 / FX
# ══════════════════════════════════════════════════════════════
class AlphaVantageProvider:
    BASE = "https://www.alphavantage.co/query"

    def __init__(self):
        self.key = _get_secret("ALPHAVANTAGE_API_KEY")
        self.enabled = bool(self.key)

    def _call(self, **params):
        if not self.enabled:
            return _err("ALPHAVANTAGE_API_KEY 미설정")
        params["apikey"] = self.key
        try:
            r = _http_get(self.BASE, params=params, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            d = r.json()
            if "Note" in d:           # rate limit
                return _err(f"Alpha Vantage 한도: {d['Note']}")
            if "Information" in d and "Thank" in d.get("Information", ""):
                return _err(f"Alpha Vantage 한도: {d['Information']}")
            if "Error Message" in d:
                return _err(d["Error Message"])
            return {"success": True, "data": d}
        except Exception as e:
            return _err(str(e))

    def quote(self, symbol: str) -> dict:
        r = self._call(function="GLOBAL_QUOTE", symbol=symbol)
        if not r["success"]:
            return r
        q = r["data"].get("Global Quote", {}) or {}
        if not q:
            return _err("응답 비어있음")
        return {"success": True, "symbol": q.get("01. symbol"),
                "price": float(q.get("05. price") or 0),
                "open": float(q.get("02. open") or 0),
                "high": float(q.get("03. high") or 0),
                "low":  float(q.get("04. low") or 0),
                "volume": int(float(q.get("06. volume") or 0)),
                "change": float(q.get("09. change") or 0),
                "change_pct": q.get("10. change percent", ""),
                "raw": q}

    def overview(self, symbol: str) -> dict:
        """기업 펀더멘털 (시총/PER/PBR/EPS/배당 등)"""
        return self._call(function="OVERVIEW", symbol=symbol)

    def daily(self, symbol: str, full: bool = False) -> dict:
        return self._call(function="TIME_SERIES_DAILY", symbol=symbol,
                          outputsize="full" if full else "compact")

    def intraday(self, symbol: str, interval: str = "5min") -> dict:
        return self._call(function="TIME_SERIES_INTRADAY", symbol=symbol, interval=interval)

    def search(self, keywords: str) -> dict:
        return self._call(function="SYMBOL_SEARCH", keywords=keywords)

    def fx(self, from_: str, to: str) -> dict:
        return self._call(function="CURRENCY_EXCHANGE_RATE",
                          from_currency=from_, to_currency=to)


# ══════════════════════════════════════════════════════════════
#  Yahoo Finance — yfinance 래퍼 (키 불필요)
# ══════════════════════════════════════════════════════════════
class YahooFinanceProvider:
    def __init__(self):
        try:
            import yfinance as yf
            self._yf = yf
            self.enabled = True
        except ImportError:
            self._yf = None
            self.enabled = False

    def quote(self, ticker: str) -> dict:
        if not self.enabled:
            return _err("yfinance 미설치")
        try:
            tk = self._yf.Ticker(ticker)
            price = None
            fi = getattr(tk, "fast_info", None)
            if fi:
                price = (getattr(fi, "last_price", None)
                         or getattr(fi, "regular_market_price", None))
            if price is None:
                h = tk.history(period="1d")
                if not h.empty:
                    price = float(h["Close"].iloc[-1])
            return {"success": True, "ticker": ticker,
                    "price": float(price) if price is not None else None}
        except Exception as e:
            return _err(str(e))

    def history(self, ticker: str, period: str = "1mo", interval: str = "1d") -> dict:
        if not self.enabled:
            return _err("yfinance 미설치")
        try:
            h = self._yf.Ticker(ticker).history(period=period, interval=interval)
            rows = h.reset_index().to_dict(orient="records")
            for r in rows:
                if "Date" in r and hasattr(r["Date"], "isoformat"):
                    r["Date"] = r["Date"].isoformat()
                if "Datetime" in r and hasattr(r["Datetime"], "isoformat"):
                    r["Datetime"] = r["Datetime"].isoformat()
            return {"success": True, "ticker": ticker, "count": len(rows), "rows": rows}
        except Exception as e:
            return _err(str(e))

    def info(self, ticker: str) -> dict:
        if not self.enabled:
            return _err("yfinance 미설치")
        try:
            d = dict(getattr(self._yf.Ticker(ticker), "info", {}) or {})
            return {"success": True, "ticker": ticker, "info": d}
        except Exception as e:
            return _err(str(e))


# ══════════════════════════════════════════════════════════════
#  Financial Modeling Prep — 미국 중심 펀더멘털·재무제표
# ══════════════════════════════════════════════════════════════
class FMPProvider:
    # FMP는 레거시 /api/v3(경로형 심볼)를 폐기하고 /stable(쿼리 파라미터 심볼)로 통합.
    # 신규 무료 키는 v3에서 403을 반환하므로 stable 엔드포인트를 사용한다.
    BASE = "https://financialmodelingprep.com/stable"

    def __init__(self):
        self.key = _get_secret("FMP_API_KEY")
        self.enabled = bool(self.key)

    def _get(self, path: str, **params) -> dict:
        if not self.enabled:
            return _err("FMP_API_KEY 미설정")
        params["apikey"] = self.key
        try:
            r = _http_get(f"{self.BASE}{path}", params=params, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            d = r.json()
            if isinstance(d, dict) and (d.get("Error Message") or d.get("error")):
                return _err(d.get("Error Message") or d.get("error"))
            return {"success": True, "data": d}
        except Exception as e:
            return _err(str(e))

    def quote(self, symbol: str) -> dict:
        if not _valid_ticker(symbol):
            return _err(f"잘못된 종목 심볼: {symbol!r}")
        r = self._get("/quote", symbol=symbol)
        if not r["success"]:
            return r
        rows = r["data"] or []
        if not rows:
            return _err("응답 비어있음")
        q = rows[0]
        return {"success": True, "symbol": q.get("symbol"), "name": q.get("name"),
                "price": q.get("price"), "change": q.get("change"),
                # stable: changePercentage / 레거시: changesPercentage 둘 다 대응
                "change_pct": q.get("changePercentage", q.get("changesPercentage")),
                "volume": q.get("volume"), "market_cap": q.get("marketCap"),
                "pe": q.get("pe"), "eps": q.get("eps"), "raw": q}

    def profile(self, symbol: str) -> dict:
        if not _valid_ticker(symbol):
            return _err(f"잘못된 종목 심볼: {symbol!r}")
        return self._get("/profile", symbol=symbol)

    def income(self, symbol: str, limit: int = 4) -> dict:
        if not _valid_ticker(symbol):
            return _err(f"잘못된 종목 심볼: {symbol!r}")
        return self._get("/income-statement", symbol=symbol, limit=limit)

    def balance(self, symbol: str, limit: int = 4) -> dict:
        if not _valid_ticker(symbol):
            return _err(f"잘못된 종목 심볼: {symbol!r}")
        return self._get("/balance-sheet-statement", symbol=symbol, limit=limit)

    def cash_flow(self, symbol: str, limit: int = 4) -> dict:
        if not _valid_ticker(symbol):
            return _err(f"잘못된 종목 심볼: {symbol!r}")
        return self._get("/cash-flow-statement", symbol=symbol, limit=limit)

    def ratios_ttm(self, symbol: str) -> dict:
        if not _valid_ticker(symbol):
            return _err(f"잘못된 종목 심볼: {symbol!r}")
        return self._get("/ratios-ttm", symbol=symbol)

    def search(self, query: str, limit: int = 10) -> dict:
        return self._get("/search-symbol", query=query, limit=limit)


# ══════════════════════════════════════════════════════════════
#  공통: OAuth2 토큰 캐시 베이스
# ══════════════════════════════════════════════════════════════
class _TokenCached:
    """OAuth2 액세스 토큰 캐시 (인메모리 + 디스크).

    KIS·LS 등은 토큰 발급 자체에 레이트리밋(예: KIS 1분 1회)이 걸려 있어,
    프로세스가 재시작될 때마다 새 토큰을 요청하면 403(EGW00133)이 난다.
    24시간 유효한 토큰을 디스크에 캐시해 프로세스 간 재사용한다.
    파일명은 앱키 해시라서 원문 키는 절대 노출되지 않는다.
    """

    _CACHE_DIR = os.path.join(
        os.environ.get("JARVIS_TOKEN_DIR") or os.path.expanduser("~/.jarvis/tokens")
    )

    def __init__(self):
        self._token = None
        self._token_exp = 0.0
        self._lock = threading.Lock()
        self._cache_tag: str | None = None   # 하위 클래스가 앱키 해시로 설정

    def _token_valid(self) -> bool:
        return bool(self._token) and time.time() < self._token_exp - 60

    @staticmethod
    def _tag_for(prefix: str, *parts: str) -> str:
        """앱키 등 민감정보를 해시해 캐시 파일 태그 생성 (원문 미노출)."""
        raw = "|".join(p or "" for p in parts).encode("utf-8")
        return f"{prefix}_{hashlib.sha256(raw).hexdigest()[:16]}"

    def _cache_file(self) -> str | None:
        if not self._cache_tag:
            return None
        try:
            os.makedirs(self._CACHE_DIR, mode=0o700, exist_ok=True)
        except Exception:
            return None
        return os.path.join(self._CACHE_DIR, f"{self._cache_tag}.json")

    def _load_disk_token(self) -> bool:
        path = self._cache_file()
        if not path or not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            tok, exp = d.get("token"), float(d.get("exp", 0))
            if tok and time.time() < exp - 60:
                self._token, self._token_exp = tok, exp
                return True
        except Exception:
            return False
        return False

    def _save_disk_token(self) -> None:
        path = self._cache_file()
        if not path or not self._token:
            return
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"token": self._token, "exp": self._token_exp}, f)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════
#  한국투자증권 (KIS) OpenAPI
# ══════════════════════════════════════════════════════════════
class KISProvider(_TokenCached):
    """국내/해외 주식 시세 조회 (조회 한정). 거래 미구현."""

    def __init__(self):
        super().__init__()
        self.app_key = _get_secret("KIS_APP_KEY")
        self.app_secret = _get_secret("KIS_APP_SECRET")
        self.account_no = _get_secret("KIS_ACCOUNT_NO")   # 옵션
        env = (_get_secret("KIS_ENV") or "real").lower()
        self.is_virtual = env in ("virtual", "vts", "mock")
        self.base = ("https://openapivts.koreainvestment.com:29443" if self.is_virtual
                     else "https://openapi.koreainvestment.com:9443")
        self.enabled = bool(self.app_key and self.app_secret)
        # 토큰 디스크 캐시 태그: 앱키+환경별로 분리 (원문 키는 해시라 미노출)
        self._cache_tag = self._tag_for(
            "kis_vts" if self.is_virtual else "kis_real", self.app_key or "")

    def _get_token(self) -> str:
        with self._lock:
            if self._token_valid():
                return self._token
            if self._load_disk_token():
                return self._token
            r = _http_post(f"{self.base}/oauth2/tokenP",
                              json={"grant_type": "client_credentials",
                                    "appkey": self.app_key,
                                    "appsecret": self.app_secret},
                              timeout=HTTP_TIMEOUT)
            if r.status_code != 200:
                # KIS는 실패 사유를 JSON 본문(error_code/error_description)에 담아줌.
                # raise_for_status()의 맨 403 대신 실제 사유를 노출한다.
                body = {}
                try:
                    body = r.json()
                except Exception:
                    pass
                code = body.get("error_code") or body.get("msg_cd") or f"HTTP {r.status_code}"
                desc = (body.get("error_description") or body.get("msg1")
                        or _redact(r.text[:200]) or "사유 미상")
                hint = ""
                if str(code) == "EGW00133":
                    hint = " (토큰 발급 1분 1회 제한 — 잠시 후 자동 해소)"
                elif str(code) in ("EGW00121", "EGW00201"):
                    env_now = "모의" if self.is_virtual else "실전"
                    hint = (f" (현재 '{env_now}' 환경으로 요청 중 — 키가 반대 환경용이면 "
                            f"KIS_ENV를 바꿔야 함)")
                raise RuntimeError(f"KIS 토큰발급 실패 [{code}] {desc}{hint}")
            d = r.json()
            tok = d.get("access_token")
            if not tok:
                raise RuntimeError(f"KIS 토큰 응답에 access_token 없음: {_redact(str(d)[:200])}")
            self._token = tok
            self._token_exp = time.time() + int(d.get("expires_in", 86400))
            self._save_disk_token()
            _log("KIS", f"토큰 발급 ({'모의' if self.is_virtual else '실전'}, 24h 캐시)")
            return self._token

    def _headers(self, tr_id: str) -> dict:
        # 모의투자는 tr_id 앞에 'V' (조회 TR은 동일하기도 함 — 매뉴얼 참조)
        return {"authorization": f"Bearer {self._get_token()}",
                "appkey": self.app_key, "appsecret": self.app_secret,
                "tr_id": tr_id,
                "Content-Type": "application/json; charset=utf-8"}

    def quote(self, code: str) -> dict:
        """국내주식 현재가 — code: 6자리 종목코드 (예: '005930')"""
        if not self.enabled:
            return _err("KIS_APP_KEY/SECRET 미설정")
        if not _valid_kr_code(code):
            return _err(f"잘못된 종목코드: {code!r}")
        try:
            r = _http_get(
                f"{self.base}/uapi/domestic-stock/v1/quotations/inquire-price",
                headers=self._headers("FHKST01010100"),
                params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
                timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            d = r.json()
            if d.get("rt_cd") != "0":
                return _err(f"KIS {d.get('msg_cd')}: {d.get('msg1')}")
            o = d.get("output") or {}
            return {"success": True, "code": code, "name": o.get("hts_kor_isnm"),
                    "price": float(o.get("stck_prpr") or 0),
                    "change": float(o.get("prdy_vrss") or 0),
                    "change_pct": float(o.get("prdy_ctrt") or 0),
                    "volume": int(float(o.get("acml_vol") or 0)),
                    "open": float(o.get("stck_oprc") or 0),
                    "high": float(o.get("stck_hgpr") or 0),
                    "low": float(o.get("stck_lwpr") or 0),
                    "per": float(o.get("per") or 0),
                    "pbr": float(o.get("pbr") or 0),
                    "raw": o}
        except Exception as e:
            return _err(str(e))

    def chart_daily(self, code: str, days: int = 30) -> dict:
        """국내주식 일봉 (최근 days 영업일)."""
        if not self.enabled:
            return _err("KIS_APP_KEY/SECRET 미설정")
        if not _valid_kr_code(code):
            return _err(f"잘못된 종목코드: {code!r}")
        try:
            today = datetime.now()
            start = (today - timedelta(days=days * 2)).strftime("%Y%m%d")  # 주말/공휴일 보정
            end = today.strftime("%Y%m%d")
            r = _http_get(
                f"{self.base}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                headers=self._headers("FHKST03010100"),
                params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                        "FID_INPUT_DATE_1": start, "FID_INPUT_DATE_2": end,
                        "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"},
                timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            d = r.json()
            if d.get("rt_cd") != "0":
                return _err(f"KIS {d.get('msg_cd')}: {d.get('msg1')}")
            rows = (d.get("output2") or [])[:days]
            return {"success": True, "code": code, "count": len(rows), "rows": rows}
        except Exception as e:
            return _err(str(e))

    def overseas_quote(self, exch: str, symbol: str) -> dict:
        """해외주식 현재가. exch ∈ {'NAS','NYS','HKS','TSE'...}; symbol 예: 'TSLA'"""
        if not self.enabled:
            return _err("KIS_APP_KEY/SECRET 미설정")
        if not (isinstance(exch, str) and re.match(r"^[A-Z]{2,5}$", exch)):
            return _err(f"잘못된 거래소 코드: {exch!r}")
        if not _valid_ticker(symbol):
            return _err(f"잘못된 종목 심볼: {symbol!r}")
        try:
            r = _http_get(
                f"{self.base}/uapi/overseas-price/v1/quotations/price",
                headers=self._headers("HHDFS00000300"),
                params={"AUTH": "", "EXCD": exch, "SYMB": symbol},
                timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            d = r.json()
            if d.get("rt_cd") != "0":
                return _err(f"KIS {d.get('msg_cd')}: {d.get('msg1')}")
            o = d.get("output") or {}
            return {"success": True, "symbol": symbol, "exch": exch,
                    "price": float(o.get("last") or 0),
                    "change_pct": float(o.get("rate") or 0),
                    "volume": int(float(o.get("tvol") or 0)),
                    "raw": o}
        except Exception as e:
            return _err(str(e))


# ══════════════════════════════════════════════════════════════
#  LS증권 Open API (구 이베스트 XingAPI 후계)
# ══════════════════════════════════════════════════════════════
class LSProvider(_TokenCached):
    """LS증권 Open API — TR(거래) 기반 REST. 시세 조회 중심.
    XingAPI(레거시 DLL)는 Windows 전용이라 미지원."""
    BASE = "https://openapi.ls-sec.co.kr:8080"

    def __init__(self):
        super().__init__()
        self.app_key = _get_secret("LS_APP_KEY")
        self.app_secret = _get_secret("LS_APP_SECRET")
        self.enabled = bool(self.app_key and self.app_secret)

    def _get_token(self) -> str:
        with self._lock:
            if self._token_valid():
                return self._token
            r = _http_post(
                f"{self.BASE}/oauth2/token",
                data={"grant_type": "client_credentials",
                      "appkey": self.app_key,
                      "appsecretkey": self.app_secret,
                      "scope": "oob"},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            d = r.json()
            self._token = d["access_token"]
            self._token_exp = time.time() + int(d.get("expires_in", 86400))
            _log("LS", "토큰 발급")
            return self._token

    def _call_tr(self, tr_path: str, tr_code: str, body: dict) -> dict:
        """LS TR 호출. tr_path: 카테고리 경로 (예: '/stock/market-data')"""
        if not self.enabled:
            return _err("LS_APP_KEY/SECRET 미설정")
        try:
            r = _http_post(
                f"{self.BASE}{tr_path}",
                headers={"content-type": "application/json; charset=utf-8",
                         "authorization": f"Bearer {self._get_token()}",
                         "tr_cd": tr_code, "tr_cont": "N"},
                json=body, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            return {"success": True, "data": r.json()}
        except Exception as e:
            return _err(str(e))

    def quote(self, code: str) -> dict:
        """t1102 — 주식 현재가 시세조회. code 6자리."""
        if not _valid_kr_code(code):
            return _err(f"잘못된 종목코드: {code!r}")
        r = self._call_tr("/stock/market-data", "t1102",
                          {"t1102InBlock": {"shcode": code}})
        if not r["success"]:
            return r
        o = (r["data"] or {}).get("t1102OutBlock", {}) or {}
        if not o:
            return _err("응답 비어있음")
        return {"success": True, "code": code, "name": o.get("hname"),
                "price": float(o.get("price") or 0),
                "change": float(o.get("change") or 0),
                "change_pct": float(o.get("diff") or 0),
                "volume": int(float(o.get("volume") or 0)),
                "open": float(o.get("open") or 0),
                "high": float(o.get("high") or 0),
                "low": float(o.get("low") or 0),
                "raw": o}

    def chart_daily(self, code: str, days: int = 30) -> dict:
        """t8410 — 주식 일/주/월/년 차트. gubun=2(일봉)."""
        if not _valid_kr_code(code):
            return _err(f"잘못된 종목코드: {code!r}")
        today = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
        r = self._call_tr("/stock/chart", "t8410",
                          {"t8410InBlock": {"shcode": code, "gubun": "2", "qrycnt": days,
                                            "sdate": start, "edate": today,
                                            "comp_yn": "N", "sujung": "N"}})
        if not r["success"]:
            return r
        rows = (r["data"] or {}).get("t8410OutBlock1", []) or []
        return {"success": True, "code": code, "count": len(rows), "rows": rows[:days]}


# ══════════════════════════════════════════════════════════════
#  키움증권 Next API (REST) — 레거시 영웅문 Open API+(ActiveX)는 Windows 전용이라 미지원
# ══════════════════════════════════════════════════════════════
class KiwoomProvider(_TokenCached):
    """키움 Next API — REST. 시세/공시 조회 중심.
    영웅문 Open API+(ActiveX/COM)는 Windows + 영웅문 설치 필요 → macOS에서 사용 불가."""

    def __init__(self):
        super().__init__()
        self.app_key = _get_secret("KIWOOM_APP_KEY")
        self.app_secret = _get_secret("KIWOOM_APP_SECRET")
        env = (_get_secret("KIWOOM_ENV") or "real").lower()
        self.is_mock = env in ("mock", "virtual")
        self.base = "https://mockapi.kiwoom.com" if self.is_mock else "https://api.kiwoom.com"
        self.enabled = bool(self.app_key and self.app_secret)

    def _get_token(self) -> str:
        with self._lock:
            if self._token_valid():
                return self._token
            r = _http_post(
                f"{self.base}/oauth2/token",
                json={"grant_type": "client_credentials",
                      "appkey": self.app_key, "secretkey": self.app_secret},
                headers={"Content-Type": "application/json;charset=UTF-8"},
                timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            d = r.json()
            self._token = d.get("token") or d.get("access_token")
            if not self._token:
                raise RuntimeError(f"키움 토큰 응답 이상: {d}")
            self._token_exp = time.time() + int(d.get("expires_in", 86400))
            _log("Kiwoom", f"토큰 발급 ({'모의' if self.is_mock else '실전'})")
            return self._token

    def _call(self, api_id: str, payload: dict, path: str = "/api/dostk/stkinfo") -> dict:
        if not self.enabled:
            return _err("KIWOOM_APP_KEY/SECRET 미설정")
        try:
            r = _http_post(
                f"{self.base}{path}",
                headers={"Content-Type": "application/json;charset=UTF-8",
                         "authorization": f"Bearer {self._get_token()}",
                         "api-id": api_id, "cont-yn": "N", "next-key": ""},
                json=payload, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            d = r.json()
            if str(d.get("return_code", "0")) not in ("0", ""):
                return _err(f"Kiwoom {d.get('return_code')}: {d.get('return_msg')}")
            return {"success": True, "data": d}
        except Exception as e:
            return _err(str(e))

    def quote(self, code: str) -> dict:
        """ka10001 — 주식기본정보. code 6자리."""
        if not _valid_kr_code(code):
            return _err(f"잘못된 종목코드: {code!r}")
        r = self._call("ka10001", {"stk_cd": code})
        if not r["success"]:
            return r
        d = r["data"]
        return {"success": True, "code": code, "name": d.get("stk_nm"),
                "price": float(d.get("cur_prc") or 0),
                "change": float(d.get("pred_diff") or 0),
                "change_pct": float(d.get("flu_rt") or 0),
                "volume": int(float(d.get("trde_qty") or 0)),
                "raw": d}

    def chart_daily(self, code: str, days: int = 30) -> dict:
        """ka10081 — 주식 일봉 차트."""
        if not _valid_kr_code(code):
            return _err(f"잘못된 종목코드: {code!r}")
        today = datetime.now().strftime("%Y%m%d")
        r = self._call("ka10081",
                       {"stk_cd": code, "base_dt": today, "upd_stkpc_tp": "1"},
                       path="/api/dostk/chart")
        if not r["success"]:
            return r
        rows = (r["data"] or {}).get("stk_dt_pole_chart_qry", []) or []
        return {"success": True, "code": code, "count": len(rows), "rows": rows[:days]}


# ══════════════════════════════════════════════════════════════
#  금감원 Open DART — 공시 / 회사정보 / 재무제표
# ══════════════════════════════════════════════════════════════
class DARTProvider:
    BASE = "https://opendart.fss.or.kr/api"

    def __init__(self):
        self.key = _get_secret("DART_API_KEY")
        self.enabled = bool(self.key)
        self._corp_map: dict | None = None
        self._corp_by_stock: dict | None = None
        self._lock = threading.Lock()

    def _get(self, path: str, **params) -> dict:
        if not self.enabled:
            return _err("DART_API_KEY 미설정")
        params["crtfc_key"] = self.key
        try:
            r = _http_get(f"{self.BASE}{path}", params=params, timeout=15)
            r.raise_for_status()
            d = r.json()
            # 000=정상, 013=조회결과없음 (실패 아님), 그 외 오류
            st = d.get("status")
            if st and st not in ("000", "013"):
                return _err(f"DART {st}: {d.get('message')}")
            return {"success": True, "data": d}
        except Exception as e:
            return _err(str(e))

    def disclosures(self, corp_code: str | None = None,
                    bgn_de: str | None = None, end_de: str | None = None,
                    page: int = 1, count: int = 20) -> dict:
        """공시검색. 날짜는 'YYYYMMDD'."""
        if corp_code is not None and not _valid_kr_code(corp_code):
            return _err(f"잘못된 corp_code: {corp_code!r}")
        for label, v in (("bgn_de", bgn_de), ("end_de", end_de)):
            if v is not None and not re.match(r"^\d{8}$", str(v)):
                return _err(f"잘못된 {label}: {v!r}")
        p: dict = {"page_no": int(page), "page_count": int(count)}
        if corp_code: p["corp_code"] = corp_code
        if bgn_de:    p["bgn_de"] = bgn_de
        if end_de:    p["end_de"] = end_de
        return self._get("/list.json", **p)

    def company(self, corp_code: str) -> dict:
        if not _valid_kr_code(corp_code):
            return _err(f"잘못된 corp_code: {corp_code!r}")
        return self._get("/company.json", corp_code=corp_code)

    def financial(self, corp_code: str, year: int, report: str = "11011") -> dict:
        """단일회사 전체 재무제표.
        report: 11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기"""
        if not _valid_kr_code(corp_code):
            return _err(f"잘못된 corp_code: {corp_code!r}")
        if not re.match(r"^\d{5}$", str(report)):
            return _err(f"잘못된 report 코드: {report!r}")
        try: y = int(year)
        except (TypeError, ValueError): return _err(f"잘못된 연도: {year!r}")
        return self._get("/fnlttSinglAcntAll.json",
                         corp_code=corp_code, bsns_year=str(y),
                         reprt_code=report, fs_div="CFS")

    def major_shareholders(self, corp_code: str) -> dict:
        """대량보유 상황보고."""
        if not _valid_kr_code(corp_code):
            return _err(f"잘못된 corp_code: {corp_code!r}")
        return self._get("/majorstock.json", corp_code=corp_code)

    def _load_corp_codes(self) -> bool:
        """전체 상장사 corp_code 목록 (zip→xml) — 캐시."""
        with self._lock:
            if self._corp_map is not None:
                return True
            if not self.enabled:
                return False
            if not _XML_HARDENED:
                _log("DART", "⚠ defusedxml 미설치 — XML 엔티티 폭발 보호 비활성")
            # 보안: 비정상 응답·zip bomb 대비 다운로드 / 압축 해제 크기 상한
            MAX_DOWNLOAD = 50 * 1024 * 1024   # 50MB — corpCode.zip 실제 크기 ~5MB
            MAX_UNCOMPRESSED = 200 * 1024 * 1024  # 200MB — 해제 후 corpCode.xml ~30MB
            try:
                r = _http_get(f"{self.BASE}/corpCode.xml",
                                 params={"crtfc_key": self.key},
                                 timeout=30, stream=True)
                r.raise_for_status()
                buf = io.BytesIO(); total = 0
                for chunk in r.iter_content(chunk_size=65536):
                    total += len(chunk)
                    if total > MAX_DOWNLOAD:
                        r.close()
                        _log("DART", f"corp_code 다운로드 거부 — 크기 초과(>{MAX_DOWNLOAD//1024//1024}MB)")
                        return False
                    buf.write(chunk)
                buf.seek(0)
                with zipfile.ZipFile(buf) as z:
                    infos = z.infolist()
                    if not infos:
                        _log("DART", "zip 비어 있음"); return False
                    info = infos[0]
                    if info.file_size > MAX_UNCOMPRESSED:
                        _log("DART", f"압축 해제 거부 — 비압축 크기 초과({info.file_size} bytes)")
                        return False
                    with z.open(info) as zf:
                        # 추가 방어: 실제 읽기도 상한으로 제한
                        xml_bytes = zf.read(MAX_UNCOMPRESSED + 1)
                        if len(xml_bytes) > MAX_UNCOMPRESSED:
                            _log("DART", "실측 비압축 크기 초과 — 거부")
                            return False
                root = ET.fromstring(xml_bytes)
                m: dict = {}
                by_stock: dict = {}
                for c in root.findall("list"):
                    name = (c.findtext("corp_name") or "").strip()
                    code = (c.findtext("corp_code") or "").strip()
                    stock = (c.findtext("stock_code") or "").strip()
                    if not (name and code):
                        continue
                    rec = {"corp_code": code, "stock_code": stock, "name": name}
                    m[name] = rec
                    if stock:
                        by_stock[stock] = rec
                self._corp_map = m
                self._corp_by_stock = by_stock
                _log("DART", f"corp_code 매핑 로드: {len(m)}개")
                return True
            except Exception as e:
                _log("DART", f"corp_code 로드 실패: {e}")
                return False

    def search_corp(self, name: str, limit: int = 10) -> dict:
        """기업명으로 corp_code 검색 (정확 일치 → 포함)."""
        if not self._load_corp_codes():
            return _err("DART corp_code 로드 실패")
        exact = [v for k, v in self._corp_map.items() if k == name]
        partial = [v for k, v in self._corp_map.items() if name in k and k != name]
        hits = (exact + partial)[:limit]
        return {"success": True, "count": len(hits), "results": hits}

    def corp_by_stock_code(self, stock_code: str) -> dict:
        """6자리 종목코드(예: '005930') → corp_code 매핑."""
        if not (isinstance(stock_code, str) and re.match(r"^\d{6}$", stock_code)):
            return _err(f"잘못된 종목코드: {stock_code!r}")
        if not self._load_corp_codes():
            return _err("DART corp_code 로드 실패")
        rec = (self._corp_by_stock or {}).get(stock_code)
        if not rec:
            return _err(f"종목코드 {stock_code} 매칭 없음")
        return {"success": True, **rec}


# ══════════════════════════════════════════════════════════════
#  Stooq — 키 불필요(keyless) 글로벌 시세 CSV
#  https://stooq.com — 미국/유럽/지수/FX 시세를 CSV로 무료 제공.
#  한국 개별종목 커버리지는 불안정 → 글로벌(미국) 위주로 사용.
# ══════════════════════════════════════════════════════════════
class StooqProvider:
    QUOTE = "https://stooq.com/q/l/"
    HIST = "https://stooq.com/q/d/l/"

    def __init__(self):
        self.enabled = True   # 키 불필요
        # Stooq는 UA 없는/데이터센터 요청을 차단하는 경향 → 브라우저 UA 부여
        self._headers = {
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Safari/537.36"),
            "Accept": "text/csv,text/plain,*/*",
        }

    @staticmethod
    def _to_symbol(ticker: str) -> str:
        """야후식 티커 → Stooq 심볼. 미국주는 소문자 + .us."""
        t = (ticker or "").strip().lower()
        if not t:
            return t
        if t.endswith(".ks") or t.endswith(".kq"):
            # Stooq 한국 심볼: 6자리 + .kr (커버리지 제한적)
            return t.split(".")[0] + ".kr"
        if "." in t or "^" in t or "-" in t:
            return t
        return t + ".us"

    @staticmethod
    def _looks_like_block(text: str) -> bool:
        """CSV 대신 HTML(차단/404 안내 페이지)이 오면 True."""
        head = (text or "")[:200].lstrip().lower()
        return (head.startswith("<") or "<title>stooq" in head
                or "does not exist" in head or "exceeded" in head)

    def quote(self, ticker: str) -> dict:
        sym = self._to_symbol(ticker)
        if not sym:
            return _err("빈 심볼")
        try:
            r = _http_get(self.QUOTE, params={"s": sym, "f": "sd2t2ohlcv", "h": "", "e": "csv"},
                          headers=self._headers, timeout=HTTP_TIMEOUT)
            text = (r.text or "").strip()
            # 차단/404은 HTML을 반환 → raise_for_status()의 맨 404 대신 깔끔히 처리
            if r.status_code >= 400 or self._looks_like_block(text):
                return _err(f"Stooq 미제공/차단({sym}) — IP 제한 또는 미커버 종목")
            lines = text.splitlines()
            if len(lines) < 2:
                return _err("Stooq 응답 비어있음")
            cols = [c.strip().lower() for c in lines[0].split(",")]
            vals = lines[1].split(",")
            row = dict(zip(cols, vals))
            close = row.get("close", "")
            if not close or close.upper() in ("N/D", "ND", ""):
                return _err("Stooq 데이터 없음")
            def _f(k):
                try:
                    return float(row.get(k) or 0) or None
                except (TypeError, ValueError):
                    return None
            price = _f("close")
            if not price:
                return _err("Stooq 가격 없음")
            vol = None
            try:
                vol = int(float(row.get("volume") or 0)) or None
            except (TypeError, ValueError):
                vol = None
            return {"success": True, "symbol": sym, "price": price,
                    "open": _f("open"), "high": _f("high"), "low": _f("low"),
                    "volume": vol, "raw": row}
        except Exception as e:
            return _err(str(e))

    def daily(self, ticker: str) -> dict:
        """일봉 CSV (Date,Open,High,Low,Close,Volume)."""
        sym = self._to_symbol(ticker)
        try:
            r = _http_get(self.HIST, params={"s": sym, "i": "d"},
                          headers=self._headers, timeout=HTTP_TIMEOUT)
            text = (r.text or "").strip()
            if r.status_code >= 400 or self._looks_like_block(text):
                return _err(f"Stooq 미제공/차단({sym}) — IP 제한 또는 미커버 종목")
            lines = text.splitlines()
            if len(lines) < 2 or "N/D" in text[:64]:
                return _err("Stooq 일봉 없음")
            cols = [c.strip() for c in lines[0].split(",")]
            rows = []
            for ln in lines[1:]:
                parts = ln.split(",")
                if len(parts) != len(cols):
                    continue
                rows.append(dict(zip(cols, parts)))
            return {"success": True, "symbol": sym, "count": len(rows), "rows": rows}
        except Exception as e:
            return _err(str(e))


# ══════════════════════════════════════════════════════════════
#  네이버 금융 — 키 불필요(keyless) 한국 시세·펀더멘털
#  m.stock.naver.com integration API: 현재가 + PER/PBR/EPS/시총 한 번에.
# ══════════════════════════════════════════════════════════════
class NaverProvider:
    INTEGRATION = "https://m.stock.naver.com/api/stock/{code}/integration"
    BASIC = "https://m.stock.naver.com/api/stock/{code}/basic"

    def __init__(self):
        self.enabled = True   # 키 불필요
        self._headers = {
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Safari/537.36"),
            "Referer": "https://m.stock.naver.com/",
        }

    @staticmethod
    def _num(v):
        """'25.02배' '12,372원' '8.22' '46.76%' → float (첫 수치 토큰)."""
        if v is None:
            return None
        m = re.search(r"-?[\d,]+(?:\.\d+)?", str(v))
        if not m:
            return None
        try:
            f = float(m.group(0).replace(",", ""))
            return f if f != 0 else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _market_won(v):
        """'1,809조 4,232억' → 원 단위 float."""
        if v is None:
            return None
        s = str(v)
        total = 0.0
        jo = re.search(r"([\d,]+)\s*조", s)
        eok = re.search(r"([\d,]+)\s*억", s)
        if jo:
            total += float(jo.group(1).replace(",", "")) * 1e12
        if eok:
            total += float(eok.group(1).replace(",", "")) * 1e8
        if total == 0.0:
            # 단위 없는 순수 숫자면 억 단위로 간주
            n = NaverProvider._num(s)
            return n * 1e8 if n else None
        return total

    def quote(self, code: str) -> dict:
        if not _valid_kr_code(code):
            return _err(f"잘못된 한국 종목코드: {code!r}")
        try:
            # ① /basic — 현재가·등락 (현재가는 여기 closePrice)
            b = _http_get(self.BASIC.format(code=code), headers=self._headers,
                          timeout=HTTP_TIMEOUT)
            b.raise_for_status()
            basic = b.json() or {}
            name = basic.get("stockName")
            price = self._num(basic.get("closePrice"))
            change = self._num(basic.get("compareToPreviousClosePrice"))
            change_pct = self._num(basic.get("fluctuationsRatio"))
            if price is None:
                return _err("네이버 가격 없음")

            # ② /integration — OHLCV·PER/PBR/EPS/시총
            per = pbr = eps = market_cap = None
            openp = high = low = volume = None
            try:
                ig = _http_get(self.INTEGRATION.format(code=code),
                               headers=self._headers, timeout=HTTP_TIMEOUT)
                if ig.status_code == 200:
                    infos = {it.get("code"): it.get("value")
                             for it in (ig.json() or {}).get("totalInfos", [])}
                    per = self._num(infos.get("per"))
                    pbr = self._num(infos.get("pbr"))
                    eps = self._num(infos.get("eps"))
                    market_cap = self._market_won(infos.get("marketValue"))
                    openp = self._num(infos.get("openPrice"))
                    high = self._num(infos.get("highPrice"))
                    low = self._num(infos.get("lowPrice"))
                    volume = self._num(infos.get("accumulatedTradingVolume"))
            except Exception:
                pass

            return {"success": True, "code": code, "name": name, "price": price,
                    "change": change, "change_pct": change_pct,
                    "open": openp, "high": high, "low": low,
                    "volume": int(volume) if volume else None,
                    "per": per, "pbr": pbr, "eps": eps, "market_cap": market_cap,
                    "currency": "KRW"}
        except Exception as e:
            return _err(str(e))


# ══════════════════════════════════════════════════════════════
#  다음 금융 — 키 불필요(keyless) 한국 시세·펀더멘털
#  finance.daum.net/api/quotes/A{code}: 현재가 + PER/PBR/EPS/시총 한 번에.
# ══════════════════════════════════════════════════════════════
class DaumProvider:
    QUOTE = "https://finance.daum.net/api/quotes/A{code}"

    def __init__(self):
        self.enabled = True   # 키 불필요
        self._headers = {
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0 Safari/537.36"),
            "Referer": "https://finance.daum.net/",
        }

    @staticmethod
    def _f(v):
        try:
            f = float(v)
            return f if f != 0 else None
        except (TypeError, ValueError):
            return None

    def quote(self, code: str) -> dict:
        if not _valid_kr_code(code):
            return _err(f"잘못된 한국 종목코드: {code!r}")
        try:
            hdr = dict(self._headers)
            hdr["Referer"] = f"https://finance.daum.net/quotes/A{code}"
            r = _http_get(self.QUOTE.format(code=code), headers=hdr, timeout=HTTP_TIMEOUT)
            if r.status_code != 200:
                return _err(f"다음 금융 응답 오류 (HTTP {r.status_code})")
            d = r.json() or {}
            price = self._f(d.get("tradePrice"))
            if price is None:
                return _err("다음 금융 가격 없음")
            # 등락: changePrice/changeRate는 절대값, change(RISE/FALL)로 부호 결정
            sign = -1.0 if str(d.get("change")).upper() == "FALL" else 1.0
            chg = self._f(d.get("changePrice"))
            change = abs(chg) * sign if chg is not None else None
            rate = self._f(d.get("changeRate"))
            change_pct = abs(rate) * 100.0 * sign if rate is not None else None
            vol = d.get("accTradeVolume")
            return {"success": True, "code": code, "name": d.get("name"),
                    "price": price, "change": change, "change_pct": change_pct,
                    "open": self._f(d.get("openingPrice")),
                    "high": self._f(d.get("highPrice")),
                    "low": self._f(d.get("lowPrice")),
                    "volume": int(float(vol)) if vol else None,
                    "per": self._f(d.get("per")), "pbr": self._f(d.get("pbr")),
                    "eps": self._f(d.get("eps")),
                    "market_cap": self._f(d.get("marketCap")),
                    "currency": "KRW", "raw": d}
        except Exception as e:
            return _err(str(e))


# ══════════════════════════════════════════════════════════════
#  Finnhub — 무료 티어(키 필요, 60req/분) 글로벌 시세·펀더멘털
# ══════════════════════════════════════════════════════════════
class FinnhubProvider:
    BASE = "https://finnhub.io/api/v1"

    def __init__(self):
        self.key = _get_secret("FINNHUB_API_KEY")
        self.enabled = bool(self.key)

    def _get(self, path: str, **params) -> dict:
        if not self.enabled:
            return _err("FINNHUB_API_KEY 미설정")
        params["token"] = self.key
        try:
            r = _http_get(f"{self.BASE}{path}", params=params, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            return {"success": True, "data": r.json()}
        except Exception as e:
            return _err(str(e))

    def quote(self, symbol: str) -> dict:
        if not _valid_ticker(symbol):
            return _err(f"잘못된 종목 심볼: {symbol!r}")
        r = self._get("/quote", symbol=symbol)
        if not r["success"]:
            return r
        d = r["data"] or {}
        price = d.get("c")
        if not price:
            return _err("Finnhub 가격 없음")
        return {"success": True, "symbol": symbol, "price": float(price),
                "open": d.get("o"), "high": d.get("h"), "low": d.get("l"),
                "change": d.get("d"), "change_pct": d.get("dp"), "raw": d}

    def metric(self, symbol: str) -> dict:
        """기업 펀더멘털 지표 (PER/PBR/EPS/시총 등)."""
        if not _valid_ticker(symbol):
            return _err(f"잘못된 종목 심볼: {symbol!r}")
        r = self._get("/stock/metric", symbol=symbol, metric="all")
        if not r["success"]:
            return r
        m = (r["data"] or {}).get("metric") or {}
        return {"success": True, "symbol": symbol,
                "per": m.get("peTTM") or m.get("peBasicExclExtraTTM"),
                "pbr": m.get("pbAnnual") or m.get("pbQuarterly"),
                "eps": m.get("epsTTM") or m.get("epsBasicExclExtraItemsTTM"),
                "market_cap": m.get("marketCapitalization"),
                "raw": m}


# ══════════════════════════════════════════════════════════════
#  레지스트리 & 상태 점검
# ══════════════════════════════════════════════════════════════
_REGISTRY: dict[str, object] = {}
_REGISTRY_LOCK = threading.Lock()


def _registry() -> dict:
    # 첫 호출 시 동시 진입해도 중복 프로바이더(따라서 중복 토큰 발급)가 생기지 않도록 보호
    with _REGISTRY_LOCK:
        if _REGISTRY:
            return _REGISTRY
        _REGISTRY.update({
            "alpha_vantage": AlphaVantageProvider(),
            "yahoo": YahooFinanceProvider(),
            "fmp": FMPProvider(),
            "kis": KISProvider(),
            "ls": LSProvider(),
            "kiwoom": KiwoomProvider(),
            "dart": DARTProvider(),
            "stooq": StooqProvider(),
            "naver": NaverProvider(),
            "daum": DaumProvider(),
            "finnhub": FinnhubProvider(),
        })
        return _REGISTRY


def get_provider(name: str):
    """이름으로 프로바이더 인스턴스 획득. 없으면 None."""
    return _registry().get(name)


def provider_status() -> dict:
    """모든 프로바이더의 활성 여부 일괄 조회."""
    out = {}
    for name, p in _registry().items():
        out[name] = {"enabled": bool(getattr(p, "enabled", False)),
                     "class": type(p).__name__}
    return out


__all__ = [
    "AlphaVantageProvider", "YahooFinanceProvider", "FMPProvider",
    "KISProvider", "LSProvider", "KiwoomProvider", "DARTProvider",
    "StooqProvider", "NaverProvider", "DaumProvider", "FinnhubProvider",
    "get_provider", "provider_status",
]


if __name__ == "__main__":
    print(json.dumps(provider_status(), indent=2, ensure_ascii=False))
