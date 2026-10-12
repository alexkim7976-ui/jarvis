"""
J.A.R.V.I.S — CNN 뉴스 + Twitter/X 기반 내일 주가 전망
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
종목별 CNN Business 헤드라인과 Twitter(X) 최근 언급을 모아 감성·모멘텀을
합산한 뒤, 다음 거래일 방향(상승/하락/보합)과 신뢰도를 브리핑한다.

수집:
  CNN  → CNN Money RSS + Google News(site:cnn.com) + (선택) Tavily/DDG
  X/트위터 → X API v2 recent search (Bearer) → 웹검색(site:x.com) 폴백

.env:
  GEMINI_API_KEY=...                 (감성·전망 요약, 없으면 Ollama/로컬)
  TAVILY_API_KEY=...                 (선택: CNN/X 검색 보강)
  X_BEARER_TOKEN=...                 (또는 TWITTER_BEARER_TOKEN — X API v2)
  FORECAST_CNN_MAX=8
  FORECAST_TWITTER_MAX=12

도구:
  analyze_tomorrow_stock(ticker) — "내일 주가 어떻게 될까", "CNN이랑 트위터로 전망"
"""
from __future__ import annotations

from speech_style import USER_DISPLAY_NAME

import html
import json
import os
import re
import time
import urllib.parse
from datetime import datetime, timedelta
from typing import Any

import requests

try:
    import net
except Exception:
    net = requests

try:
    from free_mode import free_only_enabled, use_paid_search
except ImportError:
    def free_only_enabled():  # type: ignore
        return False

    def use_paid_search():  # type: ignore
        return bool(os.getenv("TAVILY_API_KEY", ""))


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
X_BEARER_TOKEN = (
    os.getenv("X_BEARER_TOKEN", "")
    or os.getenv("TWITTER_BEARER_TOKEN", "")
).strip()
FORECAST_MODEL = os.getenv("GEMINI_FLASH_MODEL", "gemini-3.5-flash")
FORECAST_CNN_MAX = int(os.getenv("FORECAST_CNN_MAX", "8"))
FORECAST_TWITTER_MAX = int(os.getenv("FORECAST_TWITTER_MAX", "12"))
RSS_TIMEOUT = 4

_KR_NAME_MAP = {
    "005930.KS": "삼성전자", "000660.KS": "SK하이닉스",
    "035420.KS": "네이버", "035720.KS": "카카오",
    "005380.KS": "현대차", "000270.KS": "기아",
    "373220.KS": "LG에너지솔루션", "NVDA": "엔비디아",
    "AAPL": "애플", "TSLA": "테슬라", "MSFT": "마이크로소프트",
    "AMZN": "아마존", "GOOGL": "구글", "META": "메타",
    "AMD": "AMD", "INTC": "인텔",
}

_TICKER_KEYWORDS: dict[str, list[str]] = {
    "005930.KS": ["삼성전자", "Samsung Electronics"],
    "000660.KS": ["SK하이닉스", "SK Hynix"],
    "035420.KS": ["네이버", "Naver"],
    "035720.KS": ["카카오", "Kakao"],
    "005380.KS": ["현대차", "Hyundai Motor"],
    "NVDA": ["엔비디아", "NVIDIA"],
    "AAPL": ["애플", "Apple"],
    "TSLA": ["테슬라", "Tesla"],
    "MSFT": ["마이크로소프트", "Microsoft"],
    "AMZN": ["아마존", "Amazon"],
    "GOOGL": ["구글", "Alphabet Google"],
    "META": ["메타", "Meta Platforms"],
    "AMD": ["AMD", "Advanced Micro Devices"],
}

_CNN_RSS = [
    "http://rss.cnn.com/rss/money_latest.rss",
    "http://rss.cnn.com/rss/money_markets.rss",
    "http://rss.cnn.com/rss/money_news_international.rss",
]

_RSS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,ko;q=0.8",
}

_BULLISH = (
    "surge", "rally", "soar", "beat", "upgrade", "record", "bull", "gain",
    "rise", "jump", "strong", "growth", "profit", "buy", "outperform",
    "상승", "급등", "호재", "실적호조", "매수", "돌파", "최고", "수주",
)
_BEARISH = (
    "plunge", "crash", "fall", "miss", "downgrade", "bear", "loss", "cut",
    "drop", "weak", "lawsuit", "probe", "ban", "tariff", "recession", "sell",
    "하락", "급락", "악재", "실적부진", "매도", "소송", "규제", "감산", "적자",
)


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] 📡 [내일전망] {msg}")


