"""
J.A.R.V.I.S — 다중 API 교차 검증 (Multi-Source Cross-Validation)
────────────────────────────────────────────────────────────────
"삼성전자 알아봐줘" 같은 요청 한 번에:
  ① 7개 데이터 소스 병렬 조회 (ThreadPoolExecutor)
  ② 각 응답을 통일된 StockData 모델로 매핑
  ③ 필드별(가격/거래량/PER/PBR/시총/EPS) 중앙값 기준 이상치 제외 후 합의값 계산
  ④ 음성 브리핑 문구 + 구조화 리포트 동시 산출

아키텍처
  data_providers.py          ← 저수준: 7개 프로바이더 (인증/HTTP/원본 파싱)
  ⤴︎
  cross_validation.py        ← 이 파일: 매퍼 + 병렬 페치 + 합의 + 브리핑
  ⤴︎
  jarvis_v2.py               ← LLM 도구 `analyze_stock_multi_source` 노출
"""
from __future__ import annotations

import os
import re
import time
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Callable

from data_providers import get_provider

# ── CV 캐시 / On-Demand Gemini 카운터 (테스트용) ──
_CV_GEMINI_CALL_COUNT = 0


def reset_cv_gemini_call_count() -> None:
    global _CV_GEMINI_CALL_COUNT
    _CV_GEMINI_CALL_COUNT = 0


def get_cv_gemini_call_count() -> int:
    return _CV_GEMINI_CALL_COUNT


def _bump_cv_gemini() -> None:
    global _CV_GEMINI_CALL_COUNT
    _CV_GEMINI_CALL_COUNT += 1


def _cv_cache_enabled() -> bool:
    try:
        from cv_cache_manager import cv_cache_enabled
        return cv_cache_enabled()
    except ImportError:
        return False


