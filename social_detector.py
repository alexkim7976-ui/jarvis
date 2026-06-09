"""
J.A.R.V.I.S — Social Meme Stock Detector
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Reddit(r/wallstreetbets 등) 최신 게시글에서 미국 주식 티커 언급량을
집계하고, 과거 기준선 대비 500% 이상 폭증한 종목을 거래량 스파이크와
Gemini 감성 분석으로 교차 검증한다.

.env:
  SOCIAL_SUBREDDITS=wallstreetbets,stocks,investing
  SOCIAL_CHECK_INTERVAL=600
  SOCIAL_SPIKE_THRESHOLD=5.0          # 500% = 5배
  SOCIAL_BASELINE_FILE=social_baseline.json
  REDDIT_CLIENT_ID=...                # 선택(praw)
  REDDIT_CLIENT_SECRET=...            # 선택(praw)
  REDDIT_USER_AGENT=JARVIS social detector
  GEMINI_API_KEY=...

의존성:
  필수: requests yfinance pandas numpy google-generativeai(이미 프로젝트 사용)
  선택: praw
"""

from __future__ import annotations

import json
import os
import re
import time
import threading
from collections import Counter, defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
try:
    import net  # 느린 WiFi: 연결 재사용·재시도·적응형 타임아웃
except Exception:
    net = requests

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
SOCIAL_MODEL = os.getenv("GEMINI_FLASH_MODEL", "gemini-3.5-flash")  # 감성 분석 경량 작업 → FLASH
SOCIAL_SUBREDDITS = [s.strip() for s in os.getenv(
    "SOCIAL_SUBREDDITS", "wallstreetbets,stocks,investing"
).split(",") if s.strip()]
SOCIAL_CHECK_INTERVAL = int(os.getenv("SOCIAL_CHECK_INTERVAL", "600"))
SOCIAL_SPIKE_THRESHOLD = float(os.getenv("SOCIAL_SPIKE_THRESHOLD", "5.0"))
SOCIAL_BASELINE_FILE = Path(os.getenv("SOCIAL_BASELINE_FILE", "social_baseline.json"))
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "JARVIS social detector")

COMMON_WORDS = {
    "A", "I", "THE", "THIS", "THAT", "FOR", "ARE", "AND", "YOU", "YOLO",
    "DD", "CEO", "CFO", "USA", "AI", "ETF", "IPO", "SEC", "FED", "FOMO",
    "ATH", "USD", "GDP", "CPI", "PCE", "EPS", "PE", "IV", "IT", "OR",
    "ON", "IN", "TO", "OF", "BY", "BE", "CAN", "ALL", "NEW", "NOW",
    "LIKE", "NOT", "GOOD", "BAD", "BUY", "SELL", "HOLD", "MOON", "CALL",
    "PUT", "GAIN", "LOSS", "RISK", "NEWS", "POST", "VERY", "JUST",
}
WATCH_HINTS = {
    "GME", "AMC", "NVDA", "TSLA", "AAPL", "MSFT", "AMD", "META", "PLTR",
    "HOOD", "SOFI", "RIVN", "LCID", "NIO", "COIN", "MSTR", "SMCI", "BB",
}


def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] 📣 [소셜감지] {msg}")


