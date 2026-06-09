"""
J.A.R.V.I.S — 실시간 주식 뉴스 분석기 (AI 노이즈 필터 + 감성 스코어링)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
관심 종목의 최신 뉴스를 수집 → 중복/유사 기사 1차 필터 → Gemini로
NOISE(찌라시/시황반복) vs CRITICAL(공시/실적/M&A/소송 등) 분류 →
주가 영향도를 -10(악재)~+10(호재) 정수로 스코어링한다.

흐름:
  yfinance.news / Tavily → 중복 제거 → gemini-1.5-flash → JSON 배열
                                                              ↓
                            |score| >= 7 → 긴급 알림 이벤트 (HUD 깜빡임 + TTS 인터럽트)

.env 설정:
  GEMINI_API_KEY=...                  (필수: 노이즈 필터/스코어링)
  TAVILY_API_KEY=...                  (선택: yfinance 폴백/보강)
  NEWS_WATCHLIST=AAPL,NVDA,005930.KS  (관심 종목, 쉼표 구분)
  NEWS_CHECK_INTERVAL=300             (초, 기본 5분)
  NEWS_URGENT_THRESHOLD=7             (|점수| 이 값 이상이면 긴급 브리핑)
  NEWS_MAX_PER_TICKER=8               (티커당 분석 기사 수)

의존성:
  pip install yfinance google-generativeai
"""

from __future__ import annotations

import os
import re
import json
import time
import threading
from datetime import datetime
from collections import deque
from difflib import SequenceMatcher

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False

import requests
try:
    import net  # 느린 WiFi: 연결 재사용·재시도·적응형 타임아웃
except Exception:
    net = requests


# ──────────────────────────────────────────────
#  설정
# ──────────────────────────────────────────────
GEMINI_API_KEY        = os.getenv("GEMINI_API_KEY", "")
TAVILY_API_KEY        = os.getenv("TAVILY_API_KEY", "")
NEWS_WATCHLIST        = [t.strip() for t in os.getenv("NEWS_WATCHLIST", "").split(",") if t.strip()]
NEWS_CHECK_INTERVAL   = int(os.getenv("NEWS_CHECK_INTERVAL", "300"))     # 초 (기본 5분)
NEWS_URGENT_THRESHOLD = int(os.getenv("NEWS_URGENT_THRESHOLD", "7"))     # |score| 임계값
NEWS_MAX_PER_TICKER   = int(os.getenv("NEWS_MAX_PER_TICKER", "8"))       # 티커당 분석 기사 수
NEWS_SIM_THRESHOLD    = 0.82                                            # 제목 유사도 중복 기준
NEWS_MODEL            = os.getenv("GEMINI_FLASH_MODEL", "gemini-3.5-flash")

# 한글 종목명 매핑 (stock_quant._resolve_ticker 와 동일 컨벤션)
_KR_NAME_MAP = {
    "005930.KS": "삼성전자", "000660.KS": "SK하이닉스",
    "035420.KS": "네이버", "035720.KS": "카카오",
    "005380.KS": "현대차", "000270.KS": "기아",
    "373220.KS": "LG에너지솔루션", "207940.KS": "삼성바이오로직스",
    "051910.KS": "LG화학", "006400.KS": "삼성SDI",
}


def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] 📰 [뉴스] {msg}")


def _display_name(ticker: str) -> str:
    """티커 → 사람이 읽기 좋은 이름"""
    return _KR_NAME_MAP.get(ticker.upper(), ticker)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. 뉴스 수집 + 전처리 (중복/유사 기사 1차 필터)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _normalize_title(title: str) -> str:
    """제목 정규화 — 대소문자/공백/문장부호 제거 후 비교용 키 생성"""
    t = (title or "").lower().strip()
    t = re.sub(r"[\s\.\,\!\?\:\;\-\_\'\"\(\)\[\]·…“”‘’]+", "", t)
    return t


def _title_similarity(a: str, b: str) -> float:
    """두 제목의 유사도 (0~1)"""
    return SequenceMatcher(None, a, b).ratio()