def _cv_on_demand_gemini_enabled() -> bool:
    return os.getenv("CV_CACHE_ON_DEMAND_GEMINI", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


# ══════════════════════════════════════════════════════════════
#  통일 데이터 모델
# ══════════════════════════════════════════════════════════════
@dataclass
class StockData:
    """단일 소스의 정규화된 응답."""
    source: str
    ticker: str
    success: bool = False
    error: str | None = None
    name: str | None = None
    price: float | None = None
    change: float | None = None
    change_pct: float | None = None
    volume: int | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    per: float | None = None
    pbr: float | None = None
    market_cap: float | None = None
    eps: float | None = None
    currency: str = ""
    fetched_at: float = 0.0
    extras: dict = field(default_factory=dict)   # DART 공시 등 소스 고유 메타


@dataclass
class FieldConsensus:
    """필드 단위 합의 결과."""
    final: float | None
    accepted: list[tuple[str, float]]
    rejected: list[tuple[str, float, float]]    # (source, value, deviation_pct)
    method: str


@dataclass
class ValidationReport:
    query: str
    name: str | None
    currency: str
    sources_attempted: list[str]
    sources_succeeded: list[str]
    sources_failed: list[tuple[str, str]]
    price: FieldConsensus
    volume: FieldConsensus
    per: FieldConsensus
    pbr: FieldConsensus
    market_cap: FieldConsensus
    eps: FieldConsensus
    change_pct: FieldConsensus
    extras: dict
    raw: list[StockData]


# ══════════════════════════════════════════════════════════════
#  티커 해석 — 사용자 입력 → 소스별 정규 티커
# ══════════════════════════════════════════════════════════════
def _resolve_tickers(query: str) -> dict:
    """
    "삼성전자" / "005930" / "AAPL" → 각 소스가 받을 수 있는 형태로 분해.

    국내 종목은 Yahoo 형식 'XXXXXX.KS'에서 6자리 코드를 추출해
    KIS/LS/Kiwoom/DART에 사용. 해외는 KIS 해외시세(NAS 추정)만 시도.
    """
    from stock_quant import resolve_alert_query, _resolve_ticker
    r = resolve_alert_query(query)
    yahoo_t = r["ticker"] if r.get("confident") else _resolve_ticker(query)
    name = r.get("name")

    is_kr = isinstance(yahoo_t, str) and (yahoo_t.endswith(".KS") or yahoo_t.endswith(".KQ"))
    out: dict[str, Any] = {
        "_query": query, "_name": name, "_yahoo": yahoo_t, "_is_korean": is_kr,
        "yahoo": yahoo_t, "alpha": yahoo_t, "fmp": yahoo_t,
    }
    if is_kr:
        code6 = yahoo_t.rsplit(".", 1)[0]
        out["kis"] = code6; out["ls"] = code6
        out["kiwoom"] = code6; out["dart"] = code6
        out["naver"] = code6
    else:
        # 해외: NAS/NYS 자동 판별 어려움 → NAS 우선, 실패 시 NYS 재시도(매퍼 내부)
        out["kis_overseas"] = ("NAS", yahoo_t)
        out["finnhub"] = yahoo_t
    # Stooq(키 불필요)는 두 시장 모두 시도 — 야후식 티커 그대로
    out["stooq"] = yahoo_t
    return out


# ══════════════════════════════════════════════════════════════
#  소스별 매퍼 — 각자 StockData 채워서 반환
# ══════════════════════════════════════════════════════════════
def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return f if f != 0 else None
    except (TypeError, ValueError):
        return None


def _map_yahoo(ticker: str) -> StockData:
    p = get_provider("yahoo")
    out = StockData(source="Yahoo Finance", ticker=ticker, fetched_at=time.time())
    if not p.enabled:
        out.error = "yfinance 미설치"; return out
    try:
        q = p.quote(ticker)
        if q.get("success"):
            out.price = q.get("price")
        info_r = p.info(ticker)
        info = info_r.get("info") if info_r.get("success") else {}
        out.name = info.get("longName") or info.get("shortName")
        out.per = _safe_float(info.get("trailingPE"))
        out.pbr = _safe_float(info.get("priceToBook"))
        out.market_cap = _safe_float(info.get("marketCap"))
        out.eps = _safe_float(info.get("trailingEps"))
        out.volume = info.get("volume") or info.get("regularMarketVolume")
        out.currency = info.get("currency", "") or ""
        out.open = _safe_float(info.get("regularMarketOpen") or info.get("open"))
        out.high = _safe_float(info.get("dayHigh") or info.get("regularMarketDayHigh"))
        out.low = _safe_float(info.get("dayLow") or info.get("regularMarketDayLow"))
        prev = _safe_float(info.get("previousClose") or info.get("regularMarketPreviousClose"))
        if out.price and prev:
            out.change = out.price - prev
            out.change_pct = (out.change / prev) * 100
        out.success = out.price is not None and out.price > 0
        if not out.success:
            out.error = "가격 누락"
    except Exception as e:
        out.error = str(e)
    return out


def _map_alpha(symbol: str) -> StockData:
    p = get_provider("alpha_vantage")
    out = StockData(source="Alpha Vantage", ticker=symbol, fetched_at=time.time())
    if not p.enabled:
        out.error = "ALPHAVANTAGE_API_KEY 미설정"; return out
    q = p.quote(symbol)
    if not q.get("success"):
        out.error = q.get("error"); return out
    out.price = _safe_float(q.get("price"))
    out.change = _safe_float(q.get("change"))
    cp = q.get("change_pct", "")
    if isinstance(cp, str) and cp.endswith("%"):
        out.change_pct = _safe_float(cp[:-1])
    out.open = _safe_float(q.get("open"))
    out.high = _safe_float(q.get("high"))
    out.low = _safe_float(q.get("low"))
    out.volume = q.get("volume")
    # 펀더멘털 (별도 호출 — 한도 절약 위해 가격 성공 시만)
    if out.price:
        ov = p.overview(symbol)
        if ov.get("success"):
            d = ov.get("data") or {}
            out.name = d.get("Name")
            out.per = _safe_float(d.get("PERatio"))
            out.pbr = _safe_float(d.get("PriceToBookRatio"))
            out.market_cap = _safe_float(d.get("MarketCapitalization"))
            out.eps = _safe_float(d.get("EPS"))
            out.currency = d.get("Currency", "") or ""
    out.success = out.price is not None
    return out


def _map_fmp(symbol: str) -> StockData:
    p = get_provider("fmp")
    out = StockData(source="Financial Modeling Prep", ticker=symbol, fetched_at=time.time())
    if not p.enabled:
        out.error = "FMP_API_KEY 미설정"; return out
    q = p.quote(symbol)
    if not q.get("success"):
        out.error = q.get("error"); return out
    out.name = q.get("name")
    out.price = _safe_float(q.get("price"))
    out.change = _safe_float(q.get("change"))
    out.change_pct = _safe_float(q.get("change_pct"))
    out.volume = q.get("volume")
    out.market_cap = _safe_float(q.get("market_cap"))
    out.per = _safe_float(q.get("pe"))
    out.eps = _safe_float(q.get("eps"))
    # PBR은 ratios-ttm 별도 호출 (stable/레거시 필드명 모두 대응)
    r = p.ratios_ttm(symbol)
    if r.get("success"):
        data = r.get("data")
        if isinstance(data, list) and data:
            row = data[0]
            out.pbr = _safe_float(
                row.get("priceToBookRatioTTM")
                or row.get("priceToBookRatio")
                or row.get("pbRatioTTM")
            )
    out.success = out.price is not None
    return out


def _map_kis(code: str) -> StockData:
    p = get_provider("kis")
    out = StockData(source="한국투자증권", ticker=code, currency="KRW", fetched_at=time.time())
    if not p.enabled:
        out.error = "KIS_APP_KEY/SECRET 미설정"; return out
    q = p.quote(code)
    if not q.get("success"):
        out.error = q.get("error"); return out
    out.name = q.get("name")
    out.price = _safe_float(q.get("price"))
    out.change = _safe_float(q.get("change"))
    out.change_pct = _safe_float(q.get("change_pct"))
    out.volume = q.get("volume")
    out.open = _safe_float(q.get("open"))
    out.high = _safe_float(q.get("high"))
    out.low = _safe_float(q.get("low"))
    out.per = _safe_float(q.get("per"))
    out.pbr = _safe_float(q.get("pbr"))
    out.success = out.price is not None
    return out


def _map_kis_overseas(exch: str, symbol: str) -> StockData:
    p = get_provider("kis")
    out = StockData(source="한국투자증권(해외)", ticker=f"{exch}:{symbol}",
                    fetched_at=time.time())
    if not p.enabled:
        out.error = "KIS_APP_KEY/SECRET 미설정"; return out
    q = p.overseas_quote(exch, symbol)
    if not q.get("success"):
        # NAS 실패 시 NYS 자동 재시도 (티커는 동일)
        if exch == "NAS":
            q = p.overseas_quote("NYS", symbol)
            if q.get("success"):
                out.ticker = f"NYS:{symbol}"
        if not q.get("success"):
            out.error = q.get("error"); return out
    out.price = _safe_float(q.get("price"))
    out.change_pct = _safe_float(q.get("change_pct"))
    out.volume = q.get("volume")
    out.success = out.price is not None
    return out


def _map_ls(code: str) -> StockData:
    p = get_provider("ls")
    out = StockData(source="LS증권", ticker=code, currency="KRW", fetched_at=time.time())
    if not p.enabled:
        out.error = "LS_APP_KEY/SECRET 미설정"; return out
    q = p.quote(code)
    if not q.get("success"):
        out.error = q.get("error"); return out
    out.name = q.get("name")
    out.price = _safe_float(q.get("price"))
    out.change = _safe_float(q.get("change"))
    out.change_pct = _safe_float(q.get("change_pct"))
    out.volume = q.get("volume")
    out.open = _safe_float(q.get("open"))
    out.high = _safe_float(q.get("high"))
    out.low = _safe_float(q.get("low"))
    out.success = out.price is not None
    return out


def _map_kiwoom(code: str) -> StockData:
    p = get_provider("kiwoom")
    out = StockData(source="키움증권", ticker=code, currency="KRW", fetched_at=time.time())
    if not p.enabled:
        out.error = "KIWOOM_APP_KEY/SECRET 미설정"; return out
    q = p.quote(code)
    if not q.get("success"):
        out.error = q.get("error"); return out
    out.name = q.get("name")
    out.price = _safe_float(q.get("price"))
    out.change = _safe_float(q.get("change"))
    out.change_pct = _safe_float(q.get("change_pct"))
    out.volume = q.get("volume")
    out.success = out.price is not None
    return out


def _map_dart(stock_code: str) -> StockData:
    """DART는 실시간 가격 미제공 — 공시·기업메타만 채워 success 반환."""
    p = get_provider("dart")
    out = StockData(source="Open DART", ticker=stock_code, currency="KRW",
                    fetched_at=time.time())
    if not p.enabled:
        out.error = "DART_API_KEY 미설정"; return out
    m = p.corp_by_stock_code(stock_code)
    if not m.get("success"):
        out.error = m.get("error"); return out
    corp_code = m["corp_code"]
    out.name = m.get("name")
    out.extras["corp_code"] = corp_code
    today = datetime.now().strftime("%Y%m%d")
    bgn = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    d = p.disclosures(corp_code=corp_code, bgn_de=bgn, end_de=today, count=10)
    if d.get("success"):
        lst = (d.get("data") or {}).get("list") or []
        out.extras["disclosures_30d"] = len(lst)
        out.extras["recent_disclosures"] = [
            {"date": r.get("rcept_dt"), "title": r.get("report_nm")}
            for r in lst[:3]
        ]
    out.success = True   # 가격이 없어도 기업 메타로 성공 처리 (교차검증엔 미참여)
    return out


def _map_naver(code: str) -> StockData:
    """네이버 금융(키 불필요) — 한국 종목 현재가·PER/PBR/EPS/시총."""
    p = get_provider("naver")
    out = StockData(source="네이버 금융", ticker=code, currency="KRW", fetched_at=time.time())
    if not p or not getattr(p, "enabled", False):
        out.error = "네이버 프로바이더 없음"; return out
    q = p.quote(code)
    if not q.get("success"):
        out.error = q.get("error"); return out
    out.name = q.get("name")
    out.price = _safe_float(q.get("price"))
    out.change = _safe_float(q.get("change"))
    out.change_pct = _safe_float(q.get("change_pct"))
    out.open = _safe_float(q.get("open"))
    out.high = _safe_float(q.get("high"))
    out.low = _safe_float(q.get("low"))
    out.volume = q.get("volume")
    out.per = _safe_float(q.get("per"))
    out.pbr = _safe_float(q.get("pbr"))
    out.eps = _safe_float(q.get("eps"))
    out.market_cap = _safe_float(q.get("market_cap"))
    out.success = out.price is not None
    return out


def _map_daum(code: str) -> StockData:
    """다음 금융(키 불필요) — 한국 종목 현재가·PER/PBR/EPS/시총."""
    p = get_provider("daum")
    out = StockData(source="다음 금융", ticker=code, currency="KRW", fetched_at=time.time())
    if not p or not getattr(p, "enabled", False):
        out.error = "다음 프로바이더 없음"; return out
    q = p.quote(code)
    if not q.get("success"):
        out.error = q.get("error"); return out
    out.name = q.get("name")
    out.price = _safe_float(q.get("price"))
    out.change = _safe_float(q.get("change"))
    out.change_pct = _safe_float(q.get("change_pct"))
    out.open = _safe_float(q.get("open"))
    out.high = _safe_float(q.get("high"))
    out.low = _safe_float(q.get("low"))
    out.volume = q.get("volume")
    out.per = _safe_float(q.get("per"))
    out.pbr = _safe_float(q.get("pbr"))
    out.eps = _safe_float(q.get("eps"))
    out.market_cap = _safe_float(q.get("market_cap"))
    out.success = out.price is not None
    return out


def _map_stooq(ticker: str) -> StockData:
    """Stooq(키 불필요) — 글로벌 시세 CSV. 봇 차단 시 조용히 실패."""
    p = get_provider("stooq")
    out = StockData(source="Stooq", ticker=ticker, fetched_at=time.time())
    if not p or not getattr(p, "enabled", False):
        out.error = "Stooq 프로바이더 없음"; return out
    q = p.quote(ticker)
    if not q.get("success"):
        out.error = q.get("error"); return out
    out.price = _safe_float(q.get("price"))
    out.open = _safe_float(q.get("open"))
    out.high = _safe_float(q.get("high"))
    out.low = _safe_float(q.get("low"))
    out.volume = q.get("volume")
    out.success = out.price is not None
    return out


def _map_finnhub(symbol: str) -> StockData:
    """Finnhub(무료 키) — 글로벌 시세·펀더멘털."""
    p = get_provider("finnhub")
    out = StockData(source="Finnhub", ticker=symbol, fetched_at=time.time())
    if not p or not getattr(p, "enabled", False):
        out.error = "FINNHUB_API_KEY 미설정"; return out
    # Finnhub 은 접미사(.KS 등) 없는 심볼 사용 — 해외 티커만 전달됨
    q = p.quote(symbol)
    if not q.get("success"):
        out.error = q.get("error"); return out
    out.price = _safe_float(q.get("price"))
    out.change = _safe_float(q.get("change"))
    out.change_pct = _safe_float(q.get("change_pct"))
    out.open = _safe_float(q.get("open"))
    out.high = _safe_float(q.get("high"))
    out.low = _safe_float(q.get("low"))
    if out.price:
        m = p.metric(symbol)
        if m.get("success"):
            out.per = _safe_float(m.get("per"))
            out.pbr = _safe_float(m.get("pbr"))
            out.eps = _safe_float(m.get("eps"))
            mc = _safe_float(m.get("market_cap"))
            # Finnhub marketCapitalization 단위: 백만 → 원본 통화
            out.market_cap = mc * 1e6 if mc else None
    out.success = out.price is not None
    return out


# ══════════════════════════════════════════════════════════════
#  병렬 페치 — ThreadPoolExecutor (각 매퍼는 동기 requests)
# ══════════════════════════════════════════════════════════════
def fetch_all_parallel(query: str, timeout: float = 12.0) -> list[StockData]:
    """7개 소스 동시 호출. 응답 순서 무관, 타임아웃 초과 매퍼는 실패 처리."""
    tk = _resolve_tickers(query)
    tasks: list[tuple[str, Callable[[], StockData]]] = []
    yt = tk["_yahoo"]
    tasks.append(("Yahoo Finance",          lambda yt=yt: _map_yahoo(yt)))
    tasks.append(("Alpha Vantage",          lambda yt=yt: _map_alpha(yt)))
    tasks.append(("Financial Modeling Prep",lambda yt=yt: _map_fmp(yt)))
    tasks.append(("Stooq",                  lambda yt=yt: _map_stooq(yt)))
    if tk["_is_korean"]:
        c = tk["kis"]
        tasks.append(("한국투자증권", lambda c=c: _map_kis(c)))
        tasks.append(("LS증권",       lambda c=c: _map_ls(c)))
        tasks.append(("키움증권",     lambda c=c: _map_kiwoom(c)))
        tasks.append(("Open DART",    lambda c=c: _map_dart(c)))
        tasks.append(("네이버 금융",  lambda c=c: _map_naver(c)))
        tasks.append(("다음 금융",    lambda c=c: _map_daum(c)))
    else:
        exch, sym = tk["kis_overseas"]
        tasks.append(("한국투자증권(해외)",
                      lambda exch=exch, sym=sym: _map_kis_overseas(exch, sym)))
        fh = tk.get("finnhub", yt)
        tasks.append(("Finnhub", lambda fh=fh: _map_finnhub(fh)))

    results: list[StockData] = []
    with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
        futs = {ex.submit(fn): name for name, fn in tasks}
        try:
            for fut in as_completed(futs, timeout=timeout):
                src = futs[fut]
                try:
                    results.append(fut.result())
                except Exception as e:
                    results.append(StockData(source=src, ticker=query,
                                             success=False, error=str(e),
                                             fetched_at=time.time()))
        except Exception:
            # 전체 타임아웃 — 완료된 것만 수집
            for fut, src in futs.items():
                if fut.done():
                    try: results.append(fut.result())
                    except Exception as e:
                        results.append(StockData(source=src, ticker=query,
                                                 success=False, error=str(e),
                                                 fetched_at=time.time()))
                else:
                    results.append(StockData(source=src, ticker=query, success=False,
                                             error="timeout", fetched_at=time.time()))
    return results


# ══════════════════════════════════════════════════════════════
#  교차 검증 — 중앙값 기준 이상치 제외 후 합의
# ══════════════════════════════════════════════════════════════
# ── 소스 신뢰도 가중치 ──────────────────────────────────────────
# 공식 거래소·브로커 실시간 피드 > 포털 준실시간 > 지연 애그리게이터.
# 값이 갈릴 때 더 권위 있는 소스로 합의가 기울도록 가중평균에 사용.
_SOURCE_WEIGHT = {
    "KRX":               1.00,   # 한국거래소 공식
    "한국투자증권":       1.00,   # 브로커 실시간(KRX 직결)
    "한국투자증권(해외)":  0.95,
    "LS증권":            1.00,
    "키움증권":           1.00,
    "Finnhub":           0.90,   # 미국 실시간(무료 티어)
    "Financial Modeling Prep": 0.90,
    "Alpha Vantage":     0.85,
    "네이버 금융":        0.85,   # 포털 준실시간
    "다음 금융":          0.85,
    "Yahoo Finance":     0.70,   # 15분 지연 애그리게이터
    "Stooq":             0.50,   # EOD 지연
}
_DEFAULT_WEIGHT = 0.60


def _source_weight(source: str) -> float:
    return _SOURCE_WEIGHT.get(source, _DEFAULT_WEIGHT)


def _weighted_avg(pairs: list[tuple[str, float]]) -> float:
    """신뢰도 가중평균. 가중합이 0이면 단순평균으로 폴백."""
    wsum = sum(_source_weight(s) for s, _ in pairs)
    if wsum <= 0:
        return sum(v for _, v in pairs) / len(pairs)
    return sum(_source_weight(s) * v for s, v in pairs) / wsum


def _consensus(field_name: str, results: list[StockData], tol: float) -> FieldConsensus:
    """field_name 값들을 모아 중앙값 기준 |편차| > tol 소스를 걸러내고,
    남은 값들의 '신뢰도 가중평균'을 합의값으로 채택."""
    pairs: list[tuple[str, float]] = []
    for r in results:
        if not r.success:
            continue
        v = getattr(r, field_name, None)
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv <= 0:
            continue
        pairs.append((r.source, fv))

    if not pairs:
        return FieldConsensus(None, [], [], "데이터 없음")
    if len(pairs) == 1:
        return FieldConsensus(pairs[0][1], pairs, [], "단일 소스(교차검증 불가 — 참고용)")

    nums = [v for _, v in pairs]
    med = statistics.median(nums)
    accepted: list[tuple[str, float]] = []
    rejected: list[tuple[str, float, float]] = []
    for src, v in pairs:
        dev = abs(v - med) / abs(med) if med != 0 else 0.0
        (accepted if dev <= tol else rejected).append(
            (src, v) if dev <= tol else (src, v, dev)
        )

    if accepted:
        final = _weighted_avg(accepted)
        return FieldConsensus(final, accepted, rejected,
                              f"{len(accepted)}/{len(pairs)} 합의(신뢰도 가중)")

    # 모든 값이 중앙값에서 tol 초과 = '이상치'가 아니라 소스 간 산정기준 차이
    # (주로 2개 소스 펀더멘털: 연결/별도, TTM/Forward). 신뢰도 가중으로 최선 추정.
    dispersion = [(s, v, abs(v - med) / abs(med) if med != 0 else 0.0) for s, v in pairs]
    return FieldConsensus(_weighted_avg(pairs), [], dispersion,
                          "소스 간 산정기준 차이 → 신뢰도 가중 추정")


def _merge_extras(results: list[StockData]) -> dict:
    """소스 고유 메타(DART 공시 등)를 한 곳으로 모음."""
    extras: dict = {}
    for r in results:
        if r.extras:
            extras.update(r.extras)
    return extras


def cross_validate(results: list[StockData], query: str = "") -> ValidationReport:
    """필드별 합의값 계산. 허용 오차는 필드 특성에 맞춤."""
    name = next((r.name for r in results if r.name), None)
    currency = next((r.currency for r in results if r.currency), "")
    return ValidationReport(
        query=query, name=name, currency=currency,
        sources_attempted=[r.source for r in results],
        sources_succeeded=[r.source for r in results if r.success],
        sources_failed=[(r.source, r.error or "") for r in results if not r.success],
        # 사용자 명시: 가격 1% 이상 = 이상치
        price=_consensus("price", results, tol=0.01),
        # 거래량은 피드 지연/스냅샷 시점 차이 — 관대
        volume=_consensus("volume", results, tol=0.10),
        change_pct=_consensus("change_pct", results, tol=0.20),
        # 펀더멘털은 EPS 산정 기준(TTM vs Forward 등) 차이 — 5%
        per=_consensus("per", results, tol=0.05),
        pbr=_consensus("pbr", results, tol=0.05),
        market_cap=_consensus("market_cap", results, tol=0.05),
        eps=_consensus("eps", results, tol=0.05),
        extras=_merge_extras(results),
        raw=results,
    )


# ══════════════════════════════════════════════════════════════
#  브리핑 — 음성 친화적 한국어 문구 + 구조화 리포트
# ══════════════════════════════════════════════════════════════
def _fmt_money(v: float, currency: str) -> str:
    if currency == "KRW":
        return f"{v:,.0f}원"
    if currency == "USD":
        return f"${v:,.2f}"
    return f"{v:,.2f}"


def _fmt_marketcap(v: float, currency: str) -> str:
    if currency == "KRW":
        if v >= 1e12: return f"{v/1e12:.1f}조원"
        if v >= 1e8:  return f"{v/1e8:.0f}억원"
        return f"{v:,.0f}원"
    if v >= 1e12: return f"${v/1e12:.2f}T"
    if v >= 1e9:  return f"${v/1e9:.1f}B"
    if v >= 1e6:  return f"${v/1e6:.0f}M"
    return f"${v:,.0f}"


def price_confidence(report: ValidationReport) -> dict:
    """가격 교차검증 신뢰도. 독립 소스 합의 개수·편차 기반.
    반환: {level, score(0~100), n_agree, reason}."""
    c = report.price
    if c.final is None:
        return {"level": "없음", "score": 0, "n_agree": 0, "reason": "가격 데이터 없음"}
    n = len(c.accepted)
    if n >= 4:
        return {"level": "매우 높음", "score": 98, "n_agree": n,
                "reason": f"{n}개 독립 소스가 1% 이내로 일치"}
    if n == 3:
        return {"level": "높음", "score": 92, "n_agree": n,
                "reason": "3개 독립 소스가 1% 이내로 일치"}
    if n == 2:
        return {"level": "보통", "score": 74, "n_agree": n,
                "reason": "2개 소스가 일치(교차검증 최소 수준)"}
    if n == 1:
        return {"level": "낮음", "score": 45, "n_agree": 1,
                "reason": "단일 소스 — 교차검증 불가, 참고용"}
    return {"level": "낮음", "score": 38, "n_agree": 0,
            "reason": "소스 간 편차 커 가중 추정값"}


def format_briefing(report: ValidationReport) -> str:
    """2~4문장 음성 브리핑. 사용자 요구 포맷:
    '[소스들] 등 총 N개 채널의 데이터를 교차 검증한 결과, ...주가는 ...이며...'"""
    nm = report.name or report.query
    cur = report.currency
    ok = report.sources_succeeded
    total = len(report.sources_attempted)

    # 인트로 — 참여 소스 나열 (최대 3개 + "등")
    if not ok:
        return (f"{nm}에 대한 데이터를 수집하지 못했습니다. "
                f"{total}개 채널 모두 응답이 없거나 키가 설정되지 않았습니다.")
    src_head = ", ".join(ok[:3])
    suffix = " 등" if len(ok) > 3 else ""
    if total > len(ok):
        intro = (f"{src_head}{suffix} 총 {total}개 채널 중 {len(ok)}개의 데이터를 "
                 f"교차 검증한 결과,")
    else:
        intro = f"{src_head}{suffix} 총 {len(ok)}개 채널의 데이터를 교차 검증한 결과,"

    # 가격
    parts = [intro]
    if report.price.final is not None:
        price_txt = f"현재 {nm}의 주가는 {_fmt_money(report.price.final, cur)}이며"
        rej_n = len(report.price.rejected)
        if rej_n and report.price.accepted:
            rej_srcs = ", ".join(s for s, _, _ in report.price.rejected)
            price_txt += f" ({rej_srcs}의 값은 편차가 커 제외)"
        parts.append(price_txt + ",")
        # 신뢰도가 낮/보통이면 음성으로도 경고 (높음이면 소스 수가 이미 방증)
        _conf = price_confidence(report)
        if _conf["level"] in ("보통", "낮음"):
            parts.append(f"다만 이 가격은 {_conf['reason']}이라 신뢰도는 {_conf['level']} 수준입니다.")

    # 등락
    if report.change_pct.final is not None:
        sign = "상승" if report.change_pct.final >= 0 else "하락"
        parts.append(f"전일 대비 {abs(report.change_pct.final):.2f}% {sign}했습니다.")

    # 펀더멘털
    fund = []
    if report.per.final:    fund.append(f"PER {report.per.final:.1f}")
    if report.pbr.final:    fund.append(f"PBR {report.pbr.final:.2f}")
    if report.market_cap.final:
        fund.append(f"시가총액 {_fmt_marketcap(report.market_cap.final, cur)}")
    if fund:
        parts.append("주요 지표는 " + ", ".join(fund) + "입니다.")

    # 공시 (DART 전용)
    n_disc = report.extras.get("disclosures_30d")
    if n_disc:
        recent = report.extras.get("recent_disclosures") or []
        if recent:
            parts.append(
                f"최근 30일간 공시 {n_disc}건 중 최신은 "
                f"'{recent[0].get('title','제목없음')}'입니다.")

    return " ".join(parts)


def format_detail_report(report: ValidationReport) -> str:
    """HUD/로그용 다행 상세 리포트."""
    L = [f"━━━ 교차검증 리포트: {report.name or report.query} ━━━"]
    L.append(f"소스 {len(report.sources_succeeded)}/{len(report.sources_attempted)} 응답")
    _conf = price_confidence(report)
    L.append(f"가격 신뢰도: {_conf['level']} ({_conf['reason']})")
    for f in ("price", "change_pct", "volume", "per", "pbr", "market_cap", "eps"):
        c: FieldConsensus = getattr(report, f)
        if c.final is None:
            L.append(f"  {f:12} —  ({c.method})")
            continue
        L.append(f"  {f:12} {c.final:>15,.4f}  ({c.method})")
        for src, v in c.accepted:
            L.append(f"      ✓ {src:25} {v:>15,.4f}")
        # 채택이 하나도 없으면 '이상치'가 아니라 소스 간 산정기준 차이 → ≈ 로 표기
        dispersion_only = not c.accepted
        for src, v, dev in c.rejected:
            mark = "≈" if dispersion_only else "✗"
            note = "기준차" if dispersion_only else "편차"
            L.append(f"      {mark} {src:25} {v:>15,.4f}  ({note} {dev*100:.1f}%)")
    if report.sources_failed:
        L.append("실패:")
        for src, err in report.sources_failed:
            L.append(f"  ✗ {src}: {err}")
    return "\n".join(L)


def report_to_dict(report: ValidationReport) -> dict:
    """LLM 도구 응답용 직렬화."""
    def cdict(c: FieldConsensus) -> dict:
        return {"final": c.final, "method": c.method,
                "accepted": [{"source": s, "value": v} for s, v in c.accepted],
                "rejected": [{"source": s, "value": v, "deviation_pct": d*100}
                             for s, v, d in c.rejected]}
    return {
        "query": report.query, "name": report.name, "currency": report.currency,
        "sources_attempted": report.sources_attempted,
        "sources_succeeded": report.sources_succeeded,
        "sources_failed": [{"source": s, "error": e} for s, e in report.sources_failed],
        "price_confidence": price_confidence(report),
        "price": cdict(report.price), "volume": cdict(report.volume),
        "change_pct": cdict(report.change_pct),
        "per": cdict(report.per), "pbr": cdict(report.pbr),
        "market_cap": cdict(report.market_cap), "eps": cdict(report.eps),
        "extras": report.extras,
    }


# ══════════════════════════════════════════════════════════════
#  로컬 합의 정제 + 샌드박스 캐시 (Gemini 0회)
# ══════════════════════════════════════════════════════════════

_POS_NEWS = re.compile(
    r"(surge|rally|beat|record|upgrade|buy|growth|win|approval|contract|"
    r"상승|급등|호재|실적|흑자|수주|승인|돌파|매수)",
    re.I,
)
_NEG_NEWS = re.compile(
    r"(plunge|crash|miss|downgrade|sell|lawsuit|recall|fire|bankruptcy|"
    r"하락|급락|악재|적자|소송|리콜|화재|파산|매도|제재)",
    re.I,
)


@dataclass
class MarketSnapshot:
    """정제·합의된 시장 스냅샷 — Gemini 프롬프트에는 compact_digest 만 주입."""
    ticker: str
    query: str
    name: str | None
    consensus_price: float | None
    consensus_change_pct: float | None
    anomaly_flag: bool
    timeline_text: str
    news_compact_body: str
    compact_digest: str
    sources_ok: list[str]
    sentiment_score: int = 0
    sentiment_ma5: float | None = None
    sentiment_ma20: float | None = None
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "query": self.query,
            "name": self.name,
            "consensus_price": self.consensus_price,
            "consensus_change_pct": self.consensus_change_pct,
            "anomaly_flag": self.anomaly_flag,
            "timeline_text": self.timeline_text,
            "news_compact_body": self.news_compact_body,
            "compact_digest": self.compact_digest,
            "sources_ok": self.sources_ok,
            "sentiment_score": self.sentiment_score,
            "sentiment_ma5": self.sentiment_ma5,
            "sentiment_ma20": self.sentiment_ma20,
            "timestamp": int(self.timestamp or time.time()),
        }


