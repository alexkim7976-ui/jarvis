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

import time
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Callable

from data_providers import get_provider


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
    else:
        # 해외: NAS/NYS 자동 판별 어려움 → NAS 우선, 실패 시 NYS 재시도(매퍼 내부)
        out["kis_overseas"] = ("NAS", yahoo_t)
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
    # PBR은 ratios-ttm 별도 호출
    r = p.ratios_ttm(symbol)
    if r.get("success"):
        data = r.get("data")
        if isinstance(data, list) and data:
            out.pbr = _safe_float(data[0].get("priceToBookRatioTTM"))
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
    if tk["_is_korean"]:
        c = tk["kis"]
        tasks.append(("한국투자증권", lambda c=c: _map_kis(c)))
        tasks.append(("LS증권",       lambda c=c: _map_ls(c)))
        tasks.append(("키움증권",     lambda c=c: _map_kiwoom(c)))
        tasks.append(("Open DART",    lambda c=c: _map_dart(c)))
    else:
        exch, sym = tk["kis_overseas"]
        tasks.append(("한국투자증권(해외)",
                      lambda exch=exch, sym=sym: _map_kis_overseas(exch, sym)))

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
def _consensus(field_name: str, results: list[StockData], tol: float) -> FieldConsensus:
    """field_name 값들을 모아 중앙값 기준 |편차| > tol 인 소스 제외 후 평균."""
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
        return FieldConsensus(pairs[0][1], pairs, [], "단일 소스")

    nums = [v for _, v in pairs]
    med = statistics.median(nums)
    accepted: list[tuple[str, float]] = []
    rejected: list[tuple[str, float, float]] = []
    for src, v in pairs:
        dev = abs(v - med) / abs(med) if med != 0 else 0.0
        (accepted if dev <= tol else rejected).append(
            (src, v) if dev <= tol else (src, v, dev)
        )

    if not accepted:
        return FieldConsensus(med, [], rejected, "전체 이상치 → 중앙값 채택")
    final = sum(v for _, v in accepted) / len(accepted)
    return FieldConsensus(final, accepted, rejected,
                          f"{len(accepted)}/{len(pairs)} 합의")


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
        if rej_n:
            rej_srcs = ", ".join(s for s, _, _ in report.price.rejected)
            price_txt += f" ({rej_srcs}의 값은 편차가 커 제외)"
        parts.append(price_txt + ",")

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
    for f in ("price", "change_pct", "volume", "per", "pbr", "market_cap", "eps"):
        c: FieldConsensus = getattr(report, f)
        if c.final is None:
            L.append(f"  {f:12} —  ({c.method})")
            continue
        L.append(f"  {f:12} {c.final:>15,.4f}  ({c.method})")
        for src, v in c.accepted:
            L.append(f"      ✓ {src:25} {v:>15,.4f}")
        for src, v, dev in c.rejected:
            L.append(f"      ✗ {src:25} {v:>15,.4f}  (편차 {dev*100:.1f}%)")
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
        "price": cdict(report.price), "volume": cdict(report.volume),
        "change_pct": cdict(report.change_pct),
        "per": cdict(report.per), "pbr": cdict(report.pbr),
        "market_cap": cdict(report.market_cap), "eps": cdict(report.eps),
        "extras": report.extras,
    }


# ══════════════════════════════════════════════════════════════
#  공개 API — 한 줄 호출
# ══════════════════════════════════════════════════════════════
def analyze_stock(query: str, timeout: float = 12.0) -> dict:
    """사용자 요청 한 건 → 병렬 페치 → 교차검증 → 브리핑 문구·구조화 리포트 반환."""
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
                "Yahoo Finance, Alpha Vantage, Financial Modeling Prep, 한국투자증권, "
                "LS증권, 키움증권, Open DART 등 다중 데이터 소스를 병렬 조회해 "
                "주가/거래량/PER/PBR/시총 등의 값을 교차 검증한다. "
                "응답의 'briefing' 필드는 검증 결과를 사용자에게 그대로 읽어줄 수 있는 "
                "한국어 음성 문구이므로 가능하면 그 문장을 기반으로 답변하라."
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


def execute_crossval_tool(name: str, arguments) -> dict:
    if isinstance(arguments, str):
        import json
        arguments = json.loads(arguments)
    if name == "analyze_stock_multi_source":
        q = (arguments or {}).get("query", "").strip()
        if not q:
            return {"success": False, "message": "조회할 종목명을 알려주세요."}
        return analyze_stock(q)
    return {"success": False, "message": f"알 수 없는 도구: {name}"}


__all__ = [
    "StockData", "FieldConsensus", "ValidationReport",
    "fetch_all_parallel", "cross_validate",
    "format_briefing", "format_detail_report", "report_to_dict",
    "analyze_stock",
    "CROSSVAL_TOOLS", "CROSSVAL_TOOL_NAMES", "execute_crossval_tool",
]


if __name__ == "__main__":
    import sys, json
    q = sys.argv[1] if len(sys.argv) > 1 else "삼성전자"
    out = analyze_stock(q)
    print(out["detail"])
    print()
    print("[브리핑]", out["briefing"])