def _parse_yf_item(item: dict) -> dict | None:
    """yfinance 뉴스 항목을 표준 형태로 파싱 (구/신 스키마 모두 지원)"""
    # 신 스키마: {"id":..., "content": {"title","summary","pubDate","provider":{"displayName"}, ...}}
    if isinstance(item.get("content"), dict):
        c = item["content"]
        title = c.get("title", "")
        if not title:
            return None
        summary = c.get("summary") or c.get("description") or ""
        provider = (c.get("provider") or {}).get("displayName", "")
        url = (c.get("canonicalUrl") or c.get("clickThroughUrl") or {}).get("url", "")
        published = c.get("pubDate") or c.get("displayTime") or ""
        return {"title": title, "summary": summary[:400],
                "publisher": provider, "link": url, "published": str(published)}

    # 구 스키마: {"uuid","title","publisher","link","providerPublishTime"}
    title = item.get("title", "")
    if not title:
        return None
    ts = item.get("providerPublishTime")
    published = ""
    if ts:
        try:
            published = datetime.fromtimestamp(int(ts)).isoformat()
        except Exception:
            published = str(ts)
    return {"title": title, "summary": item.get("summary", "")[:400],
            "publisher": item.get("publisher", ""), "link": item.get("link", ""),
            "published": published}


def fetch_news_yfinance(ticker: str, limit: int = NEWS_MAX_PER_TICKER) -> list[dict]:
    """yfinance stock.news 로 최신 뉴스 수집"""
    if not YF_AVAILABLE:
        return []
    try:
        raw = yf.Ticker(ticker).news or []
    except Exception as e:
        _log(f"yfinance 뉴스 수집 실패({ticker}): {e}")
        return []

    items = []
    for it in raw:
        parsed = _parse_yf_item(it)
        if parsed:
            items.append(parsed)
    return items[: limit * 2]  # 중복 제거 여유분


def fetch_news_tavily(ticker: str, limit: int = NEWS_MAX_PER_TICKER) -> list[dict]:
    """Tavily news 검색으로 뉴스 수집 (yfinance 폴백/보강)"""
    if not TAVILY_API_KEY:
        return []
    name = _display_name(ticker)
    query = f"{name} {ticker} stock news 실적 공시 OR earnings OR lawsuit OR acquisition"
    try:
        resp = net.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "basic",
                "topic": "news",
                "max_results": limit,
                "include_answer": False,
            },
            timeout=(5, 15),
        )
        if resp.status_code != 200:
            _log(f"Tavily 오류 {resp.status_code}")
            return []
        data = resp.json()
    except Exception as e:
        _log(f"Tavily 뉴스 수집 실패({ticker}): {e}")
        return []

    items = []
    for r in data.get("results", []):
        title = r.get("title", "")
        if not title:
            continue
        items.append({
            "title": title,
            "summary": (r.get("content", "") or "")[:400],
            "publisher": (r.get("url", "") or "").split("/")[2] if r.get("url") else "",
            "link": r.get("url", ""),
            "published": r.get("published_date", ""),
        })
    return items