def _resolve_yahoo_ticker(query: str) -> str:
    tk = _resolve_tickers(query)
    return str(tk.get("_yahoo") or query).strip()


def compress_news_local(news_items: list[dict], *, max_headlines: int = 5) -> tuple[str, int]:
    """
    로컬 뉴스 압축 — Gemini 없이 제목 기반 감성 힌트.
    Returns: (news_compact_body, sentiment_score -10..+10)
    """
    if not news_items:
        return "", 0

    titles: list[str] = []
    score = 0
    for item in news_items[:max_headlines]:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        titles.append(title[:80])
        if _POS_NEWS.search(title):
            score += 2
        if _NEG_NEWS.search(title):
            score -= 2

    score = max(-10, min(10, score))
    if not titles:
        return "", score

    body = "; ".join(titles)
    if len(body) > 280:
        body = body[:277] + "..."
    return body, score


# ── 뉴스 감성 시계열 (Sentiment MA) — Gemini 0 ──

_sentiment_ts_override: Any = None
_SENTIMENT_TS_FILE = "sentiment_timeseries.json"
_MAX_SENTIMENT_HISTORY = 120


def set_sentiment_timeseries_path_for_tests(path: Any) -> None:
    """테스트 격리 — None 이면 기본 샌드박스."""
    global _sentiment_ts_override
    _sentiment_ts_override = path


