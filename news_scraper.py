"""
J.A.R.V.I.S — 실시간 뉴스 스크레이퍼 & AI 원인 요약 엔진
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
stock_monitor.py 의 AlertRule 이 발동되면 즉시 호출되어,
Google News RSS (1차) → yfinance news (폴백) 로 최신 헤드라인 3건을
수집하고, Gemini 1.5 Flash 로 주가 변동 원인을 1줄 한국어로 요약한다.

공개 API(RSS) 만 사용하므로 추가 키가 불필요하며, 전체 처리는
stock_monitor 의 백그라운드 스레드 내에서 완료된 뒤 이벤트 큐에
push 된다 — 메인 OpenCV 루프가 절대 블로킹되지 않는다.

흐름:
  stock_monitor._check_rule()
    └── _fetch_news_context()        ← 이 모듈 호출 (백그라운드 스레드)
          ├── fetch_ticker_headlines()
          │     ├── Google News RSS  (ko → en 순서)
          │     └── yfinance 폴백
          └── _gemini_summarize()    ← Gemini 1.5 Flash (GEMINI_API_KEY)
                └── "원인 1줄 요약"  → evt["message"] 에 합성

.env 필요:
  GEMINI_API_KEY=...  (필수: Gemini 요약)
  TAVILY_API_KEY=...  (선택: 추가 뉴스 소스)

의존성: pip install requests google-generativeai
"""
from __future__ import annotations

import html
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime

# 보안: stdlib xml.etree는 billion-laughs(엔티티 폭발)·외부 엔티티에 취약.
# 원격 RSS를 파싱하므로 defusedxml로 대체(미설치 시 폴백 + 파싱 시 경고).
try:
    from defusedxml import ElementTree as ET   # type: ignore
    _XML_HARDENED = True
except ImportError:
    from xml.etree import ElementTree as ET
    _XML_HARDENED = False

import requests
try:
    import net  # 느린 WiFi: 연결 재사용·재시도·적응형 타임아웃
except Exception:
    net = requests

GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "")
TAVILY_API_KEY   = os.getenv("TAVILY_API_KEY", "")
NEWS_MODEL       = os.getenv("GEMINI_FLASH_MODEL", "gemini-3.5-flash")
RSS_TIMEOUT      = 3    # 각 RSS 요청 타임아웃 (초)
GEMINI_TIMEOUT   = 5    # Gemini API 타임아웃 (초)
FALLBACK_SUMMARY = "현재 관련 주요 뉴스는 포착되지 않았습니다"

# 종목명 검색어 매핑 (한글명 → 영문명 쌍)
_TICKER_KEYWORDS: dict[str, list[str]] = {
    "005930.KS": ["삼성전자", "Samsung Electronics"],
    "000660.KS": ["SK하이닉스", "SK Hynix"],
    "035420.KS": ["네이버",    "Naver"],
    "035720.KS": ["카카오",    "Kakao"],
    "005380.KS": ["현대차",    "Hyundai Motor"],
    "000270.KS": ["기아",      "Kia Motors"],
    "039490.KS": ["키움증권",  "Kiwoom Securities"],
    "079370.KQ": ["제우스",    "Zeus"],
    "NVDA":  ["엔비디아",        "NVIDIA"],
    "AAPL":  ["애플",            "Apple"],
    "TSLA":  ["테슬라",          "Tesla"],
    "MSFT":  ["마이크로소프트",  "Microsoft"],
    "AMZN":  ["아마존",          "Amazon"],
    "GOOGL": ["구글",            "Google Alphabet"],
    "META":  ["메타",            "Meta Platforms"],
    "NFLX":  ["넷플릭스",        "Netflix"],
    "AMD":   ["AMD",             "Advanced Micro Devices"],
    "INTC":  ["인텔",            "Intel"],
    "BABA":  ["알리바바",        "Alibaba"],
}

_RSS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
}