def _load_baseline() -> dict:
    if not SOCIAL_BASELINE_FILE.exists():
        return {}
    try:
        with open(SOCIAL_BASELINE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_baseline(data: dict):
    try:
        with open(SOCIAL_BASELINE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _log(f"baseline 저장 실패: {e}")


def _extract_tickers(text: str) -> list:
    """게시글 텍스트에서 미국 주식 티커 후보 추출. $NVDA와 대문자 NVDA 둘 다 지원."""
    if not text:
        return []
    cashtag = re.findall(r"(?<![A-Z0-9])\$([A-Z]{1,5})(?![A-Z0-9])", text.upper())
    plain = re.findall(r"\b[A-Z]{2,5}\b", text.upper())
    out = []
    for sym in cashtag + plain:
        if sym in COMMON_WORDS:
            continue
        # 일반 대문자 단어 오탐을 줄이기 위해 $티커 또는 관심 힌트/짧은 심볼 중심으로 허용
        if sym in cashtag or sym in WATCH_HINTS or len(sym) <= 4:
            out.append(sym)
    return out


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1. Reddit 수집
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fetch_reddit_posts_praw(subreddits: list, limit: int = 100) -> list:
    """praw가 설정돼 있으면 Reddit API로 최신글 수집."""
    cid = os.getenv("REDDIT_CLIENT_ID", "")
    secret = os.getenv("REDDIT_CLIENT_SECRET", "")
    if not cid or not secret:
        return []
    try:
        import praw
        reddit = praw.Reddit(client_id=cid, client_secret=secret,
                             user_agent=REDDIT_USER_AGENT)
        posts = []
        cutoff = time.time() - 3600
        for sub in subreddits:
            for p in reddit.subreddit(sub).new(limit=limit):
                created = float(getattr(p, "created_utc", 0) or 0)
                if created < cutoff:
                    continue
                posts.append({
                    "subreddit": sub,
                    "title": getattr(p, "title", "") or "",
                    "body": getattr(p, "selftext", "") or "",
                    "created_utc": created,
                    "score": int(getattr(p, "score", 0) or 0),
                    "url": f"https://reddit.com{getattr(p, 'permalink', '')}",
                })
        return posts
    except Exception as e:
        _log(f"praw 수집 실패, 공개 JSON으로 폴백: {e}")
        return []


def _fetch_reddit_posts_json(subreddits: list, limit: int = 100) -> list:
    """API 키 없이 Reddit 공개 JSON endpoint에서 최신글 수집."""
    cutoff = time.time() - 3600
    headers = {"User-Agent": REDDIT_USER_AGENT}
    posts = []
    for sub in subreddits:
        url = f"https://www.reddit.com/r/{sub}/new.json"
        try:
            resp = net.get(url, headers=headers, params={"limit": limit}, timeout=(5, 15))
            if resp.status_code != 200:
                continue
            children = resp.json().get("data", {}).get("children", [])
            for child in children:
                d = child.get("data", {})
                created = float(d.get("created_utc", 0) or 0)
                if created < cutoff:
                    continue
                posts.append({
                    "subreddit": sub,
                    "title": d.get("title", "") or "",
                    "body": d.get("selftext", "") or "",
                    "created_utc": created,
                    "score": int(d.get("score", 0) or 0),
                    "url": "https://reddit.com" + (d.get("permalink", "") or ""),
                })
        except Exception as e:
            _log(f"Reddit 공개 JSON 수집 실패({sub}): {e}")
    return posts


def fetch_social_posts(subreddits: Optional[list] = None, limit: int = 100) -> list:
    subreddits = subreddits or SOCIAL_SUBREDDITS
    posts = _fetch_reddit_posts_praw(subreddits, limit=limit)
    if not posts:
        posts = _fetch_reddit_posts_json(subreddits, limit=limit)
    return posts


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2. 거래량 / Gemini 감성 분석
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _check_volume_spike(ticker: str) -> dict:
    """최근 거래량이 20일 평균 대비 얼마나 큰지 계산."""
    if not YF_AVAILABLE:
        return {"volume_ratio": 1.0, "current_volume": 0, "avg_volume": 0, "volume_spike": False}
    try:
        df = yf.Ticker(ticker).history(period="3mo", auto_adjust=True)
        if df is None or df.empty or len(df) < 25:
            return {"volume_ratio": 1.0, "current_volume": 0, "avg_volume": 0, "volume_spike": False}
        vol = df["Volume"].dropna()
        cur = float(vol.iloc[-1])
        avg = float(vol.iloc[-21:-1].mean())
        ratio = cur / avg if avg > 0 else 1.0
        return {
            "volume_ratio": round(ratio, 2),
            "current_volume": int(cur),
            "avg_volume": int(avg),
            "volume_spike": ratio >= 2.0,
        }
    except Exception:
        return {"volume_ratio": 1.0, "current_volume": 0, "avg_volume": 0, "volume_spike": False}


def _analyze_sentiment_gemini(ticker: str, posts: list) -> dict:
    """Gemini로 폭증 게시글 분위기와 위험도를 분류."""
    fallback = {
        "risk_label": "단순 투기 세력 유입(위험도 상)",
        "risk_level": "상",
        "reason": "단기간 언급량 급증으로 투기성 수급 유입 가능성이 큽니다.",
    }
    if not GEMINI_API_KEY or not posts:
        return fallback
    try:
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(
            model_name=SOCIAL_MODEL,
            system_instruction=(
                "당신은 소셜 주식 커뮤니티 리스크 분석가입니다. "
                "게시글 분위기를 분석해 JSON만 반환하세요. "
                "risk_label은 '단순 투기 세력 유입(위험도 상)', '숏스퀴즈/숏커버링 징후(위험도 중)', "
                "'건전한 실적/뉴스 기반 관심(위험도 하)' 중 하나로 고르세요."
            ),
        )
        sample = [{"title": p["title"][:180], "body": p["body"][:260], "score": p.get("score", 0)}
                  for p in posts[:12]]
        resp = model.generate_content(
            f"티커 {ticker} 관련 Reddit 게시글 샘플입니다. JSON으로 risk_label, risk_level, reason을 반환하세요.\n"
            f"{json.dumps(sample, ensure_ascii=False)}",
            generation_config=genai.types.GenerationConfig(
                temperature=0.2, response_mime_type="application/json"),
        )
        parsed = json.loads((resp.text or "").strip().strip("`"))
        return {
            "risk_label": str(parsed.get("risk_label", fallback["risk_label"])),
            "risk_level": str(parsed.get("risk_level", fallback["risk_level"])),
            "reason": str(parsed.get("reason", fallback["reason"]))[:180],
        }
    except Exception as e:
        _log(f"Gemini 소셜 감성 분석 실패: {e}")
        return fallback


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  3. 소셜 트렌드 분석
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def analyze_social_trends(subreddits: Optional[list] = None, spike_threshold: float = SOCIAL_SPIKE_THRESHOLD,
                          update_baseline: bool = True) -> dict:
    """
    최근 1시간 Reddit 게시글에서 티커 언급 빈도와 기준선 대비 폭증률을 분석한다.
    """
    posts = fetch_social_posts(subreddits)
    ticker_counts = Counter()
    ticker_posts = defaultdict(list)
    for p in posts:
        text = f"{p.get('title','')} {p.get('body','')}"
        seen = set(_extract_tickers(text))
        for ticker in seen:
            ticker_counts[ticker] += 1
            ticker_posts[ticker].append(p)

    baseline = _load_baseline()
    spikes = []
    now_iso = datetime.now().isoformat()

    for ticker, count in ticker_counts.most_common(30):
        base = float(baseline.get(ticker, {}).get("avg_hourly_mentions", 0.2))
        ratio = count / max(base, 0.2)
        if ratio < spike_threshold or count < 3:
            continue
        vol = _check_volume_spike(ticker)
        sentiment = _analyze_sentiment_gemini(ticker, ticker_posts[ticker])
        meme_score = min(100, int(ratio * 10 + (25 if vol["volume_spike"] else 0)))
        spikes.append({
            "ticker": ticker,
            "mentions_1h": count,
            "baseline_mentions": round(base, 2),
            "spike_ratio": round(ratio, 2),
            "volume_ratio": vol["volume_ratio"],
            "volume_spike": vol["volume_spike"],
            "risk_label": sentiment["risk_label"],
            "risk_level": sentiment["risk_level"],
            "reason": sentiment["reason"],
            "meme_score": meme_score,
            "posts": ticker_posts[ticker][:8],
        })

    if update_baseline:
        # EWMA 기준선 업데이트. 폭증 시에도 천천히만 반영해 다음 루프의 민감도 유지.
        for ticker, count in ticker_counts.items():
            old = float(baseline.get(ticker, {}).get("avg_hourly_mentions", count))
            new = old * 0.85 + count * 0.15
            baseline[ticker] = {"avg_hourly_mentions": round(new, 3), "updated_at": now_iso}
        _save_baseline(baseline)

    rows = [[s["ticker"], str(s["mentions_1h"]), f"{s['spike_ratio']:.1f}x",
             f"{s['volume_ratio']:.1f}x", s["risk_level"]]
            for s in spikes[:8]]
    overlay_table = {
        "type": "table",
        "title": "⚠ MEME STOCK SOCIAL SPIKE",
        "headers": ["티커", "언급", "증가", "거래량", "위험"],
        "rows": rows,
        "alert_text": "⚠ 밈 주식 급증 경보",
    }
    top = spikes[0] if spikes else None
    message = ""
    if top:
        message = (
            f"선생님, 현재 레딧 커뮤니티에서 {top['ticker']} 종목의 언급량이 "
            f"{top['spike_ratio']:.0f}배 이상 폭증했습니다. AI 분석 결과 "
            f"{top['risk_label']}으로 판단되오니 주의하십시오."
        )
    return {
        "success": True,
        "post_count": len(posts),
        "ticker_counts": dict(ticker_counts),
        "spikes": spikes,
        "overlay_table": overlay_table if spikes else None,
        "message": message,
        "briefing": message or "현재 소셜 커뮤니티에서 500% 이상 급증한 밈 주식 신호는 없습니다.",
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  4. 백그라운드 워커
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SocialSpikeDetector:
    def __init__(self, subreddits=None, interval: int = SOCIAL_CHECK_INTERVAL,
                 spike_threshold: float = SOCIAL_SPIKE_THRESHOLD):
        self.subreddits = subreddits or list(SOCIAL_SUBREDDITS)
        self.interval = max(120, int(interval))
        self.spike_threshold = spike_threshold
        self.enabled = bool(self.subreddits)
        self._event_q = deque(maxlen=30)
        self._lock = threading.Lock()
        self._thread = None
        self._running = False
        self._seen = {}
        self._check_count = 0
        # 매트릭스 엔진이 조회하는 최근 스파이크 캐시 (티커 → spike dict + ts)
        self._spike_cache: dict = {}

    def start(self):
        if not self.enabled or self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        _log(f"감시 시작: {', '.join(self.subreddits)} | 간격={self.interval}초")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def get_events(self):
        out = []
        with self._lock:
            while self._event_q:
                out.append(self._event_q.popleft())
        return out

    def requeue(self, events):
        with self._lock:
            for evt in reversed(events):
                self._event_q.appendleft(evt)

    def _loop(self):
        time.sleep(15)
        while self._running:
            self._check_count += 1
            try:
                result = analyze_social_trends(self.subreddits, self.spike_threshold)
                now = time.time()
                for spike in result.get("spikes", []):
                    ticker = spike["ticker"]
                    if now < self._seen.get(ticker, 0):
                        continue
                    self._seen[ticker] = now + 3600
                    # 매트릭스 엔진 조회용 스파이크 캐시 갱신 (posts 제외, 경량 저장)
                    self._spike_cache[ticker] = {
                        k: v for k, v in spike.items() if k != "posts"
                    }
                    self._spike_cache[ticker]["ts"] = now
                    evt = {
                        "source": "social_detector",
                        "type": "social_spike",
                        "ticker": ticker,
                        "message": result["message"],
                        "overlay_table": result["overlay_table"],
                        "alert_text": "⚠ 밈 주식 급증 경보",
                        "spike": spike,
                        "timestamp": datetime.now().isoformat(),
                    }
                    with self._lock:
                        self._event_q.append(evt)
                    _log(f"⚠ {ticker} 언급량 {spike['spike_ratio']:.1f}x, 거래량 {spike['volume_ratio']:.1f}x")
                    break
            except Exception as e:
                _log(f"소셜 감시 오류: {e}")

            slept = 0
            while self._running and slept < self.interval:
                time.sleep(5)
                slept += 5

    def get_ticker_spike_status(self, ticker: str) -> dict | None:
        """
        특정 티커의 최근 1시간 이내 스파이크 캐시 데이터를 반환한다.
        매트릭스 엔진(_compute_social_layer_score)이 라이브 Reddit 수집 없이
        빠르게 조회할 수 있도록 제공.
        """
        t_sym = ticker.upper().split(".")[0]
        cached = self._spike_cache.get(t_sym)
        if cached and time.time() - float(cached.get("ts", 0)) < 3600:
            return cached
        return None

    @property
    def status(self):
        return {
            "running": self._running,
            "subreddits": self.subreddits,
            "checks": self._check_count,
            "pending": len(self._event_q),
            "interval": f"{self.interval}초",
            "spike_threshold": self.spike_threshold,
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  5. LLM 도구
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SOCIAL_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "analyze_social_trends",
            "description": "Reddit 주식 커뮤니티에서 최근 1시간 티커 언급량 급증(밈 주식 신호)과 거래량 스파이크, Gemini 위험도 분석을 수행합니다.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_social_detector_status",
            "description": "소셜 밈 주식 감지 백그라운드 워커 상태를 조회합니다.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

_detector_instance = None


def set_social_detector_instance(detector):
    global _detector_instance
    _detector_instance = detector


def execute_social_tool(tool_name, arguments):
    if tool_name == "get_social_detector_status":
        if not _detector_instance:
            return {"success": False, "message": "소셜 감지기 없음"}
        return {"success": True, **_detector_instance.status}
    if tool_name == "analyze_social_trends":
        result = analyze_social_trends()
        return {
            "success": True,
            "briefing": result["briefing"],
            "spikes": [{k: v for k, v in s.items() if k != "posts"} for s in result.get("spikes", [])],
            "overlay_table": result.get("overlay_table"),
            "post_count": result.get("post_count", 0),
        }
    return {"success": False, "message": f"알 수 없는 도구: {tool_name}"}