def sentiment_timeseries_path():
    from pathlib import Path
    if _sentiment_ts_override is not None:
        return _sentiment_ts_override
    rel = os.getenv("CV_SENTIMENT_TS_FILE", _SENTIMENT_TS_FILE).strip() or _SENTIMENT_TS_FILE
    try:
        from sandbox import resolve_in_sandbox, init_sandbox
        init_sandbox(create_standard_dirs=False)
        return resolve_in_sandbox(rel, create_parents=True)
    except Exception:
        from pathlib import Path
        p = Path(__file__).resolve().parent / "data" / "sandbox" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


def _empty_sentiment_store() -> dict:
    return {"version": 1, "series": {}, "updated_at": 0}


def read_sentiment_store() -> dict:
    path = sentiment_timeseries_path()
    if not path.is_file():
        return _empty_sentiment_store()
    try:
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_sentiment_store()
        data.setdefault("version", 1)
        data.setdefault("series", {})
        return data
    except Exception:
        return _empty_sentiment_store()


def write_sentiment_store(store: dict) -> None:
    from cv_cache_manager import write_json_atomic
    store["updated_at"] = int(time.time())
    write_json_atomic(sentiment_timeseries_path(), store)


def record_sentiment_observation(
    ticker: str,
    score: int,
    *,
    timestamp: float | None = None,
) -> list[dict]:
    """
    일별 감성 점수(-10~+10) 누적 — 동일 날짜는 갱신.
    Returns: 해당 티커 전체 시계열 list[dict].
    """
    key = _resolve_yahoo_ticker(ticker).upper()
    ts = float(timestamp or time.time())
    day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    clamped = max(-10, min(10, int(score)))

    store = read_sentiment_store()
    series = store.setdefault("series", {})
    hist: list[dict] = list(series.get(key) or [])

    entry = {"date": day, "timestamp": int(ts), "score": clamped}
    if hist and hist[-1].get("date") == day:
        hist[-1] = entry
    else:
        hist.append(entry)

    hist.sort(key=lambda x: x.get("date", ""))
    if len(hist) > _MAX_SENTIMENT_HISTORY:
        hist = hist[-_MAX_SENTIMENT_HISTORY:]
    series[key] = hist
    write_sentiment_store(store)
    return hist