def _log(level: str, msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    icons = {"INFO": "◈", "OK": "✓", "WARN": "⚠", "ERR": "✗"}
    print(f"[{ts}] {icons.get(level, '·')} [뉴스스크레이퍼] {msg}")


# ──────────────────────────────────────────────
#  검색어 생성
# ──────────────────────────────────────────────
def _build_search_queries(ticker: str, company_name: str) -> list[tuple[str, str]]:
    """
    (query, lang_params) 쌍 목록 반환.
    한국어 검색 → 영어 검색 순서로 시도.
    """
    mapped = _TICKER_KEYWORDS.get(ticker.upper(), [])
    ko_name = mapped[0] if mapped else company_name
    en_name = mapped[1] if len(mapped) > 1 else (
        ticker if not ticker.endswith((".KS", ".KQ")) else company_name
    )

    queries = []
    # 1. 한국어 RSS
    if ko_name:
        queries.append((f"{ko_name} 주가", "hl=ko&gl=KR&ceid=KR:ko"))
    # 2. 영어 RSS
    en_q = f"{en_name} stock" if not ticker.endswith((".KS", ".KQ")) else f"{en_name} 주가"
    queries.append((en_q, "hl=en-US&gl=US&ceid=US:en"))
    return queries


# ──────────────────────────────────────────────
#  RSS 수집 & 파싱
# ──────────────────────────────────────────────
def _fetch_rss_bytes(url: str) -> bytes | None:
    """HTTP GET으로 RSS raw bytes 수집 (타임아웃=RSS_TIMEOUT)"""
    try:
        r = net.get(url, headers=_RSS_HEADERS, timeout=RSS_TIMEOUT)
        r.raise_for_status()
        return r.content
    except requests.exceptions.Timeout:
        _log("WARN", f"RSS 타임아웃: {url[:60]}")
    except Exception as e:
        _log("WARN", f"RSS 수집 실패: {e}")
    return None


def _strip_html(text: str) -> str:
    """HTML 태그 제거 + 엔티티 디코딩"""
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def _parse_rss(xml_bytes: bytes, max_items: int = 5) -> list[dict]:
    """
    RSS 2.0 / Google News RSS XML → 표준 항목 리스트.
    [{"title", "summary", "link", "source", "published"}, ...]
    """
    items: list[dict] = []
    if not _XML_HARDENED:
        _log("WARN", "defusedxml 미설치 — XML 엔티티 폭발 보호 비활성(pip install defusedxml 권장)")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        _log("WARN", f"XML 파싱 오류: {e}")
        return items

    channel = root.find("channel")
    if channel is None:
        return items

    for item_el in channel.findall("item")[:max_items]:
        title = _strip_html(item_el.findtext("title", ""))
        if not title:
            continue

        # Google News RSS: description에 소스 링크 HTML이 들어 있어 본문 추출 무의미
        # → title만 신뢰하고 description은 60자 스니펫만 사용
        raw_desc  = _strip_html(item_el.findtext("description", ""))
        # Google News description은 <ol><li><a href>제목</a>...</li> 구조라
        # strip 후 남는 텍스트가 사실상 제목 반복이므로 너무 길면 버림
        snippet   = raw_desc[:120] if len(raw_desc) < len(title) * 3 else ""

        link      = item_el.findtext("link", "").strip()
        pub_date  = item_el.findtext("pubDate", "")[:25]
        # <source url="...">Bloomberg</source>
        src_el    = item_el.find("source")
        source    = src_el.text.strip() if src_el is not None and src_el.text else ""

        items.append({
            "title":     title,
            "summary":   snippet,
            "source":    source,
            "link":      link,
            "published": pub_date,
        })
    return items


# ──────────────────────────────────────────────
#  yfinance 폴백 파서 (기존 news_analyzer 재사용)
# ──────────────────────────────────────────────
def _yfinance_headlines(ticker: str, n: int) -> list[dict]:
    """yfinance stock.news API → 헤드라인 n개 (폴백 전용)"""
    try:
        import yfinance as yf
        # news_analyzer._parse_yf_item 은 구/신 스키마 통합 파서
        from news_analyzer import _parse_yf_item
        raw = yf.Ticker(ticker).news or []
        items = []
        for it in raw:
            parsed = _parse_yf_item(it)
            if parsed and parsed.get("title"):
                items.append({
                    "title":     parsed["title"],
                    "summary":   parsed.get("summary", "")[:120],
                    "source":    parsed.get("publisher", ""),
                    "link":      parsed.get("link", ""),
                    "published": parsed.get("published", ""),
                })
                if len(items) >= n:
                    break
        return items
    except Exception as e:
        _log("WARN", f"yfinance 폴백 실패 ({ticker}): {e}")
        return []


# ──────────────────────────────────────────────
#  공개 함수 1: 헤드라인 수집
# ──────────────────────────────────────────────
def fetch_ticker_headlines(ticker: str, company_name: str,
                           n: int = 3) -> list[dict]:
    """
    종목 관련 최신 뉴스 헤드라인을 최대 n개 반환.

    우선순위:
      1. Google News RSS (한국어)
      2. Google News RSS (영어)
      3. yfinance news (폴백)

    Returns:
        [{"title", "summary", "source", "link", "published"}, ...]
        실패 시 빈 리스트
    """
    for query, lang_params in _build_search_queries(ticker, company_name):
        q_enc = urllib.parse.quote(query)
        url   = f"https://news.google.com/rss/search?q={q_enc}&{lang_params}"
        raw   = _fetch_rss_bytes(url)
        if raw:
            items = _parse_rss(raw, max_items=n + 2)
            if items:
                _log("OK", f"RSS {len(items[:n])}건 수집 ({ticker}, q={query[:20]})")
                return items[:n]

    # 폴백: yfinance
    items = _yfinance_headlines(ticker, n)
    if items:
        _log("OK", f"yfinance 폴백 {len(items)}건 ({ticker})")
    return items


# ──────────────────────────────────────────────
#  Gemini 1.5 Flash 원인 요약
# ──────────────────────────────────────────────
def _gemini_summarize(headlines: list[dict], ticker: str,
                      company_name: str, change_info: str = "") -> str:
    """
    Gemini 1.5 Flash → 주가 변동 원인 1줄 한국어 요약 (30자 이내).
    실패 시 빈 문자열 반환.
    """
    if not GEMINI_API_KEY or not headlines:
        return ""
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(
            model_name=NEWS_MODEL,
            generation_config={
                "max_output_tokens": 80,
                "temperature": 0.1,   # 일관성 최우선
            },
        )

        headline_block = "\n".join(
            f"{i+1}. {h['title']}"
            + (f"  [{h['source']}]" if h.get("source") else "")
            + (f"  — {h['summary'][:80]}" if h.get("summary") else "")
            for i, h in enumerate(headlines[:3])
        )
        ctx_line = f"현재 주가 상황: {change_info}\n\n" if change_info else ""

        prompt = (
            f"{ctx_line}"
            f"[{company_name} ({ticker})] 최신 뉴스 헤드라인:\n"
            f"{headline_block}\n\n"
            "위 뉴스를 바탕으로 현재 주가 변동의 핵심 원인을 "
            "딱 1줄(한국어, 30자 이내, 명사구 형태)로 요약해. "
            "예시: '미 국채 금리 급등 및 공급망 우려' / '예상 하회 실적 발표' / '대규모 자사주 매입 발표'. "
            "원인이 불명확하면 '시장 전반 변동성 영향'으로 답해. "
            "따옴표·마침표·설명 없이 핵심 원인 구절만 출력."
        )

        # GEMINI_TIMEOUT 적용
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(model.generate_content, prompt)
            response = fut.result(timeout=GEMINI_TIMEOUT)

        raw = (response.text or "").strip()
        # 잔여 따옴표/마침표/줄바꿈 제거
        summary = re.sub(r'^[\"\'\s]+|[\"\'\.\s]+$', "", raw.split("\n")[0])
        _log("OK", f"Gemini 요약: {summary}")
        return summary

    except concurrent.futures.TimeoutError:
        _log("WARN", f"Gemini 요약 타임아웃 {GEMINI_TIMEOUT}초")
    except Exception as e:
        _log("WARN", f"Gemini 요약 실패: {e}")
    return ""


# ──────────────────────────────────────────────
#  공개 함수 2 (메인 진입점)
# ──────────────────────────────────────────────
def get_ticker_news_summary(ticker: str, company_name: str,
                            num_headlines: int = 3,
                            change_info: str = "") -> dict:
    """
    헤드라인 수집 → Gemini 1줄 원인 요약.

    Parameters:
        ticker        : yfinance 티커 (예: NVDA, 005930.KS)
        company_name  : 표시용 이름 (예: 엔비디아, 삼성전자)
        num_headlines : 수집할 헤드라인 수 (기본 3)
        change_info   : 변동 컨텍스트 문자열 (예: "전일 대비 -5.2%, 현재가 $850")

    Returns:
        {
          "success"   : bool,
          "headlines" : [{"title","summary","source","link","published"}, ...],
          "summary"   : str,    # Gemini 1줄 요약 (실패 시 FALLBACK_SUMMARY)
          "elapsed"   : float   # 처리 소요 시간(초)
        }
    """
    t0 = time.time()
    try:
        headlines = fetch_ticker_headlines(ticker, company_name, n=num_headlines)
        if not headlines:
            _log("INFO", f"헤드라인 없음 ({ticker})")
            return {
                "success":   False,
                "headlines": [],
                "summary":   FALLBACK_SUMMARY,
                "elapsed":   round(time.time() - t0, 2),
            }

        summary = _gemini_summarize(headlines, ticker, company_name, change_info)
        if not summary:
            summary = FALLBACK_SUMMARY

        elapsed = round(time.time() - t0, 2)
        _log("OK", f"뉴스 컨텍스트 완료 ({ticker}) — {elapsed}초, 헤드라인 {len(headlines)}건")
        return {
            "success":   True,
            "headlines": headlines,
            "summary":   summary,
            "elapsed":   elapsed,
        }

    except Exception as e:
        _log("ERR", f"뉴스 요약 전체 실패 ({ticker}): {e}")
        return {
            "success":   False,
            "headlines": [],
            "summary":   FALLBACK_SUMMARY,
            "elapsed":   round(time.time() - t0, 2),
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  단독 실행 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    for tk, nm in [("NVDA", "엔비디아"), ("005930.KS", "삼성전자"), ("TSLA", "테슬라")]:
        print(f"\n{'='*55}")
        print(f"▶ {nm} ({tk})")
        result = get_ticker_news_summary(tk, nm, change_info="전일 대비 -4.1%")
        print(f"  성공: {result['success']}  소요: {result['elapsed']}초")
        print(f"  요약: {result['summary']}")
        for i, h in enumerate(result.get("headlines", []), 1):
            print(f"  [{i}] {h['title'][:70]}")
