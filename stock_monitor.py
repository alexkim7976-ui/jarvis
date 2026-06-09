"""
J.A.R.V.I.S — 동적 주가 감시 모듈
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
사용자가 음성으로 "엔비디아 5% 이상 폭락하면 알려줘" 같이 명령하면
LLM이 add_stock_alert() 도구를 호출해 규칙을 등록한다.
백그라운드 스레드가 30초마다 yfinance로 현재가를 조회하고, 조건 충족 시
이벤트 큐에 긴급 알림을 적재한다.

지원 조건:
  change_pct_down  — 전일 대비 하락률 X% 이상 (예: -5%)
  change_pct_up    — 전일 대비 상승률 X% 이상 (예: +3%)
  price_below      — 현재가가 목표가 이하 (예: $800 아래)
  price_above      — 현재가가 목표가 이상 (예: $1,000 위)

알림 중복 방지:
  - 조건 충족 시 triggered=True 설정 → 같은 조건으로 재알림 없음
  - 조건 해소(+ 0.5% 완충) 시 triggered 자동 리셋
  - cooldown_sec 경과 전 재알림 차단 (기본 1시간)

의존성: pip install yfinance
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import time
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False

from stock_quant import _resolve_ticker

# ──────────────────────────────────────────────
#  설정
# ──────────────────────────────────────────────
RULES_FILE    = Path("stock_monitor_rules.json")
CHECK_INTERVAL = int(os.getenv("STOCK_MONITOR_INTERVAL", "30"))   # 초 (기본 30초)
DEFAULT_COOLDOWN = int(os.getenv("STOCK_MONITOR_COOLDOWN", "3600")) # 초 (기본 1시간)
RESET_BUFFER_PCT = 0.5   # 조건 해소 판정 완충 (%)

CONDITION_LABELS = {
    "change_pct_down": "하락률",
    "change_pct_up":   "상승률",
    "price_below":     "가격 하한",
    "price_above":     "가격 상한",
}

_KR_NAME_MAP = {
    "005930.KS": "삼성전자", "000660.KS": "SK하이닉스", "035420.KS": "네이버",
    "035720.KS": "카카오",  "005380.KS": "현대차",    "000270.KS": "기아",
    "039490.KS": "키움증권", "079370.KQ": "제우스",
    "NVDA": "엔비디아",  "AAPL": "애플",  "TSLA": "테슬라",
    "MSFT": "마이크로소프트", "AMZN": "아마존", "GOOGL": "구글",
    "META": "메타", "NFLX": "넷플릭스", "AMD": "AMD",
}


def _log(level, msg):
    ts = datetime.now().strftime("%H:%M:%S")
    icons = {"INFO": "◈", "OK": "✓", "WARN": "⚠", "ALERT": "🚨"}
    print(f"[{ts}] {icons.get(level,'·')} [주가감시] {msg}")


def _display_name(ticker: str) -> str:
    return _KR_NAME_MAP.get(ticker.upper(), ticker)


# ──────────────────────────────────────────────
#  AlertRule 데이터 클래스
# ──────────────────────────────────────────────
@dataclass
class AlertRule:
    id: str
    ticker: str          # yfinance 해석 가능 티커 (resolve 완료)
    name: str            # 표시용 이름 (삼성전자, 엔비디아 등)
    condition_type: str  # change_pct_down | change_pct_up | price_below | price_above
    threshold: float     # 조건 임계값 (양수로 저장; 방향은 condition_type이 결정)
    cooldown_sec: int    # 재알림 쿨다운 (초)
    triggered: bool = False          # 현재 조건 충족 상태
    last_alert_at: float = 0.0       # 마지막 알림 epoch time

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AlertRule":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @property
    def description(self) -> str:
        cond = CONDITION_LABELS.get(self.condition_type, self.condition_type)
        if self.condition_type in ("change_pct_down", "change_pct_up"):
            sign = "-" if self.condition_type == "change_pct_down" else "+"
            return f"{self.name} {sign}{self.threshold:.1f}% {cond}"
        else:
            is_krw = self.ticker.endswith(".KS") or self.ticker.endswith(".KQ")
            price_s = f"{self.threshold:,.0f}원" if is_krw else f"${self.threshold:,.2f}"
            return f"{self.name} {price_s} {cond}"


# ──────────────────────────────────────────────
#  가격 조회 (변화율 + 현재가)
# ──────────────────────────────────────────────
def _fetch_price_change(ticker: str) -> tuple[float, float] | None:
    """(change_pct, current_price) 반환. 실패 시 None."""
    if not YF_AVAILABLE:
        return None
    try:
        df = yf.Ticker(ticker).history(period="2d", auto_adjust=True)
        if df is None or len(df) < 2:
            return None
        prev_close = float(df["Close"].iloc[-2])
        current    = float(df["Close"].iloc[-1])
        if prev_close <= 0:
            return None
        pct = (current - prev_close) / prev_close * 100
        return round(pct, 3), round(current, 4)
    except Exception as e:
        _log("WARN", f"가격 조회 실패 ({ticker}): {e}")
        return None


# ──────────────────────────────────────────────
#  뉴스 컨텍스트 수집 헬퍼 (백그라운드 스레드 전용)
# ──────────────────────────────────────────────
def _ko_subject_particle(name: str) -> str:
    """한국어 이름 뒤에 붙는 주어 조사 '이'/'가' 반환."""
    if not name:
        return "가"
    last = name[-1]
    code = ord(last)
    if 0xAC00 <= code <= 0xD7A3:          # 완성형 한글 범위
        return "이" if (code - 0xAC00) % 28 != 0 else "가"
    return "가"                            # 영어·숫자 종료 → '가'


def _fetch_news_context(ticker: str, name: str,
                        change_pct: float, price: float,
                        is_krw: bool,
                        timeout: int = 8) -> dict | None:
    """
    news_scraper.get_ticker_news_summary() 를 백그라운드 스레드에서 호출.
    concurrent.futures 로 hard timeout 보장 (기본 8초).
    실패·타임아웃 시 None 반환 → 원본 메시지 그대로 사용.
    """
    try:
        from news_scraper import get_ticker_news_summary
        price_s = f"{price:,.0f}원" if is_krw else f"${price:,.2f}"
        change_info = f"전일 대비 {change_pct:+.1f}% 변동, 현재가 {price_s}"
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(get_ticker_news_summary, ticker, name, 3, change_info)
            return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        _log("WARN", f"뉴스 컨텍스트 타임아웃 {timeout}초 ({ticker})")
    except ImportError:
        _log("INFO", "news_scraper.py 없음 — 뉴스 컨텍스트 스킵")
    except Exception as e:
        _log("WARN", f"뉴스 컨텍스트 실패 ({ticker}): {e}")
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  StockMonitor 클래스
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class StockMonitor:
    """
    동적 주가 감시 데몬.

    사용법 (jarvis_v2):
        monitor = StockMonitor()
        monitor.start()
        # LLM이 add_stock_alert()를 호출하면 규칙 자동 등록
        # 메인 루프에서:
        for evt in monitor.get_events():
            ... TTS 인터럽트 + HUD 알림 ...
    """

    def __init__(self, interval: int = CHECK_INTERVAL):
        self.interval   = max(15, interval)
        self._rules: list[AlertRule] = []
        self._lock      = threading.Lock()
        self._event_q: deque[dict] = deque(maxlen=50)
        self._thread    = None
        self._running   = False
        self._check_count = 0
        self._total_alerts = 0
        self._load_rules()

    # ── 라이프사이클 ──
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        _log("OK", f"감시 시작 — 간격={self.interval}초 | 규칙={len(self._rules)}개")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    # ── 이벤트 큐 인터페이스 (price_alert 호환) ──
    def get_events(self) -> list[dict]:
        events = []
        with self._lock:
            while self._event_q:
                events.append(self._event_q.popleft())
        return events

    def requeue(self, events: list[dict]):
        with self._lock:
            for evt in reversed(events):
                self._event_q.appendleft(evt)

    # ── 규칙 관리 ──
    def add_rule(self, ticker_raw: str, condition_type: str,
                 threshold: float, cooldown_sec: int = DEFAULT_COOLDOWN) -> dict:
        if condition_type not in CONDITION_LABELS:
            return {"success": False,
                    "message": f"알 수 없는 조건: {condition_type}. "
                               f"사용 가능: {', '.join(CONDITION_LABELS)}"}
        if threshold <= 0:
            return {"success": False, "message": "threshold는 양수여야 합니다."}

        ticker = _resolve_ticker(ticker_raw)
        name   = _display_name(ticker)
        rule   = AlertRule(
            id=str(uuid.uuid4())[:8],
            ticker=ticker,
            name=name,
            condition_type=condition_type,
            threshold=abs(threshold),
            cooldown_sec=cooldown_sec,
        )
        with self._lock:
            self._rules.append(rule)
        self._save_rules()
        _log("OK", f"규칙 추가: {rule.description} (id={rule.id})")
        return {
            "success": True,
            "id": rule.id,
            "message": f"알림 등록: {rule.description}",
            "description": rule.description,
        }

    def remove_rule(self, alert_id: str) -> dict:
        with self._lock:
            before = len(self._rules)
            self._rules = [r for r in self._rules if r.id != alert_id]
            removed = before - len(self._rules)
        if removed:
            self._save_rules()
            _log("OK", f"규칙 삭제: id={alert_id}")
            return {"success": True, "message": f"알림 id={alert_id} 삭제 완료"}
        return {"success": False, "message": f"id={alert_id} 규칙을 찾을 수 없습니다."}

    def list_rules(self) -> dict:
        with self._lock:
            rules = list(self._rules)
        if not rules:
            return {"success": True, "count": 0, "rules": [],
                    "message": "등록된 알림 규칙이 없습니다."}
        rows = []
        now = time.time()
        for r in rules:
            cooldown_left = max(0, r.last_alert_at + r.cooldown_sec - now)
            status = "쿨다운" if cooldown_left > 0 else ("발동됨" if r.triggered else "감시중")
            rows.append({
                "id": r.id,
                "description": r.description,
                "status": status,
                "cooldown_left": int(cooldown_left),
            })
        return {
            "success": True,
            "count": len(rows),
            "rules": rows,
            "message": f"{len(rows)}개의 알림 규칙이 등록되어 있습니다.",
            "overlay_table": {
                "type": "table",
                "title": "📋 주가 알림 규칙",
                "headers": ["ID", "조건", "상태"],
                "rows": [[r["id"], r["description"], r["status"]] for r in rows],
            },
        }

    # ── 감시 루프 ──
    def _monitor_loop(self):
        time.sleep(5)  # 부팅 직후 부하 분산
        while self._running:
            self._check_count += 1
            with self._lock:
                rules_copy = list(self._rules)

            for rule in rules_copy:
                if not self._running:
                    break
                try:
                    self._check_rule(rule)
                except Exception as e:
                    _log("WARN", f"규칙 점검 오류 ({rule.ticker}): {e}")
                # 티커 간 짧은 텀
                for _ in range(5):
                    if not self._running:
                        break
                    time.sleep(0.1)

            # 다음 사이클 대기 (1초 단위 → 즉시 종료 반응)
            slept = 0
            while self._running and slept < self.interval:
                time.sleep(1)
                slept += 1

    def _check_rule(self, rule: AlertRule):
        result = _fetch_price_change(rule.ticker)
        if result is None:
            return
        change_pct, price = result
        now = time.time()

        triggered = self._evaluate(rule, change_pct, price)

        # 조건 해소 → triggered 리셋 (완충 포함)
        if rule.triggered and not triggered:
            buffer_ok = self._evaluate_with_buffer(rule, change_pct, price)
            if not buffer_ok:
                rule.triggered = False
                _log("INFO", f"조건 해소 — {rule.description} | 변화율={change_pct:+.2f}%, 현재가={price}")
            return

        if not triggered:
            return

        # 이미 발동 중이거나 쿨다운이면 스킵
        if rule.triggered:
            return
        if now < rule.last_alert_at + rule.cooldown_sec:
            return

        # ── 알림 발화 ──
        rule.triggered = True
        rule.last_alert_at = now
        self._total_alerts += 1
        self._save_rules()

        is_krw = rule.ticker.endswith(".KS") or rule.ticker.endswith(".KQ")
        price_s = f"{price:,.0f}원" if is_krw else f"${price:,.2f}"
        cond_label = CONDITION_LABELS[rule.condition_type]

        # ── 1단계: 기본 TTS 메시지 & overlay 구성 ──
        ptc = _ko_subject_particle(rule.name)   # '이' or '가'
        if rule.condition_type == "change_pct_down":
            base_msg = (f"선생님, {rule.name}{ptc} 현재 전일 대비 "
                        f"{abs(change_pct):.1f}% 급락 중입니다. 현재가 {price_s}입니다.")
        elif rule.condition_type == "change_pct_up":
            base_msg = (f"선생님, {rule.name}{ptc} 현재 전일 대비 "
                        f"{change_pct:.1f}% 급등 중입니다. 현재가 {price_s}입니다.")
        elif rule.condition_type == "price_below":
            base_msg = (f"선생님, {rule.name} 주가가 {price_s}까지 하락하여 "
                        f"설정하신 목표가 이하에 진입했습니다.")
        else:  # price_above
            base_msg = (f"선생님, {rule.name} 주가가 {price_s}까지 상승하여 "
                        f"설정하신 목표가를 돌파했습니다.")

        alert_text = f"🚨 {rule.name} {cond_label} 알림"
        overlay_table = {
            "type":    "table",
            "title":   alert_text,
            "headers": ["항목", "값"],
            "rows": [
                ["종목",   rule.name],
                ["현재가", price_s],
                ["변화율", f"{change_pct:+.2f}%"],
                ["조건",   rule.description],
            ],
            "alert_text": alert_text,
        }

        # ── 2단계: 뉴스 컨텍스트 수집 + Gemini 원인 요약 (백그라운드 스레드 내 실행) ──
        # 메인 루프(OpenCV/마이크)는 절대 블로킹하지 않음 — 완료 후 큐에 push
        headlines: list[dict] = []
        news_ctx = _fetch_news_context(rule.ticker, rule.name,
                                        change_pct, price, is_krw)
        if news_ctx and news_ctx.get("success"):
            headlines = news_ctx.get("headlines", [])
            summary   = news_ctx.get("summary", "")
            from news_scraper import FALLBACK_SUMMARY
            has_real_summary = bool(summary) and summary != FALLBACK_SUMMARY

            if has_real_summary:
                # 자비스 원인 분석형 대사로 업그레이드
                if rule.condition_type == "change_pct_down":
                    base_msg = (
                        f"선생님, {rule.name}{ptc} 현재 전일 대비 "
                        f"{abs(change_pct):.1f}% 급락 중입니다. "
                        f"AI 분석 결과, {summary} 관련 뉴스가 주요 원인으로 포착되었습니다. "
                        f"현재가 {price_s}입니다."
                    )
                elif rule.condition_type == "change_pct_up":
                    base_msg = (
                        f"선생님, {rule.name}{ptc} 현재 전일 대비 "
                        f"{change_pct:.1f}% 급등 중입니다. "
                        f"AI 분석 결과, {summary} 관련 뉴스가 주요 배경으로 포착되었습니다. "
                        f"현재가 {price_s}입니다."
                    )
                else:
                    base_msg = f"{base_msg} AI 분석: {summary}"

                # HUD overlay_table 에 AI 원인 요약 행 추가
                overlay_table["rows"].append(["AI 원인 요약", summary[:45], "🧠"])
                _log("OK", f"뉴스 컨텍스트 합성 완료 ({rule.ticker}) → {summary[:40]}")
            else:
                _log("INFO", f"뉴스 요약 없음 — 기본 메시지 사용 ({rule.ticker})")
        else:
            _log("INFO", f"뉴스 컨텍스트 미수집 — 기본 메시지 사용 ({rule.ticker})")

        message = base_msg

        # ── 3단계: 완성된 이벤트 패키지를 큐에 push ──
        evt = {
            "source":        "stock_monitor",
            "rule_id":       rule.id,
            "ticker":        rule.ticker,
            "name":          rule.name,
            "condition":     rule.condition_type,
            "change_pct":    change_pct,
            "price":         price,
            "message":       message,
            "alert_text":    alert_text,
            "overlay_table": overlay_table,
            "headlines":     headlines,          # 뉴스 헤드라인 (chat_log 표시용)
            "news_summary":  news_ctx.get("summary", "") if news_ctx else "",
            "timestamp":     datetime.now().isoformat(),
        }
        with self._lock:
            self._event_q.append(evt)
        _log("ALERT", f"{rule.name}: {cond_label} 발동 | 변화율={change_pct:+.2f}%, 현재가={price_s}")

    # ── 조건 평가 ──
    def _evaluate(self, rule: AlertRule, change_pct: float, price: float) -> bool:
        if rule.condition_type == "change_pct_down":
            return change_pct <= -rule.threshold
        if rule.condition_type == "change_pct_up":
            return change_pct >= rule.threshold
        if rule.condition_type == "price_below":
            return price <= rule.threshold
        if rule.condition_type == "price_above":
            return price >= rule.threshold
        return False

    def _evaluate_with_buffer(self, rule: AlertRule, change_pct: float, price: float) -> bool:
        """완충 구간 포함 평가 — 조건에서 막 벗어난 경우는 아직 해소로 보지 않는다."""
        buf = RESET_BUFFER_PCT
        if rule.condition_type == "change_pct_down":
            return change_pct <= -(rule.threshold - buf)
        if rule.condition_type == "change_pct_up":
            return change_pct >= (rule.threshold - buf)
        if rule.condition_type == "price_below":
            return price <= rule.threshold * (1 + buf / 100)
        if rule.condition_type == "price_above":
            return price >= rule.threshold * (1 - buf / 100)
        return False

    # ── JSON 영속성 ──
    def _save_rules(self):
        try:
            with self._lock:
                data = [r.to_dict() for r in self._rules]
            RULES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            _log("WARN", f"규칙 저장 실패: {e}")

    def _load_rules(self):
        if not RULES_FILE.exists():
            return
        try:
            data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
            self._rules = [AlertRule.from_dict(d) for d in data]
            # 재시작 시 triggered 상태 리셋 (가격 재평가)
            for r in self._rules:
                r.triggered = False
            _log("OK", f"규칙 로드: {len(self._rules)}개")
        except Exception as e:
            _log("WARN", f"규칙 로드 실패: {e}")

    @property
    def status(self) -> dict:
        with self._lock:
            rules = list(self._rules)
        now = time.time()
        active = [r for r in rules if r.triggered]
        cooling = [r for r in rules if not r.triggered and now < r.last_alert_at + r.cooldown_sec]
        return {
            "running":       self._running,
            "interval":      f"{self.interval}초",
            "checks":        self._check_count,
            "total_alerts":  self._total_alerts,
            "rules_count":   len(rules),
            "active_count":  len(active),
            "cooling_count": len(cooling),
            "rules":         [r.description for r in rules],
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LLM 도구 정의 (Gemini Function Calling)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STOCK_MONITOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_stock_alert",
            "description": (
                "주가 알림 규칙을 등록합니다. 사용자가 '엔비디아 5% 이상 폭락하면 알려줘', "
                "'삼성전자 5만원 이하로 떨어지면 알림', '테슬라 10% 급등하면 알려줘' 같이 "
                "말하면 이 도구를 호출하세요. condition_type: "
                "change_pct_down(하락률), change_pct_up(상승률), "
                "price_below(가격 하한), price_above(가격 상한)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "종목 티커 또는 한국어 이름 (예: NVDA, 엔비디아, 005930.KS, 삼성전자)",
                    },
                    "condition_type": {
                        "type": "string",
                        "enum": ["change_pct_down", "change_pct_up", "price_below", "price_above"],
                        "description": "알림 조건 유형",
                    },
                    "threshold": {
                        "type": "number",
                        "description": "조건 임계값. 양수로 입력. (하락률 5% → 5, 목표가 50000원 → 50000)",
                    },
                    "cooldown_sec": {
                        "type": "integer",
                        "description": "재알림 쿨다운(초). 기본값 3600(1시간). 생략 가능.",
                    },
                },
                "required": ["ticker", "condition_type", "threshold"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_stock_alert",
            "description": "등록된 주가 알림 규칙을 ID로 삭제합니다. list_stock_alerts로 ID를 먼저 확인하세요.",
            "parameters": {
                "type": "object",
                "properties": {
                    "alert_id": {
                        "type": "string",
                        "description": "삭제할 알림 규칙 ID (list_stock_alerts 응답에서 확인)",
                    },
                },
                "required": ["alert_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_stock_alerts",
            "description": "현재 등록된 주가 알림 규칙 목록과 상태(감시중/발동됨/쿨다운)를 조회합니다.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_stock_monitor_status",
            "description": "주가 감시 데몬의 전체 상태(실행 여부, 점검 횟수, 누적 알림 수)를 조회합니다.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

_monitor_instance: StockMonitor | None = None


def set_stock_monitor_instance(inst: StockMonitor):
    global _monitor_instance
    _monitor_instance = inst


def execute_stock_monitor_tool(tool_name: str, arguments: dict) -> dict:
    global _monitor_instance
    if not _monitor_instance:
        return {"success": False, "message": "주가 감시 모듈 없음"}

    if tool_name == "add_stock_alert":
        return _monitor_instance.add_rule(
            ticker_raw     = arguments.get("ticker", ""),
            condition_type = arguments.get("condition_type", ""),
            threshold      = float(arguments.get("threshold", 0)),
            cooldown_sec   = int(arguments.get("cooldown_sec", DEFAULT_COOLDOWN)),
        )

    if tool_name == "remove_stock_alert":
        return _monitor_instance.remove_rule(arguments.get("alert_id", ""))

    if tool_name == "list_stock_alerts":
        return _monitor_instance.list_rules()

    if tool_name == "get_stock_monitor_status":
        return {"success": True, **_monitor_instance.status}

    return {"success": False, "message": f"알 수 없는 도구: {tool_name}"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  단독 실행 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    m = StockMonitor(interval=15)
    # 테스트 규칙 등록
    print(m.add_rule("NVDA",     "change_pct_down", 1.0))   # 1% 하락
    print(m.add_rule("엔비디아", "change_pct_up",   1.0))   # 1% 상승 (중복 테스트)
    print(m.add_rule("AAPL",     "price_below",     200.0)) # $200 아래
    m.start()
    print("감시 시작 (30초 대기)...")
    time.sleep(35)
    evts = m.get_events()
    print(f"이벤트 {len(evts)}개:")
    for e in evts:
        print(f"  [{e['ticker']}] {e['message']}")
    m.stop()