def load_sentiment_timeseries(ticker: str):
    """
    Pandas DataFrame — index=date, columns score (float).
    """
    import pandas as pd

    key = _resolve_yahoo_ticker(ticker).upper()
    hist = list((read_sentiment_store().get("series") or {}).get(key) or [])
    if not hist:
        return pd.DataFrame(columns=["score"])

    rows = []
    for item in hist:
        d = item.get("date")
        if not d:
            continue
        try:
            rows.append({"date": pd.Timestamp(d), "score": float(item.get("score", 0))})
        except (TypeError, ValueError):
            continue

    if not rows:
        return pd.DataFrame(columns=["score"])

    df = pd.DataFrame(rows).sort_values("date").drop_duplicates("date", keep="last")
    df = df.set_index("date")
    return df


def build_sentiment_ma_frame(df, *, ma_short: int = 5, ma_long: int = 20):
    """
    score + MA5 + MA20 컬럼 DataFrame.
    """
    import pandas as pd

    if df is None or df.empty:
        return pd.DataFrame(columns=["score", "ma5", "ma20"])

    out = df[["score"]].copy()
    out["ma5"] = out["score"].rolling(ma_short, min_periods=ma_short).mean()
    out["ma20"] = out["score"].rolling(ma_long, min_periods=ma_long).mean()
    return out