def fetch_and_filter_news(ticker: str, limit: int = NEWS_MAX_PER_TICKER,
                          use_tavily: bool = True) -> list[dict]:
    """
    특정 티커의 최신 뉴스를 수집하고 중복/유사 기사를 1차 필터링한다.

    Args:
        ticker: 종목 티커 (예: AAPL, NVDA, 005930.KS)
        limit: 최종 반환 기사 수 상한
        use_tavily: yfinance 결과가 부족하면 Tavily 로 보강할지 여부

    Returns:
        중복 제거된 뉴스 dict 리스트
        [{"title","summary","publisher","link","published"}]
    """
    collected = fetch_news_yfinance(ticker, limit)

    # yfinance 결과가 부족하면 Tavily 로 보강
    if use_tavily and len(collected) < max(3, limit // 2):
        collected += fetch_news_tavily(ticker, limit)

    # ── 중복/유사 기사 1차 필터 ──
    deduped: list[dict] = []
    seen_keys: set[str] = set()
    norm_titles: list[str] = []

    for item in collected:
        norm = _normalize_title(item["title"])
        if not norm:
            continue
        # 완전 동일 제목 차단
        if norm in seen_keys:
            continue
        # 높은 유사도(부분 중복) 차단
        if any(_title_similarity(norm, prev) >= NEWS_SIM_THRESHOLD for prev in norm_titles):
            continue

        seen_keys.add(norm)
        norm_titles.append(norm)
        deduped.append(item)

        if len(deduped) >= limit:
            break

    return deduped


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. Gemini 노이즈 필터링 + 감성 스코어링
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_NEWS_SYSTEM_INSTRUCTION = """당신은 월스트리트 헤지펀드의 수석 뉴스 애널리스트입니다.
주어진 주식 뉴스 목록을 분석하여 각 기사를 정확히 두 가지 타입으로 분류하십시오.

[분류 기준]
- NOISE: 단순 시황 반복, 클릭 유도성 낚시 기사, 일반론, 주가 등락 단순 보도,
         추측성 칼럼, 광고성/홍보성 기사, 이미 시장에 반영된 반복 보도.
- CRITICAL: 기업의 본질 가치를 흔드는 중대 뉴스. 실적 발표/어닝 서프라이즈,
            공시, M&A/인수합병, 대형 계약/수주, 소송/규제/제재, 리콜/화재/생산중단,
            경영진 교체, 신제품/신기술 승인, 대규모 투자/감원 등.

[스코어링 규칙]
- CRITICAL 기사에 한해, 해당 뉴스가 주가에 미칠 영향을 -10(초강력 악재)에서
  +10(초강력 호재) 사이의 "정수"로 산출하십시오. 0은 사용하지 마십시오.
- NOISE 기사는 score 를 0 으로 설정하십시오.
- reason 은 판단 근거를 한국어 1문장으로 간결하게 요약하십시오.

[출력 형식 — 매우 중요]
설명, 인사말, 마크다운, 코드블록 없이 "오직 아래 구조의 JSON 배열만" 반환하십시오.
입력된 모든 기사를 동일한 순서로 포함해야 합니다.
[{"title": "뉴스제목", "type": "CRITICAL", "score": -8, "reason": "공장 화재로 인한 생산 중단 공시"}]
"""


def _strip_json_fence(text: str) -> str:
    """모델이 코드펜스를 붙였을 경우 제거"""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def analyze_news_with_gemini(news_items: list[dict], ticker: str = "") -> list[dict]:
    """
    전처리된 뉴스 리스트를 Gemini(gemini-1.5-flash)에 전달하여
    NOISE/CRITICAL 분류 + 감성 점수를 산출한다.

    Returns:
        [{"title","type","score","reason"}] — 입력 기사 수만큼
    """
    if not news_items:
        return []
    if not GEMINI_API_KEY:
        _log("GEMINI_API_KEY 없음 — 스코어링 불가")
        return []

    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
    except Exception as e:
        _log(f"Gemini 초기화 실패: {e}")
        return []

    # 모델에 넘길 입력 구성 (제목 + 요약)
    payload = []
    for i, n in enumerate(news_items):
        payload.append({
            "index": i,
            "title": n["title"],
            "summary": (n.get("summary") or "")[:300],
            "publisher": n.get("publisher", ""),
        })

    name = _display_name(ticker) if ticker else ""
    prompt = (
        f"분석 대상 종목: {name} ({ticker})\n"
        f"아래는 수집된 뉴스 목록입니다. 지침에 따라 분류/스코어링하여 JSON 배열로만 답하십시오.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )

    try:
        model = genai.GenerativeModel(
            model_name=NEWS_MODEL,
            system_instruction=_NEWS_SYSTEM_INSTRUCTION,
        )
        resp = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.2,
                response_mime_type="application/json",
            ),
        )
        raw = _strip_json_fence(resp.text or "")
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        _log(f"JSON 파싱 실패: {e} — 원문 일부: {raw[:120] if 'raw' in dir() else ''}")
        return []
    except Exception as e:
        _log(f"Gemini 분석 실패: {e}")
        return []

    if not isinstance(parsed, list):
        return []

    # 결과 정규화 (타입/점수 유효성 보정)
    results = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        ntype = str(item.get("type", "NOISE")).upper()
        if ntype not in ("NOISE", "CRITICAL"):
            ntype = "NOISE"
        try:
            score = int(item.get("score", 0))
        except (TypeError, ValueError):
            score = 0
        score = max(-10, min(10, score))
        if ntype == "NOISE":
            score = 0
        results.append({
            "title": str(item.get("title", ""))[:200],
            "type": ntype,
            "score": score,
            "reason": str(item.get("reason", ""))[:200],
        })
    return results


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. 티커 단위 종합 분석 (수집→필터→스코어링→이벤트)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_overlay_table(ticker: str, critical_items: list[dict]) -> dict:
    """screen_overlay 의 type=table 로 바로 투사 가능한 표 데이터 생성.
    셀에 +/- 부호가 들어가면 오버레이가 자동으로 녹색/적색으로 표시한다."""
    name = _display_name(ticker)
    rows = []
    for it in critical_items[:8]:
        score = it["score"]
        sign = f"{score:+d}"   # +8 / -8 형태 → 색상 자동 처리
        rows.append([it["title"][:22], sign, it["reason"][:24]])
    return {
        "type": "table",
        "title": f"🚨 {name} 긴급 뉴스 | 감성 스코어",
        "headers": ["뉴스", "점수", "사유"],
        "rows": rows,
    }