def _display_name(ticker: str) -> str:
    return _KR_NAME_MAP.get(ticker.upper(), ticker)


def _resolve_ticker(query: str) -> str:
    try:
        from stock_quant import _resolve_ticker as resolve
        return resolve(query)
    except Exception:
        q = (query or "").strip()
        if q.isdigit() and len(q) == 6:
            return f"{q}.KS"
        return q.upper() if q.isascii() else q


def _keywords_for(ticker: str) -> list[str]:
    mapped = _TICKER_KEYWORDS.get(ticker.upper(), [])
    sym = ticker.split(".")[0].upper()
    name = _display_name(ticker)
    out = [sym, name] + list(mapped)
    # 중복 제거, 짧은 키 유지
    seen, uniq = set(), []
    for k in out:
        k = (k or "").strip()
        if not k:
            continue
        key = k.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(k)
    return uniq


def _strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def _normalize_title(title: str) -> str:
    t = (title or "").lower().strip()
    return re.sub(r"[\s\.\,\!\?\:\;\-\_\'\"\(\)\[\]·…“”‘’]+", "", t)


# ──────────────────────────────────────────────
#  RSS / 검색 유틸
# ──────────────────────────────────────────────
try:
    from defusedxml import ElementTree as ET  # type: ignore
except ImportError:
    from xml.etree import ElementTree as ET  # type: ignore


def _fetch_rss(url: str, max_items: int = 15) -> list[dict]:
    try:
        r = net.get(url, headers=_RSS_HEADERS, timeout=RSS_TIMEOUT)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        _log(f"RSS 실패 ({url[:50]}…): {e}")
        return []

    channel = root.find("channel")
    if channel is None:
        return []
    items = []
    for el in channel.findall("item")[:max_items]:
        title = _strip_html(el.findtext("title", ""))
        if not title:
            continue
        desc = _strip_html(el.findtext("description", ""))[:200]
        link = (el.findtext("link", "") or "").strip()
        pub = (el.findtext("pubDate", "") or "")[:30]
        src_el = el.find("source")
        source = src_el.text.strip() if src_el is not None and src_el.text else "CNN"
        items.append({
            "title": title,
            "summary": desc,
            "link": link,
            "published": pub,
            "source": source or "CNN",
        })
    return items


def _text_matches_ticker(text: str, keywords: list[str]) -> bool:
    if not text:
        return False
    low = text.lower()
    for kw in keywords:
        if len(kw) <= 2:
            # 짧은 심볼은 단어 경계 검사
            if re.search(rf"(?<![a-z0-9]){re.escape(kw.lower())}(?![a-z0-9])", low):
                return True
        elif kw.lower() in low:
            return True
    return False


def _web_search(query: str, max_results: int = 6) -> list[dict]:
    """Tavily(유료 허용 시) → DuckDuckGo 폴백."""
    results: list[dict] = []
    if use_paid_search() and TAVILY_API_KEY:
        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=TAVILY_API_KEY)
            resp = client.search(query, max_results=max_results, search_depth="basic")
            for r in (resp.get("results") or [])[:max_results]:
                results.append({
                    "title": r.get("title", ""),
                    "summary": (r.get("content") or "")[:240],
                    "link": r.get("url", ""),
                    "source": "tavily",
                })
            if results:
                return results
        except Exception as e:
            _log(f"Tavily 검색 실패: {e}")

    try:
        from free_search import duckduckgo_search
        out = duckduckgo_search(query, max_results=max_results)
        if out.get("success"):
            for r in out.get("results") or []:
                results.append({
                    "title": r.get("title", ""),
                    "summary": (r.get("content") or "")[:240],
                    "link": r.get("url", ""),
                    "source": "duckduckgo",
                })
    except Exception as e:
        _log(f"DDG 검색 실패: {e}")
    return results