def compute_sentiment_ma(ticker: str) -> dict:
    """
    Sentiment MA5 / MA20 최신값 및 관측 수.
    """
    raw = load_sentiment_timeseries(ticker)
    empty = {
        "ma5": None, "ma20": None, "latest_score": None,
        "observations": 0, "ready": False,
    }
    if raw.empty:
        return empty

    frame = build_sentiment_ma_frame(raw)
    ma5 = frame["ma5"].iloc[-1] if frame["ma5"].notna().any() else None
    ma20 = frame["ma20"].iloc[-1] if frame["ma20"].notna().any() else None
    latest = float(raw["score"].iloc[-1])

    return {
        "ma5": round(float(ma5), 2) if ma5 == ma5 else None,
        "ma20": round(float(ma20), 2) if ma20 == ma20 else None,
        "latest_score": int(latest),
        "observations": len(raw),
        "ready": ma5 == ma5 and ma20 == ma20,
    }


def build_timeline_text(report: ValidationReport) -> str:
    """소스별 가격·등락 타임라인 — raw OHLCV 제외."""
    nm = report.name or report.query
    parts: list[str] = []

    if report.price.accepted:
        src_prices = ", ".join(f"{s} {v:,.2f}" for s, v in report.price.accepted[:4])
        parts.append(f"{nm} 가격 소스: {src_prices}")

    if report.price.final is not None:
        cur = report.currency or ""
        parts.append(f"합의가 {_fmt_money(report.price.final, cur)}")

    if report.change_pct.final is not None:
        sign = "+" if report.change_pct.final >= 0 else ""
        parts.append(f"전일대비 {sign}{report.change_pct.final:.2f}%")

    if report.price.rejected:
        rej = ", ".join(s for s, _, _ in report.price.rejected[:3])
        parts.append(f"이상치 제외: {rej}")

    n_disc = report.extras.get("disclosures_30d")
    if n_disc:
        parts.append(f"최근 공시 {n_disc}건")

    return ". ".join(parts) + "." if parts else f"{nm} 교차검증 데이터 없음."