def _briefing_text(ticker: str, item: dict) -> str:
    """긴급 뉴스 1건의 음성 브리핑 텍스트"""
    name = _display_name(ticker)
    score = item["score"]
    if score >= NEWS_URGENT_THRESHOLD:
        tone = "초강력 호재"
    elif score <= -NEWS_URGENT_THRESHOLD:
        tone = "초강력 악재"
    else:
        tone = "호재" if score > 0 else "악재"
    return (f"선생님, 긴급 속보입니다. {name} 관련 {tone} 뉴스가 포착되었습니다. "
            f"{item['reason']} 예상 영향 점수는 {score:+d}점입니다.")


def score_ticker_news(ticker: str, limit: int = NEWS_MAX_PER_TICKER,
                      use_tavily: bool = True) -> dict:
    """
    한 종목의 뉴스를 수집→중복필터→Gemini 스코어링까지 수행하는 통합 함수.

    Returns:
        {
          "success": bool, "ticker", "name",
          "total": 전체 수집 수, "noise": 노이즈 수,
          "critical": [{"title","type","score","reason"}],  # CRITICAL만
          "analyzed": [...전체 분석 결과...]
        }
    """
    name = _display_name(ticker)
    news = fetch_and_filter_news(ticker, limit, use_tavily=use_tavily)
    if not news:
        return {"success": False, "ticker": ticker, "name": name,
                "message": "수집된 뉴스가 없습니다.", "critical": [], "analyzed": []}

    analyzed = analyze_news_with_gemini(news, ticker)
    if not analyzed:
        return {"success": False, "ticker": ticker, "name": name,
                "message": "Gemini 분석에 실패했습니다.", "critical": [], "analyzed": []}

    critical = [a for a in analyzed if a["type"] == "CRITICAL"]
    noise_cnt = len(analyzed) - len(critical)

    return {
        "success": True,
        "ticker": ticker,
        "name": name,
        "total": len(analyzed),
        "noise": noise_cnt,
        "critical": critical,
        "analyzed": analyzed,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. 백그라운드 워커 (5분마다 관심 종목 감시)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class NewsAnalyzer:
    """
    관심 종목 뉴스 백그라운드 감시 데몬.
    NEWS_CHECK_INTERVAL(기본 5분)마다 watchlist 를 순회하며
    CRITICAL 뉴스를 스코어링하고, |score| >= NEWS_URGENT_THRESHOLD 인
    초강력 호재/악재를 '긴급(urgent)' 이벤트로 큐에 적재한다.

    사용법:
        analyzer = NewsAnalyzer(watchlist=["AAPL","NVDA"])
        analyzer.start()
        # 메인 루프에서:
        for evt in analyzer.get_events():
            if evt["urgent"]: ...긴급 브리핑...
    """

    def __init__(self, watchlist: list[str] | None = None,
                 interval: int = NEWS_CHECK_INTERVAL,
                 urgent_threshold: int = NEWS_URGENT_THRESHOLD):
        self.watchlist = watchlist if watchlist is not None else list(NEWS_WATCHLIST)
        self.interval = interval
        self.urgent_threshold = urgent_threshold
        self.enabled = bool(self.watchlist) and bool(GEMINI_API_KEY)

        self._event_q = deque(maxlen=50)
        self._lock = threading.Lock()
        self._thread = None
        self._running = False
        self._seen_titles: set[str] = set()   # 이미 알린 뉴스(중복 알림 방지)
        self._check_count = 0
        self._total_critical = 0

    # ── 라이프사이클 ──
    def start(self):
        if not self.enabled:
            _log("뉴스 감시 비활성: NEWS_WATCHLIST 와 GEMINI_API_KEY 설정 필요")
            return
        self._running = True
        self._thread = threading.Thread(target=self._daemon_loop, daemon=True)
        self._thread.start()
        _log(f"뉴스 감시 시작: {', '.join(self.watchlist)} | 간격={self.interval}초 "
             f"| 긴급 임계={self.urgent_threshold}")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def get_events(self) -> list[dict]:
        """대기 중인 이벤트 모두 반환 (메인 루프에서 폴링)"""
        events = []
        with self._lock:
            while self._event_q:
                events.append(self._event_q.popleft())
        return events

    # ── 내부 ──
    def _daemon_loop(self):
        # 첫 사이클은 살짝 지연 (부팅 직후 부하 분산)
        time.sleep(5)
        while self._running:
            self._check_count += 1
            for ticker in self.watchlist:
                if not self._running:
                    break
                try:
                    self._check_ticker(ticker)
                except Exception as e:
                    _log(f"분석 오류({ticker}): {e}")
                time.sleep(1)  # 티커 간 간격 (API rate 완화)

            # 다음 사이클까지 대기 (중단 신호 빠르게 반영)
            slept = 0
            while self._running and slept < self.interval:
                time.sleep(2)
                slept += 2

    def _check_ticker(self, ticker: str):
        result = score_ticker_news(ticker)
        if not result.get("success"):
            return

        critical = result["critical"]
        if not critical:
            return

        # 신규 CRITICAL 만 추림 (이미 알린 제목 제외)
        fresh = []
        for it in critical:
            key = _normalize_title(it["title"])
            if key and key not in self._seen_titles:
                self._seen_titles.add(key)
                fresh.append(it)
        if not fresh:
            return

        self._total_critical += len(fresh)

        # 긴급(초강력) 뉴스와 일반 CRITICAL 분리
        urgent_items = [it for it in fresh if abs(it["score"]) >= self.urgent_threshold]

        overlay_table = _build_overlay_table(ticker, fresh)

        if urgent_items:
            # 가장 임팩트 큰 뉴스 기준으로 브리핑
            top = max(urgent_items, key=lambda x: abs(x["score"]))
            evt = {
                "source": "news",
                "ticker": ticker,
                "name": result["name"],
                "urgent": True,
                "score": top["score"],
                "title": top["title"],
                "reason": top["reason"],
                "critical_items": fresh,
                "overlay_table": overlay_table,
                "message": _briefing_text(ticker, top),
                "timestamp": datetime.now().isoformat(),
            }
            self._enqueue(evt)
            _log(f"🚨 긴급({top['score']:+d}) {result['name']}: {top['title'][:40]}")
        else:
            # 일반 CRITICAL — 대기 상태에서 차분히 브리핑
            top = max(fresh, key=lambda x: abs(x["score"]))
            evt = {
                "source": "news",
                "ticker": ticker,
                "name": result["name"],
                "urgent": False,
                "score": top["score"],
                "title": top["title"],
                "reason": top["reason"],
                "critical_items": fresh,
                "overlay_table": overlay_table,
                "message": (f"선생님, {result['name']} 관련 주목할 뉴스가 있습니다. "
                            f"{top['reason']} 예상 영향은 {top['score']:+d}점입니다."),
                "timestamp": datetime.now().isoformat(),
            }
            self._enqueue(evt)
            _log(f"◆ CRITICAL({top['score']:+d}) {result['name']}: {top['title'][:40]}")

    def _enqueue(self, evt):
        with self._lock:
            self._event_q.append(evt)

    @property
    def status(self):
        return {
            "running": self._running,
            "watchlist": self.watchlist,
            "checks": self._check_count,
            "total_critical": self._total_critical,
            "pending": len(self._event_q),
            "interval": f"{self.interval}초",
            "urgent_threshold": self.urgent_threshold,
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  5. LLM 도구 정의 (온디맨드 뉴스 분석)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NEWS_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "analyze_stock_news",
            "description": (
                "특정 주식 종목의 최신 뉴스를 실시간 수집하여 AI로 노이즈(찌라시/시황반복)를 "
                "걸러내고, 핵심 호재/악재(공시·실적·M&A·소송 등)만 -10~+10 감성 점수로 분석합니다. "
                "'엔비디아 뉴스 분석해줘', '삼성전자 무슨 일 있어?', '테슬라 호재 있어?' 같은 요청에 사용하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "종목 티커 (예: AAPL, NVDA, TSLA / 한국거래소: 005930.KS)",
                    },
                },
                "required": ["ticker"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_news_monitor_status",
            "description": "백그라운드 뉴스 감시 상태(관심 종목, 점검 횟수, 누적 CRITICAL 수)를 조회합니다.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

_analyzer_instance: NewsAnalyzer | None = None


def set_news_analyzer_instance(inst: NewsAnalyzer):
    global _analyzer_instance
    _analyzer_instance = inst


def execute_news_tool(tool_name, arguments):
    """뉴스 분석 도구 실행"""
    try:
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
    except Exception:
        arguments = {}

    if tool_name == "get_news_monitor_status":
        if not _analyzer_instance:
            return {"success": False, "message": "뉴스 감시기 없음"}
        return {"success": True, **_analyzer_instance.status}

    if tool_name == "analyze_stock_news":
        ticker = (arguments.get("ticker") or "").strip()
        if not ticker:
            return {"success": False, "message": "ticker 가 필요합니다."}

        result = score_ticker_news(ticker)
        if not result.get("success"):
            return result

        critical = result["critical"]
        if not critical:
            return {
                "success": True,
                "ticker": result["ticker"],
                "name": result["name"],
                "message": (f"{result['name']} 관련 중대 뉴스는 없습니다. "
                            f"수집 {result['total']}건 중 노이즈 {result['noise']}건뿐입니다."),
                "critical": [],
                "overlay_table": None,
            }

        top = max(critical, key=lambda x: abs(x["score"]))
        overlay_table = _build_overlay_table(ticker, critical)
        briefing = (f"{result['name']} 뉴스 분석 결과, "
                    f"중대 뉴스 {len(critical)}건을 발라냈습니다. "
                    f"가장 영향이 큰 소식은 '{top['reason']}', 감성 점수 {top['score']:+d}점입니다.")
        return {
            "success": True,
            "ticker": result["ticker"],
            "name": result["name"],
            "briefing": briefing,
            "critical": critical,
            "noise_count": result["noise"],
            "overlay_table": overlay_table,
        }

    return {"success": False, "message": f"알 수 없는 도구: {tool_name}"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  단독 실행 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    import sys
    test_ticker = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    print(f"\n=== {test_ticker} 뉴스 수집 + 필터 테스트 ===")
    news = fetch_and_filter_news(test_ticker)
    for i, n in enumerate(news):
        print(f"[{i}] {n['title']}  ({n['publisher']})")

    print(f"\n=== Gemini 노이즈 필터 + 스코어링 ===")
    res = score_ticker_news(test_ticker)
    print(json.dumps(res, ensure_ascii=False, indent=2))