# ──────────────────────────────────────────────
#  1. CNN 수집
# ──────────────────────────────────────────────
def fetch_cnn_news(ticker: str, limit: int = FORECAST_CNN_MAX) -> list[dict]:
    """종목 관련 CNN 헤드라인 수집."""
    keywords = _keywords_for(ticker)
    collected: list[dict] = []
    seen: set[str] = set()

    def _add(item: dict, src: str):
        title = (item.get("title") or "").strip()
        key = _normalize_title(title)
        if not title or not key or key in seen:
            return
        blob = f"{title} {item.get('summary', '')}"
        if not _text_matches_ticker(blob, keywords):
            return
        seen.add(key)
        collected.append({
            "title": title,
            "summary": (item.get("summary") or "")[:240],
            "link": item.get("link", ""),
            "published": item.get("published", ""),
            "source": src,
            "channel": "cnn",
        })

    # 1) CNN Money RSS — 티커 키워드 필터
    for url in _CNN_RSS:
        for it in _fetch_rss(url, max_items=20):
            _add(it, "CNN RSS")
            if len(collected) >= limit:
                return collected[:limit]

    # 2) Google News RSS — site:cnn.com
    en = keywords[1] if len(keywords) > 1 else keywords[0]
    q = urllib.parse.quote_plus(f'{en} OR {keywords[0]} stock when:2d site:cnn.com')
    gurl = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    for it in _fetch_rss(gurl, max_items=12):
        it["source"] = it.get("source") or "CNN via Google News"
        _add(it, "CNN/Google News")
        if len(collected) >= limit:
            return collected[:limit]

    # 3) 웹 검색 보강
    if len(collected) < max(3, limit // 2):
        for r in _web_search(f"{en} stock site:cnn.com", max_results=6):
            _add(r, "CNN/web")
            if len(collected) >= limit:
                break

    return collected[:limit]


# ──────────────────────────────────────────────
#  2. Twitter / X 수집
# ──────────────────────────────────────────────
def _fetch_twitter_api(ticker: str, limit: int) -> list[dict]:
    if not X_BEARER_TOKEN:
        return []
    keywords = _keywords_for(ticker)
    sym = ticker.split(".")[0].upper()
    # X recent search: cashtag + 영문 회사명
    en = keywords[1] if len(keywords) > 1 else keywords[0]
    query = f'(${sym} OR "{en}") (stock OR shares OR earnings OR market) lang:en -is:retweet'
    params = {
        "query": query,
        "max_results": min(max(limit, 10), 100),
        "tweet.fields": "created_at,public_metrics,lang",
    }
    try:
        r = net.get(
            "https://api.twitter.com/2/tweets/search/recent",
            headers={"Authorization": f"Bearer {X_BEARER_TOKEN}"},
            params=params,
            timeout=8,
        )
        if r.status_code == 429:
            _log("X API rate limit")
            return []
        if r.status_code != 200:
            _log(f"X API HTTP {r.status_code}: {r.text[:120]}")
            return []
        data = r.json().get("data") or []
        out = []
        for tw in data[:limit]:
            text = (tw.get("text") or "").strip()
            if not text:
                continue
            metrics = tw.get("public_metrics") or {}
            out.append({
                "title": text[:180],
                "summary": text[:280],
                "link": f"https://x.com/i/web/status/{tw.get('id', '')}",
                "published": tw.get("created_at", ""),
                "source": "Twitter/X API",
                "channel": "twitter",
                "likes": int(metrics.get("like_count") or 0),
                "retweets": int(metrics.get("retweet_count") or 0),
            })
        return out
    except Exception as e:
        _log(f"X API 실패: {e}")
        return []


def _fetch_twitter_web(ticker: str, limit: int) -> list[dict]:
    """API 키 없을 때 site:x.com / twitter 웹검색 폴백."""
    keywords = _keywords_for(ticker)
    sym = ticker.split(".")[0].upper()
    en = keywords[1] if len(keywords) > 1 else keywords[0]
    queries = [
        f"${sym} OR {en} stock site:x.com",
        f"{en} stock twitter OR x.com",
    ]
    seen: set[str] = set()
    out: list[dict] = []
    for q in queries:
        for r in _web_search(q, max_results=max(4, limit // 2)):
            title = (r.get("title") or "").strip()
            key = _normalize_title(title)
            if not title or key in seen:
                continue
            blob = f"{title} {r.get('summary', '')}"
            if not _text_matches_ticker(blob, keywords + [f"${sym}"]):
                # 웹검색은 느슨하게: 심볼/$심볼만 있어도 허용
                if sym.lower() not in blob.lower() and f"${sym.lower()}" not in blob.lower():
                    continue
            seen.add(key)
            out.append({
                "title": title,
                "summary": (r.get("summary") or "")[:240],
                "link": r.get("link", ""),
                "published": "",
                "source": "Twitter/web",
                "channel": "twitter",
                "likes": 0,
                "retweets": 0,
            })
            if len(out) >= limit:
                return out
    return out


def fetch_twitter_posts(ticker: str, limit: int = FORECAST_TWITTER_MAX) -> list[dict]:
    posts = _fetch_twitter_api(ticker, limit)
    if len(posts) >= max(3, limit // 3):
        return posts[:limit]
    web = _fetch_twitter_web(ticker, limit)
    # API 결과 + 웹 폴백 병합
    seen = {_normalize_title(p["title"]) for p in posts}
    for w in web:
        key = _normalize_title(w["title"])
        if key in seen:
            continue
        seen.add(key)
        posts.append(w)
        if len(posts) >= limit:
            break
    return posts[:limit]


# ──────────────────────────────────────────────
#  3. 감성 스코어링
# ──────────────────────────────────────────────
def _local_score_text(text: str) -> int:
    low = (text or "").lower()
    score = 0
    for w in _BULLISH:
        if w.lower() in low:
            score += 1
    for w in _BEARISH:
        if w.lower() in low:
            score -= 1
    return max(-10, min(10, score))


def _score_items_local(items: list[dict]) -> list[dict]:
    scored = []
    for it in items:
        text = f"{it.get('title', '')} {it.get('summary', '')}"
        s = _local_score_text(text)
        scored.append({
            **it,
            "sentiment": s,
            "tone": "호재" if s > 1 else ("악재" if s < -1 else "중립"),
        })
    return scored


def _avg_sentiment(scored: list[dict]) -> float:
    if not scored:
        return 0.0
    return sum(float(x.get("sentiment") or 0) for x in scored) / len(scored)


def _score_with_llm(ticker: str, cnn: list[dict], tweets: list[dict]) -> dict | None:
    """Gemini/Ollama로 내일 방향·신뢰도·한줄 근거 JSON 생성. 실패 시 None."""
    name = _display_name(ticker)
    payload = {
        "ticker": ticker,
        "name": name,
        "horizon": "next_trading_day",
        "cnn_headlines": [
            {"title": c.get("title"), "summary": c.get("summary", "")[:160]}
            for c in cnn[:8]
        ],
        "twitter_posts": [
            {"text": t.get("title"), "likes": t.get("likes", 0)}
            for t in tweets[:10]
        ],
    }
    instruction = (
        "당신은 단기 주식 센티먼트 애널리스트입니다. "
        "CNN 헤드라인과 Twitter/X 반응만으로 '다음 거래일' 방향성을 추정하세요. "
        "확정 예측이 아니라 정보 기반 시나리오입니다. "
        "JSON만 출력: "
        '{"direction":"UP|DOWN|FLAT","confidence":0~100,'
        '"cnn_score":-10~10,"twitter_score":-10~10,"combined_score":-10~10,'
        '"drivers":["근거1","근거2"],"risks":["리스크1"],'
        '"briefing_ko":"한국어 2문장 브리핑"}'
    )
    prompt = f"{instruction}\n\n입력:\n{json.dumps(payload, ensure_ascii=False)}"

    # Gemini
    if not free_only_enabled() and GEMINI_API_KEY and GEMINI_API_KEY != "STORED_IN_KEYCHAIN":
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            model = genai.GenerativeModel(model_name=FORECAST_MODEL)
            resp = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )
            raw = (resp.text or "").strip()
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.M)
            return json.loads(raw)
        except Exception as e:
            _log(f"Gemini 전망 실패: {e}")

    # Ollama 폴백
    try:
        ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        model = os.getenv("OLLAMA_STOCK_MODEL") or os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")
        r = net.post(
            f"{ollama_url}/api/chat",
            json={
                "model": model,
                "stream": False,
                "format": "json",
                "messages": [
                    {"role": "system", "content": instruction},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                "options": {"temperature": 0.2},
            },
            timeout=90,
        )
        if r.status_code == 200:
            content = (r.json().get("message") or {}).get("content") or ""
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I | re.M)
            return json.loads(content)
    except Exception as e:
        _log(f"Ollama 전망 실패: {e}")
    return None


def _price_context(ticker: str) -> dict:
    """최근 종가·전일 대비 % (있으면 보조 신호)."""
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="5d")
        if hist is None or len(hist) < 2:
            return {}
        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return {}
        last, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
        chg = ((last - prev) / prev) * 100.0 if prev else 0.0
        return {"last_price": round(last, 4), "day_change_pct": round(chg, 2)}
    except Exception:
        return {}


def _direction_from_score(score: float) -> str:
    if score >= 1.5:
        return "UP"
    if score <= -1.5:
        return "DOWN"
    return "FLAT"


def _direction_ko(direction: str) -> str:
    return {"UP": "상승", "DOWN": "하락", "FLAT": "보합"}.get(
        (direction or "FLAT").upper(), "보합"
    )


def _build_overlay(
    ticker: str,
    name: str,
    direction: str,
    confidence: int,
    cnn_score: float,
    tw_score: float,
    combined: float,
    cnn_n: int,
    tw_n: int,
    drivers: list[str],
) -> dict:
    dir_ko = _direction_ko(direction)
    rows = [
        ["종목", f"{name} ({ticker})"],
        ["내일 방향", dir_ko],
        ["신뢰도", f"{confidence}%"],
        ["종합 감성", f"{combined:+.1f} / 10"],
        ["CNN 감성", f"{cnn_score:+.1f} ({cnn_n}건)"],
        ["Twitter 감성", f"{tw_score:+.1f} ({tw_n}건)"],
    ]
    for i, d in enumerate((drivers or [])[:3], 1):
        rows.append([f"근거{i}", str(d)[:60]])
    return {
        "type": "table",
        "title": f"내일 전망 · {name}",
        "headers": ["항목", "값"],
        "rows": rows,
    }


# ──────────────────────────────────────────────
#  4. 통합 전망
# ──────────────────────────────────────────────
def analyze_tomorrow_stock(ticker_query: str) -> dict:
    """
    CNN + Twitter 기반 다음 거래일 주가 방향 전망.

    Returns success/briefing/overlay_table/direction/confidence/...
    """
    raw = (ticker_query or "").strip()
    if not raw:
        return {"success": False, "message": "종목(ticker)이 필요합니다."}

    ticker = _resolve_ticker(raw)
    name = _display_name(ticker)
    _log(f"분석 시작: {name} ({ticker})")

    cnn = fetch_cnn_news(ticker, FORECAST_CNN_MAX)
    tweets = fetch_twitter_posts(ticker, FORECAST_TWITTER_MAX)
    price = _price_context(ticker)

    if not cnn and not tweets:
        return {
            "success": False,
            "ticker": ticker,
            "name": name,
            "message": (
                f"{name} 관련 CNN·Twitter 정보가 부족합니다. "
                "종목명을 확인하거나 X_BEARER_TOKEN을 설정해 주세요."
            ),
        }

    cnn_scored = _score_items_local(cnn)
    tw_scored = _score_items_local(tweets)
    cnn_avg = _avg_sentiment(cnn_scored)
    tw_avg = _avg_sentiment(tw_scored)

    # 가중 합산: CNN 55% + Twitter 45% (뉴스 신뢰도 약간 우위)
    local_combined = 0.55 * cnn_avg + 0.45 * tw_avg
    # 당일 모멘텀 소폭 반영 (±0.5점 캡)
    if price.get("day_change_pct") is not None:
        local_combined += max(-0.5, min(0.5, float(price["day_change_pct"]) / 4.0))

    llm = _score_with_llm(ticker, cnn, tweets) or {}

    def _clamp_score(v, default):
        try:
            return max(-10.0, min(10.0, float(v)))
        except (TypeError, ValueError):
            return default

    cnn_score = _clamp_score(llm.get("cnn_score"), cnn_avg)
    tw_score = _clamp_score(llm.get("twitter_score"), tw_avg)
    combined = _clamp_score(llm.get("combined_score"), local_combined)

    direction = str(llm.get("direction") or _direction_from_score(combined)).upper()
    if direction not in ("UP", "DOWN", "FLAT"):
        direction = _direction_from_score(combined)

    try:
        confidence = int(llm.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
    if confidence <= 0:
        # 로컬 신뢰도: |점수|·샘플 수 기반
        n = len(cnn) + len(tweets)
        confidence = int(min(85, 35 + abs(combined) * 6 + min(n, 10) * 2))
    confidence = max(15, min(90, confidence))

    drivers = llm.get("drivers") if isinstance(llm.get("drivers"), list) else []
    risks = llm.get("risks") if isinstance(llm.get("risks"), list) else []
    if not drivers:
        top_cnn = sorted(cnn_scored, key=lambda x: abs(x.get("sentiment", 0)), reverse=True)[:2]
        drivers = [c["title"][:80] for c in top_cnn if c.get("title")]
        if tw_scored:
            drivers.append(f"트위터 감성 {tw_avg:+.1f}점 ({len(tweets)}건)")

    briefing = (llm.get("briefing_ko") or "").strip()
    dir_ko = _direction_ko(direction)
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%m월 %d일")
    if not briefing:
        src_bits = []
        if cnn:
            src_bits.append(f"CNN {len(cnn)}건")
        if tweets:
            src_bits.append(f"트위터 {len(tweets)}건")
        src = "·".join(src_bits) or "수집 데이터"
        briefing = (
            f"{USER_DISPLAY_NAME}, {name}의 다음 거래일({tomorrow}) 전망은 "
            f"{dir_ko} 쪽으로 기울고 있으며 신뢰도는 {confidence}%입니다. "
            f"{src} 기준 종합 감성 {combined:+.1f}점입니다."
        )
        if risks:
            briefing += f" 주의: {risks[0]}"

    # 데이터 출처 메모
    tw_src = "X API" if any(t.get("source") == "Twitter/X API" for t in tweets) else (
        "웹검색 폴백" if tweets else "없음"
    )
    disclaimer = (
        "※ CNN·소셜 센티먼트 기반 단기 시나리오이며 투자 조언이 아닙니다."
    )

    overlay = _build_overlay(
        ticker, name, direction, confidence,
        cnn_score, tw_score, combined,
        len(cnn), len(tweets), drivers,
    )

    return {
        "success": True,
        "ticker": ticker,
        "name": name,
        "horizon": "next_trading_day",
        "direction": direction,
        "direction_ko": dir_ko,
        "confidence": confidence,
        "cnn_score": round(cnn_score, 2),
        "twitter_score": round(tw_score, 2),
        "combined_score": round(combined, 2),
        "cnn_count": len(cnn),
        "twitter_count": len(tweets),
        "twitter_source": tw_src,
        "drivers": drivers[:5],
        "risks": risks[:3],
        "price_context": price,
        "cnn_headlines": [
            {"title": c["title"], "sentiment": c.get("sentiment", 0), "source": c.get("source")}
            for c in cnn_scored[:5]
        ],
        "twitter_samples": [
            {"text": t["title"], "sentiment": t.get("sentiment", 0)}
            for t in tw_scored[:5]
        ],
        "briefing": f"{briefing} {disclaimer}",
        "message": briefing,
        "overlay_table": overlay,
        "analyzed_at": datetime.now().isoformat(timespec="seconds"),
    }


# ──────────────────────────────────────────────
#  5. LLM 도구
# ──────────────────────────────────────────────
FORECAST_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "analyze_tomorrow_stock",
            "description": (
                "CNN 뉴스와 Twitter/X 최근 반응을 모아 특정 종목의 '내일(다음 거래일)' "
                "주가 방향(상승/하락/보합)과 신뢰도를 분석합니다. "
                "'내일 주가 어떻게 될까', 'CNN이랑 트위터로 전망해줘', "
                "'엔비디아 내일 전망', '삼성전자 내일 오를까' 같은 요청에 사용하세요."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": (
                            "종목 티커 또는 한글/영문 이름 "
                            "(예: NVDA, AAPL, 엔비디아, 삼성전자, 005930.KS)"
                        ),
                    },
                },
                "required": ["ticker"],
            },
        },
    },
]


def execute_forecast_tool(tool_name: str, arguments: Any) -> dict:
    try:
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
    except Exception:
        arguments = {}

    if tool_name == "analyze_tomorrow_stock":
        ticker = (arguments or {}).get("ticker") or (arguments or {}).get("query") or ""
        return analyze_tomorrow_stock(str(ticker).strip())

    return {"success": False, "message": f"알 수 없는 도구: {tool_name}"}


if __name__ == "__main__":
    import sys
    q = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    print(json.dumps(analyze_tomorrow_stock(q), ensure_ascii=False, indent=2))