def detect_anomaly_flag(report: ValidationReport) -> bool:
    """가격 이상치·급변동·다수 소스 불일치."""
    if report.price.rejected:
        return True
    cp = report.change_pct.final
    if cp is not None and abs(cp) >= float(os.getenv("CV_CACHE_CHANGE_SPIKE_PCT", "5")):
        return True
    ok = len(report.sources_succeeded)
    attempted = len(report.sources_attempted)
    if attempted >= 3 and ok < max(2, attempted // 2):
        return True
    return False


def build_compact_digest(
    report: ValidationReport,
    news_body: str,
    sentiment_score: int,
) -> str:
    """Gemini On-Demand 주입용 1~2문장 요약."""
    nm = report.name or report.query
    cur = report.currency or ""
    srcs = report.sources_succeeded[:3]
    src_label = "/".join(s.replace(" Financial Modeling Prep", " FMP") for s in srcs) or "다중소스"

    price_part = ""
    if report.price.final is not None:
        price_part = f"{src_label} 합의 가격 {_fmt_money(report.price.final, cur)}"
        if report.change_pct.final is not None:
            sign = "+" if report.change_pct.final >= 0 else ""
            price_part += f", 전일대비 {sign}{report.change_pct.final:.1f}%"

    news_part = ""
    if news_body:
        tone = "중립"
        if sentiment_score >= 4:
            tone = "호재성 뉴스 집중"
        elif sentiment_score <= -4:
            tone = "악재성 뉴스 집중"
        news_part = f"AI 감성 점수 {sentiment_score:+d}로 {tone}"

    anomaly = " 특이점 감지." if detect_anomaly_flag(report) else ""
    chunks = [c for c in (f"{nm}({report.query})", price_part, news_part) if c]
    digest = ". ".join(chunks) + anomaly
    return digest.strip()


def build_market_snapshot(
    query: str,
    report: ValidationReport,
    *,
    news_items: list[dict] | None = None,
    extra_note: str = "",
) -> MarketSnapshot:
    """교차검증 리포트 + 로컬 뉴스 압축 → MarketSnapshot."""
    ticker = _resolve_yahoo_ticker(query)
    news_body, sentiment = compress_news_local(news_items or [])
    if extra_note:
        news_body = f"{extra_note}. {news_body}".strip(" .")

    timeline = build_timeline_text(report)
    digest = build_compact_digest(report, news_body, sentiment)

    try:
        record_sentiment_observation(ticker, sentiment)
        ma_info = compute_sentiment_ma(ticker)
    except Exception:
        ma_info = {"ma5": None, "ma20": None}

    return MarketSnapshot(
        ticker=ticker,
        query=query,
        name=report.name,
        consensus_price=report.price.final,
        consensus_change_pct=report.change_pct.final,
        anomaly_flag=detect_anomaly_flag(report),
        timeline_text=timeline,
        news_compact_body=news_body,
        compact_digest=digest,
        sources_ok=list(report.sources_succeeded),
        sentiment_score=sentiment,
        sentiment_ma5=ma_info.get("ma5"),
        sentiment_ma20=ma_info.get("ma20"),
        timestamp=time.time(),
    )


def _fetch_news_local(query: str, limit: int = 5) -> list[dict]:
    """yfinance/Tavily 로컬 수집 — Gemini 미호출."""
    ticker = _resolve_yahoo_ticker(query)
    try:
        from news_analyzer import fetch_and_filter_news
        return fetch_and_filter_news(ticker, limit=limit, use_tavily=True)
    except ImportError:
        return []


def collect_consensus_local(
    query: str,
    timeout: float = 12.0,
    *,
    include_news: bool = True,
    extra_note: str = "",
) -> tuple[ValidationReport, MarketSnapshot]:
    """병렬 페치 → 교차검증 → 로컬 뉴스 압축. Gemini 0회."""
    results = fetch_all_parallel(query, timeout=timeout)
    report = cross_validate(results, query=query)
    news = _fetch_news_local(query) if include_news else []
    snap = build_market_snapshot(query, report, news_items=news, extra_note=extra_note)
    return report, snap


def refresh_market_snapshot_cache(
    query: str,
    timeout: float = 12.0,
    *,
    include_news: bool = True,
    extra_note: str = "",
) -> MarketSnapshot:
    """백그라운드 워커용 — 샌드박스 JSON 갱신만, Gemini 0회."""
    report, snap = collect_consensus_local(
        query, timeout=timeout, include_news=include_news, extra_note=extra_note,
    )
    if _cv_cache_enabled():
        try:
            from cv_cache_manager import upsert_snapshot
            upsert_snapshot(snap.to_dict())
        except ImportError:
            pass
    try:
        from local_intelligence import refresh_local_analysis_report
        refresh_local_analysis_report(query, timeout=timeout)
    except Exception:
        pass
    return snap


def _enhance_briefing_with_gemini(compact_digest: str, query: str) -> str:
    """On-Demand — compact_digest 만 Gemini에 전달 (raw 데이터 배제)."""
    if not compact_digest or not _cv_on_demand_gemini_enabled():
        return ""

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        try:
            from secure_config import get_secret
            api_key = get_secret("GEMINI_API_KEY") or ""
        except Exception:
            api_key = ""

    if not api_key:
        return ""

    try:
        import google.generativeai as genai
    except ImportError:
        return ""

    model_name = os.getenv("GEMINI_FLASH_MODEL", "gemini-3.5-flash")
    prompt = (
        f"종목: {query}\n"
        f"교차검증 요약(이 텍스트만 참고, 추가 데이터 요청 금지):\n{compact_digest}\n\n"
        "위 요약을 바탕으로 사용자에게 읽어줄 한국어 음성 브리핑 2문장 이내로 작성하세요. "
        "숫자는 요약에 있는 값만 사용하세요."
    )

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(model_name)
        resp = model.generate_content(prompt)
        _bump_cv_gemini()
        text = (getattr(resp, "text", None) or "").strip()
        return text[:500]
    except Exception:
        return ""


def analyze_stock_on_demand(query: str, timeout: float = 12.0) -> dict:
    """
    On-Demand — 캐시 우선, compact_digest 만 Gemini 주입.
    캐시 미스/만료 시 로컬 재수집(역시 Gemini 0) 후 브리핑.
    """
    snap_dict: dict | None = None
    report: ValidationReport | None = None

    if _cv_cache_enabled():
        try:
            from cv_cache_manager import get_snapshot, is_snapshot_fresh
            cached = get_snapshot(_resolve_yahoo_ticker(query))
            if cached and is_snapshot_fresh(cached):
                snap_dict = cached
        except ImportError:
            pass

    if snap_dict is None:
        report, snap = collect_consensus_local(query, timeout=timeout)
        snap_dict = snap.to_dict()
        if _cv_cache_enabled():
            try:
                from cv_cache_manager import upsert_snapshot
                upsert_snapshot(snap_dict)
            except ImportError:
                pass
    else:
        # 캐시 히트 — 로컬 브리핑은 timeline/digest 기반
        report = None

    briefing = snap_dict.get("timeline_text") or format_briefing(
        report if report else _report_from_snapshot(snap_dict)
    )
    gemini_note = _enhance_briefing_with_gemini(
        snap_dict.get("compact_digest") or "", query,
    )
    if gemini_note:
        briefing = gemini_note

    rep_dict = report_to_dict(report) if report else _report_dict_from_snapshot(snap_dict)

    return {
        "success": bool(snap_dict.get("sources_ok")),
        "briefing": briefing,
        "report": rep_dict,
        "detail": _detail_from_snapshot(snap_dict) if report is None else format_detail_report(report),
        "cached": report is None,
        "compact_digest": snap_dict.get("compact_digest", ""),
        "gemini_called": bool(gemini_note),
        "snapshot": snap_dict,
    }


def _report_from_snapshot(snap: dict) -> ValidationReport:
    """캐시 히트 시 format_briefing 폴백용 최소 ValidationReport."""
    fc = FieldConsensus(snap.get("consensus_price"), [], [], "캐시")
    cp = FieldConsensus(snap.get("consensus_change_pct"), [], [], "캐시")
    return ValidationReport(
        query=snap.get("query", ""),
        name=snap.get("name"),
        currency="",
        sources_attempted=snap.get("sources_ok") or [],
        sources_succeeded=snap.get("sources_ok") or [],
        sources_failed=[],
        price=fc, volume=FieldConsensus(None, [], [], "캐시"),
        per=FieldConsensus(None, [], [], "캐시"),
        pbr=FieldConsensus(None, [], [], "캐시"),
        market_cap=FieldConsensus(None, [], [], "캐시"),
        eps=FieldConsensus(None, [], [], "캐시"),
        change_pct=cp,
        extras={},
        raw=[],
    )


def _report_dict_from_snapshot(snap: dict) -> dict:
    return {
        "query": snap.get("query"),
        "name": snap.get("name"),
        "cached": True,
        "consensus_price": snap.get("consensus_price"),
        "consensus_change_pct": snap.get("consensus_change_pct"),
        "anomaly_flag": snap.get("anomaly_flag"),
        "sources_succeeded": snap.get("sources_ok") or [],
        "compact_digest": snap.get("compact_digest"),
    }


def _detail_from_snapshot(snap: dict) -> str:
    return (
        f"━━━ 캐시 스냅샷: {snap.get('name') or snap.get('query')} ━━━\n"
        f"  consensus_price  {snap.get('consensus_price')}\n"
        f"  change_pct         {snap.get('consensus_change_pct')}\n"
        f"  anomaly            {snap.get('anomaly_flag')}\n"
        f"  digest             {(snap.get('compact_digest') or '')[:120]}"
    )


# ══════════════════════════════════════════════════════════════
#  공개 API — 한 줄 호출
# ══════════════════════════════════════════════════════════════
def analyze_stock(query: str, timeout: float = 12.0, *, on_demand: bool | None = None) -> dict:
    """
    사용자 요청 한 건 → 병렬 페치 → 교차검증 → 브리핑 문구·구조화 리포트 반환.

    CV_CACHE_ENABLED=1 이면 On-Demand 경로(캐시 + compact_digest Gemini) 사용.
    on_demand=False 로 레거시 동작 강제 가능.
    """
    use_on_demand = on_demand if on_demand is not None else _cv_cache_enabled()
    if use_on_demand and _cv_cache_enabled():
        return analyze_stock_on_demand(query, timeout=timeout)

    results = fetch_all_parallel(query, timeout=timeout)
    report = cross_validate(results, query=query)
    briefing = format_briefing(report)
    return {
        "success": bool(report.sources_succeeded),
        "briefing": briefing,
        "report": report_to_dict(report),
        "detail": format_detail_report(report),
    }


# ══════════════════════════════════════════════════════════════
#  LLM 도구 정의
# ══════════════════════════════════════════════════════════════
CROSSVAL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "analyze_stock_multi_source",
            "description": (
                "사용자가 종목을 물으면(예: '삼성전자 알아봐줘', '테슬라 정보 줘') "
                "Yahoo Finance, Finnhub, Alpha Vantage, Financial Modeling Prep, "
                "한국투자증권, 네이버 금융, 다음 금융, Open DART 등 다중 데이터 소스를 "
                "병렬 조회해 주가/거래량/PER/PBR/시총을 소스 신뢰도 가중으로 교차 검증한다. "
                "응답의 'briefing' 필드는 사용자에게 그대로 읽어줄 수 있는 한국어 음성 "
                "문구이므로 가능하면 그 문장을 기반으로 답변하라. 응답의 'price_confidence' "
                "(level/reason)가 '보통' 이하이면 답변에 신뢰도가 낮다는 점을 반드시 언급하라."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "종목명·티커·종목코드 (예: '삼성전자', '005930', 'AAPL', '테슬라')"
                    }
                },
                "required": ["query"],
            },
        },
    },
]

CROSSVAL_TOOL_NAMES = {t["function"]["name"] for t in CROSSVAL_TOOLS}


def _fast_preliminary(query: str) -> dict:
    """예비 답변 — 단일 실시간 시세(빠름) 또는 신선한 CV 캐시. 실패 시 success=False."""
    # ① 신선한 교차검증 캐시(있으면 즉시)
    try:
        from cv_cache_manager import cv_cache_enabled, get_snapshot, is_snapshot_fresh
        if cv_cache_enabled():
            snap = get_snapshot(_resolve_yahoo_ticker(query))
            if snap and is_snapshot_fresh(snap):
                price = snap.get("consensus_price")
                if price:
                    nm = snap.get("name") or query
                    b = (f"{nm} 최근 교차검증 합의가는 {price:,.0f} 정도예요. "
                         f"방금 값으로 다시 교차검증하고 AI로 정밀하게 보고 있어요, 곧 정리해서 알려드릴게요.")
                    return {"success": True, "briefing": b, "price": price,
                            "currency": "", "preliminary": True, "name": nm}
    except Exception:
        pass
    # ② 단일 실시간 시세(가장 빠른 소스)
    try:
        from stock_quant import get_stock_quote
        qd = get_stock_quote(query)
        if qd.get("success"):
            b = (qd.get("briefing", "").strip()
                 + " 지금 여러 무료 채널로 교차검증하고 AI로 정밀하게 보고 있어요, 곧 정리해서 알려드릴게요.")
            return {"success": True, "briefing": b.strip(),
                    "price": qd.get("price"), "currency": qd.get("currency", ""),
                    "preliminary": True, "name": qd.get("name") or query}
    except Exception:
        pass
    return {"success": False}


def execute_crossval_tool(name: str, arguments) -> dict:
    if isinstance(arguments, str):
        import json
        arguments = json.loads(arguments)
    if name == "analyze_stock_multi_source":
        q = (arguments or {}).get("query", "").strip()
        if not q:
            return {"success": False, "message": "조회할 종목명을 알려주세요."}
        # 2단계: 빠른 예비 답변을 먼저 돌려주고, 정밀 교차검증+AI 분석은 백그라운드에서
        try:
            from cv_followup import two_phase_enabled, get_cv_followup
            if two_phase_enabled():
                prelim = _fast_preliminary(q)
                if prelim.get("success"):
                    get_cv_followup().start(
                        q,
                        preliminary_price=prelim.get("price"),
                        currency=prelim.get("currency", ""),
                    )
                    return prelim
        except Exception:
            pass
        try:
            from hybrid_router import hybrid_routing_enabled
            if hybrid_routing_enabled():
                pass  # analyze_stock_multi_source 는 cross_validation.analyze_stock 직행
        except ImportError:
            pass
        return analyze_stock(q)
    return {"success": False, "message": f"알 수 없는 도구: {name}"}


__all__ = [
    "StockData", "FieldConsensus", "ValidationReport", "MarketSnapshot",
    "fetch_all_parallel", "cross_validate",
    "format_briefing", "format_detail_report", "report_to_dict",
    "compress_news_local", "build_timeline_text", "build_compact_digest",
    "build_market_snapshot", "collect_consensus_local",
    "record_sentiment_observation", "load_sentiment_timeseries",
    "build_sentiment_ma_frame", "compute_sentiment_ma",
    "set_sentiment_timeseries_path_for_tests",
    "refresh_market_snapshot_cache", "analyze_stock_on_demand",
    "analyze_stock",
    "reset_cv_gemini_call_count", "get_cv_gemini_call_count",
    "CROSSVAL_TOOLS", "CROSSVAL_TOOL_NAMES", "execute_crossval_tool",
]


if __name__ == "__main__":
    import sys, json
    q = sys.argv[1] if len(sys.argv) > 1 else "삼성전자"
    out = analyze_stock(q)
    print(out["detail"])
    print()
    print("[브리핑]", out["briefing"])
