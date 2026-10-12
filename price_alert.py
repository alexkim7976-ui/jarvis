"""
J.A.R.V.I.S — 실시간 지정가/지표 알림 워커
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
메인 UI/웹캠/음성 루프와 완전히 분리된 데몬 스레드로 관심 종목을
주기적으로(기본 60초) 감시한다. RSI 과매도 또는 볼린저 밴드 하단
하향 돌파 같은 강력한 매수 시그널이 포착되면 이벤트를 큐에 적재하고,
메인 루프가 IDLE 일 때 HUD 적색 펄스 배너 + TTS 인터럽트로 먼저 말을 건다.

stock_quant.py 의 지표 연산(compute_technical_snapshot)을 재사용한다.

.env 설정:
  WATCH_LIST=AAPL,NVDA,005930.KS      (감시 종목, 쉼표 구분)
  PRICE_ALERT_INTERVAL=60             (초, 기본 1분)
  PRICE_ALERT_COOLDOWN=3600           (초, 종목별 중복 알림 쿨다운, 기본 1시간)
  RSI_OVERSOLD=30                     (RSI 과매도 임계값)

의존성: pip install yfinance pandas numpy
"""
from __future__ import annotations

from speech_style import USER_DISPLAY_NAME

import os
import json
import time
import threading
from collections import deque
from datetime import datetime

try:
    import yfinance as yf
    YF_AVAILABLE = True
except ImportError:
    YF_AVAILABLE = False

# stock_quant 의 지표 스냅샷 + 티커 변환 + 종목명 해석 재사용
from stock_quant import (compute_technical_snapshot, _resolve_ticker,
                         resolve_alert_query, suggest_stock_candidates)

# 지정가(목표가) 알림 영속 저장 파일
_TARGETS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "price_targets.json")


# ──────────────────────────────────────────────
#  설정
# ──────────────────────────────────────────────
def _parse_watch_list(raw: str) -> list[str]:
    """'AAPL, NVDA, 005930.KS  # 주석' 같은 입력을 안전하게 파싱"""
    if not raw:
        return []
    raw = raw.split("#")[0]  # 인라인 주석 제거
    items = []
    for tok in raw.split(","):
        t = tok.strip()
        if not t:
            continue
        # 'KRX: 039490' / 'KOSDAQ: 079370' 형태 보정
        if ":" in t:
            market, _, code = t.partition(":")
            code = code.strip()
            market = market.strip().upper()
            if code.isdigit():
                t = f"{code}.{'KQ' if market == 'KOSDAQ' else 'KS'}"
        items.append(t)
    return items


WATCH_LIST           = _parse_watch_list(os.getenv("WATCH_LIST", os.getenv("NEWS_WATCHLIST", "")))
PRICE_ALERT_INTERVAL = int(os.getenv("PRICE_ALERT_INTERVAL", "60"))     # 초 (기본 1분)
PRICE_ALERT_COOLDOWN = int(os.getenv("PRICE_ALERT_COOLDOWN", "3600"))   # 초 (기본 1시간)
RSI_OVERSOLD         = float(os.getenv("RSI_OVERSOLD", "30"))

_KR_NAME_MAP = {
    "005930.KS": "삼성전자", "000660.KS": "SK하이닉스", "035420.KS": "NAVER",
    "035720.KS": "카카오", "005380.KS": "현대차", "000270.KS": "기아",
    "039490.KS": "키움증권", "079370.KQ": "제우스",
    "NVDA": "엔비디아", "AAPL": "애플", "TSLA": "테슬라", "MSFT": "마이크로소프트",
    "PKX": "포스코","GOOG": "Google",
}


def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] 🔔 [지정가알림] {msg}")


def _display_name(ticker: str) -> str:
    return _KR_NAME_MAP.get(ticker.upper(), ticker)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  데이터 수집 (가벼운 history — 무거운 .info 미사용)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fetch_snapshot(ticker: str) -> dict | None:
    """최신 봉 기준 지표 스냅샷(values/signals) 반환. 실패 시 None."""
    if not YF_AVAILABLE:
        return None
    try:
        df = yf.Ticker(ticker).history(period="3mo", auto_adjust=True)
        if df is None or df.empty or len(df) < 25:
            return None
        return compute_technical_snapshot(df)
    except Exception as e:
        _log(f"스냅샷 수집 실패({ticker}): {e}")
        return None


def _fetch_price(ticker: str) -> float | None:
    """현재가만 가볍게 조회 (목표가 비교용). 실패 시 None."""
    if not YF_AVAILABLE:
        return None
    try:
        t = yf.Ticker(ticker)
        fi = getattr(t, "fast_info", None)
        if fi is not None:
            for k in ("last_price", "lastPrice"):
                try:
                    v = fi.get(k) if hasattr(fi, "get") else fi[k]
                except Exception:
                    v = None
                if v:
                    return float(v)
        df = t.history(period="1d")
        if df is not None and not df.empty:
            return float(df["Close"].iloc[-1])
    except Exception as e:
        _log(f"가격 조회 실패({ticker}): {e}")
    return None


def _fmt_price(ticker: str, price: float) -> str:
    is_krw = ticker.upper().endswith(".KS") or ticker.upper().endswith(".KQ")
    return f"{price:,.0f}원" if is_krw else f"${price:,.2f}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  백그라운드 알림 워커
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PriceAlertWorker:
    """
    관심 종목 실시간 지표 감시 데몬.

    사용법(jarvis_v2):
        worker = PriceAlertWorker()
        worker.start_price_alert_worker()
        # 메인 루프에서:
        for evt in worker.get_events():
            ...HUD 적색 펄스 + TTS 인터럽트...
    """

    def __init__(self, watch_list: list[str] | None = None,
                 interval: int = PRICE_ALERT_INTERVAL,
                 cooldown: int = PRICE_ALERT_COOLDOWN,
                 rsi_oversold: float = RSI_OVERSOLD):
        self.watch_list = watch_list if watch_list is not None else list(WATCH_LIST)
        self.interval = max(15, interval)        # 과도한 호출 방지 하한
        self.cooldown = cooldown
        self.rsi_oversold = rsi_oversold
        # WATCH_LIST가 비어도 사용자가 음성으로 지정한 목표가는 감시해야 하므로 yfinance만 있으면 활성
        self.enabled = YF_AVAILABLE

        self._event_q = deque(maxlen=50)
        self._lock = threading.Lock()
        self._thread = None
        self._running = False
        self._cooldown_until: dict[str, float] = {}   # ticker → 알림 가능 시각(epoch)
        self._check_count = 0
        self._total_alerts = 0

        # 사용자 지정 목표가 알림: [{ticker,name,price,direction,created}]
        self.targets: list[dict] = []
        self._load_targets()

    # ── 라이프사이클 ──
    def start_price_alert_worker(self):
        """데몬 스레드로 상시 감시 루프 구동"""
        if not self.enabled:
            _log("비활성: WATCH_LIST 미설정 또는 yfinance 미설치")
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._alert_loop, daemon=True)
        self._thread.start()
        _log(f"감시 시작: {', '.join(self.watch_list)} | 간격={self.interval}초 "
             f"| 쿨다운={self.cooldown}초 | RSI<{self.rsi_oversold:g}")

    # 다른 워커들과 일관된 인터페이스
    def start(self):
        self.start_price_alert_worker()

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def get_events(self) -> list[dict]:
        """대기 중인 알림 이벤트 모두 반환 (메인 루프 폴링)"""
        events = []
        with self._lock:
            while self._event_q:
                events.append(self._event_q.popleft())
        return events

    def requeue(self, events):
        """처리하지 못한 이벤트를 원래 순서로 다시 큐 앞쪽에 넣는다 (다음 IDLE 사이클 처리용)"""
        with self._lock:
            for evt in reversed(events):
                self._event_q.appendleft(evt)

    # ── 지정가(목표가) 알림 영속 ──
    def _load_targets(self):
        try:
            if os.path.exists(_TARGETS_FILE):
                with open(_TARGETS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self.targets = [t for t in data
                                    if isinstance(t, dict) and t.get("ticker") and t.get("price") is not None]
                    if self.targets:
                        _log(f"저장된 지정가 알림 {len(self.targets)}건 로드")
        except Exception as e:
            _log(f"지정가 알림 로드 실패: {e}")

    def _save_targets(self):
        try:
            with open(_TARGETS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.targets, f, ensure_ascii=False, indent=2)
        except Exception as e:
            _log(f"지정가 알림 저장 실패: {e}")

    def add_target(self, ticker: str, name: str, price, direction: str | None = None) -> dict:
        """목표가 알림 등록(또는 동일 종목·방향 갱신). 방향 미지정 시 현재가 기준 자동 판정."""
        ticker = _resolve_ticker(ticker)
        try:
            price = float(price)
        except (TypeError, ValueError):
            return {"success": False, "message": "목표가를 숫자로 인식하지 못했습니다."}
        cur = _fetch_price(ticker)
        if direction not in ("above", "below"):
            direction = "above" if (cur is None or price >= cur) else "below"

        with self._lock:
            updated = False
            for t in self.targets:
                if t["ticker"] == ticker and t["direction"] == direction:
                    t["price"] = price; t["name"] = name
                    t["created"] = datetime.now().isoformat(); updated = True; break
            if not updated:
                self.targets.append({"ticker": ticker, "name": name, "price": price,
                                     "direction": direction,
                                     "created": datetime.now().isoformat()})
        self._save_targets()
        if not self._running and self.enabled:
            self.start_price_alert_worker()

        arrow = "이상" if direction == "above" else "이하"
        ps = _fmt_price(ticker, price)
        cur_s = f" 현재가는 {_fmt_price(ticker, cur)}입니다." if cur else ""
        _log(f"🎯 목표가 설정: {name}({ticker}) {ps} {arrow}{' (갱신)' if updated else ''}")
        return {"success": True, "ticker": ticker, "name": name, "price": price,
                "direction": direction, "current": cur, "count": len(self.targets),
                "message": f"{name} 목표가 {ps} {arrow} 도달 시 알림을 설정했습니다, {USER_DISPLAY_NAME}.{cur_s}"}

    def cancel_target(self, ticker: str) -> int:
        rt = _resolve_ticker(ticker)
        with self._lock:
            before = len(self.targets)
            self.targets = [t for t in self.targets if t["ticker"] != rt]
            removed = before - len(self.targets)
        if removed:
            self._save_targets()
        return removed

    def list_targets(self) -> list:
        with self._lock:
            return list(self.targets)

    # ── 감시 루프 ──
    def _alert_loop(self):
        time.sleep(8)  # 부팅 직후 부하 분산
        while self._running:
            self._check_count += 1
            for ticker in self.watch_list:
                if not self._running:
                    break
                try:
                    self._check_ticker(ticker)
                except Exception as e:
                    _log(f"감시 오류({ticker}): {e}")
                # 티커 간 짧은 간격 + 중단 신호 즉시 반영 (CPU 점유 최소화)
                for _ in range(10):
                    if not self._running:
                        break
                    time.sleep(0.1)

            # 사용자 지정 목표가 점검
            if self._running:
                try:
                    self._check_targets()
                except Exception as e:
                    _log(f"목표가 점검 오류: {e}")

            # 다음 사이클까지 1초 단위로 쪼개 대기 → 종료 신호 빠른 반영
            slept = 0
            while self._running and slept < self.interval:
                time.sleep(1)
                slept += 1

    def _check_ticker(self, ticker: str):
        resolved = _resolve_ticker(ticker)

        # 쿨다운 중이면 스킵
        now = time.time()
        if now < self._cooldown_until.get(resolved, 0):
            return

        snap = _fetch_snapshot(resolved)
        if not snap:
            return
        vals = snap["values"]
        rsi = vals.get("rsi", 50)
        price = vals.get("price", 0)
        bb_lower = vals.get("bb_lower", 0)

        triggers = []   # (라벨, 설명)
        # ── 트리거 1: RSI 과매도 ──
        if rsi and rsi < self.rsi_oversold:
            triggers.append(("RSI 과매도", f"RSI {rsi:.0f} (<{self.rsi_oversold:g})"))
        # ── 트리거 2: 볼린저 밴드 하단 하향 돌파 ──
        if bb_lower and price and price <= bb_lower:
            triggers.append(("볼린저 하단 돌파", f"현재가 {price:,.2f} ≤ BB_Low {bb_lower:,.2f}"))

        if not triggers:
            return

        # 쿨다운 설정 (1시간)
        self._cooldown_until[resolved] = now + self.cooldown
        self._total_alerts += 1

        name = _display_name(resolved)
        reasons = [t[1] for t in triggers]
        labels = ", ".join(t[0] for t in triggers)

        message = (f"{USER_DISPLAY_NAME}, {name} 종목이 과매도 구간에 진입하여 "
                   f"리스크 가이드라인 기준 진입 적기입니다. {labels} 신호입니다.")

        overlay_table = self._build_overlay_table(name, resolved, vals, triggers)
        alert_text = f"🚨 {name} 매수 시그널 — {labels}"

        evt = {
            "source": "price_alert",
            "ticker": resolved,
            "name": name,
            "triggers": labels,
            "reasons": reasons,
            "rsi": rsi,
            "price": price,
            "message": message,
            "overlay_table": overlay_table,
            "alert_text": alert_text,
            "timestamp": datetime.now().isoformat(),
        }
        with self._lock:
            self._event_q.append(evt)
        _log(f"🚨 {name}: {labels} (RSI {rsi:.0f}, 현재가 {price:,.2f})")

    # ── 사용자 지정 목표가 점검 ──
    def _check_targets(self):
        if not self.targets:
            return
        hit = []
        for t in list(self.targets):
            if not self._running:
                break
            price = _fetch_price(t["ticker"])
            if price is None:
                continue
            crossed = ((t["direction"] == "above" and price >= t["price"]) or
                       (t["direction"] == "below" and price <= t["price"]))
            if crossed:
                self._emit_target_event(t, price)
                hit.append((t["ticker"], t["direction"]))
        if hit:
            # 발동한 목표가는 1회성 — 제거 후 저장
            with self._lock:
                self.targets = [t for t in self.targets
                                if (t["ticker"], t["direction"]) not in hit]
            self._save_targets()

    def _emit_target_event(self, t: dict, price: float):
        name = t["name"]; ticker = t["ticker"]
        arrow = "이상" if t["direction"] == "above" else "이하"
        ps = _fmt_price(ticker, price); tgt = _fmt_price(ticker, t["price"])
        labels = f"목표가 {arrow} 도달"
        message = (f"{USER_DISPLAY_NAME}, {name} 종목이 목표가 {tgt} {arrow}에 도달했습니다. "
                   f"현재가는 {ps}입니다.")
        overlay_table = {
            "type": "table", "title": f"🎯 {name} 목표가 도달",
            "headers": ["항목", "값", "신호"],
            "rows": [["현재가", ps, "+"], ["목표가", tgt, "★"], ["조건", f"{arrow} 도달", "+"]],
            "alert_text": f"🎯 {name} 목표가 {arrow} 도달",
        }
        evt = {
            "source": "price_target", "ticker": ticker, "name": name,
            "triggers": labels, "reasons": [f"목표가 {tgt} {arrow}"],
            "price": price, "message": message, "overlay_table": overlay_table,
            "alert_text": f"🎯 {name} 목표가 도달 — {ps}",
            "timestamp": datetime.now().isoformat(),
        }
        with self._lock:
            self._event_q.append(evt)
            self._total_alerts += 1
        _log(f"🎯 {name}: 목표가 {tgt} {arrow} 도달 (현재 {ps})")

    def _build_overlay_table(self, name, ticker, vals, triggers) -> dict:
        """screen_overlay type=table 호환 + 적색 펄스 배너용 데이터"""
        is_krw = ticker.upper().endswith(".KS") or ticker.upper().endswith(".KQ")
        price = vals.get("price", 0)
        price_s = f"{price:,.0f}원" if is_krw else f"${price:,.2f}"
        rows = [
            ["현재가", price_s, "▼"],
            ["RSI", f"{vals.get('rsi', 0):.0f}", "▼" if vals.get("rsi", 50) < self.rsi_oversold else "="],
            ["BB 하단", f"{vals.get('bb_lower', 0):,.2f}", "-"],
        ]
        for label, desc in triggers:
            rows.append([label, desc, "+"])  # 매수 신호 → 녹색
        return {
            "type": "table",
            "title": f"🚨 {name} 매수 시그널",
            "headers": ["항목", "값", "신호"],
            "rows": rows,
            "alert_text": f"🚨 {name} 매수 시그널 포착",
        }

    @property
    def status(self):
        return {
            "running": self._running,
            "watch_list": self.watch_list,
            "checks": self._check_count,
            "total_alerts": self._total_alerts,
            "pending": len(self._event_q),
            "interval": f"{self.interval}초",
            "cooldown": f"{self.cooldown}초",
            "cooldown_active": [t for t, until in self._cooldown_until.items()
                                if time.time() < until],
            "targets": [{"name": t["name"], "ticker": t["ticker"],
                         "price": t["price"], "direction": t["direction"]}
                        for t in self.targets],
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LLM 도구 정의
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

PRICE_ALERT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_price_alert_status",
            "description": "실시간 지정가/지표 알림 워커의 상태(감시 종목, 점검 횟수, 누적 알림, 쿨다운)를 조회합니다.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_watchlist_now",
            "description": "감시 종목 전체를 즉시 점검하여 RSI 과매도/볼린저 하단 돌파 시그널을 바로 확인합니다.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_price_alert",
            "description": ("특정 종목이 사용자가 지정한 목표가에 도달하면 알림을 설정합니다. "
                            "'테슬라가 300달러 되면 알려줘', '삼성전자 8만원 가면 알림 줘' 처럼 "
                            "종목과 목표가를 말하면 호출하세요. 재시작 후에도 유지됩니다."),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string",
                               "description": "종목명 또는 티커 (한글 가능: 삼성전자, 테슬라 / 영문: TSLA, AAPL / 코드: 005930)"},
                    "price": {"type": "number", "description": "목표가 (숫자만, 통화기호 제외)"},
                    "direction": {"type": "string", "enum": ["above", "below"],
                                  "description": "above=목표가 이상 도달 시, below=목표가 이하 하락 시. 생략하면 현재가 기준 자동 판정."},
                },
                "required": ["ticker", "price"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_price_alerts",
            "description": "현재 설정된 사용자 지정 목표가 알림 목록을 조회합니다.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_price_alert",
            "description": "특정 종목에 설정된 목표가 알림을 해제합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "해제할 종목명 또는 티커"},
                },
                "required": ["ticker"],
            },
        },
    },
]

_worker_instance: PriceAlertWorker | None = None

# 종목명 오인식 시 후보 선택 대기 컨텍스트 (jarvis_v2 메인 루프가 polling)
#   {"query": 원본질의, "price": 목표가, "direction": 방향|None, "candidates": [..]}
_PENDING_SELECTION: dict | None = None


def set_price_alert_instance(inst: PriceAlertWorker):
    global _worker_instance
    _worker_instance = inst


def request_selection(query, price, direction, candidates):
    """오인식 후보 선택을 요청 — jarvis_v2가 take_pending_selection()으로 가져가 음성 안내."""
    global _PENDING_SELECTION
    _PENDING_SELECTION = {"query": query, "price": price,
                          "direction": direction, "candidates": candidates}


def take_pending_selection() -> dict | None:
    global _PENDING_SELECTION
    v = _PENDING_SELECTION
    _PENDING_SELECTION = None
    return v


def add_target_choice(candidate: dict, price, direction=None) -> dict:
    """후보 목록에서 선택된 항목으로 목표가 알림 등록 (음성 번호 선택 확정)."""
    if not _worker_instance:
        return {"success": False, "message": "지정가 알림 워커 없음"}
    return _worker_instance.add_target(candidate["ticker"], candidate["name"], price, direction)


def add_target_by_query(query: str, price, direction=None) -> dict:
    """직접 입력(타이핑)된 종목명으로 목표가 알림 등록 — 베스트에포트 해석."""
    if not _worker_instance:
        return {"success": False, "message": "지정가 알림 워커 없음"}
    res = resolve_alert_query(query)
    if res["confident"]:
        return _worker_instance.add_target(res["ticker"], res["name"], price, direction)
    tk = _resolve_ticker(query)
    return _worker_instance.add_target(tk, _display_name(tk), price, direction)


def execute_price_alert_tool(tool_name, arguments):
    global _worker_instance
    if not _worker_instance:
        return {"success": False, "message": "지정가 알림 워커 없음"}

    if tool_name == "get_price_alert_status":
        return {"success": True, **_worker_instance.status}

    if tool_name == "check_watchlist_now":
        hits = []
        for ticker in _worker_instance.watch_list:
            resolved = _resolve_ticker(ticker)
            snap = _fetch_snapshot(resolved)
            if not snap:
                continue
            v = snap["values"]
            rsi = v.get("rsi", 50); price = v.get("price", 0); bb = v.get("bb_lower", 0)
            sig = []
            if rsi and rsi < _worker_instance.rsi_oversold:
                sig.append(f"RSI {rsi:.0f} 과매도")
            if bb and price and price <= bb:
                sig.append("볼린저 하단 돌파")
            if sig:
                hits.append({"ticker": resolved, "name": _display_name(resolved),
                             "signals": sig, "rsi": round(rsi, 1), "price": price})
        return {"success": True, "count": len(hits), "hits": hits,
                "message": (f"{len(hits)}개 종목에서 매수 시그널이 포착되었습니다."
                            if hits else "현재 매수 시그널이 포착된 종목은 없습니다.")}

    if tool_name == "set_price_alert":
        ticker = (arguments.get("ticker") or "").strip()
        price = arguments.get("price")
        direction = arguments.get("direction")
        if not ticker or price in (None, ""):
            return {"success": False, "message": "종목과 목표가를 모두 말씀해 주세요."}
        try:
            price = float(price)
        except (TypeError, ValueError):
            return {"success": False, "message": "목표가를 숫자로 인식하지 못했습니다."}

        res = resolve_alert_query(ticker)
        if res["confident"]:
            return _worker_instance.add_target(res["ticker"], res["name"], price, direction)

        # 종목명을 확신하지 못함 → 후보 선택 요청(메인 루프가 음성으로 1~6 안내)
        cands = res["candidates"]
        if not cands:
            return {"success": False,
                    "message": f"'{ticker}' 종목을 찾지 못했습니다. 종목명을 다시 확인해 주세요."}
        request_selection(ticker, price, direction, cands)
        return {"success": True, "needs_selection": True, "candidates": cands,
                "message": f"'{ticker}' 종목명을 정확히 알아듣지 못했습니다. 비슷한 후보를 보여드리겠습니다."}

    if tool_name == "list_price_alerts":
        ts = _worker_instance.list_targets()
        if not ts:
            return {"success": True, "count": 0, "message": f"설정된 지정가 알림이 없습니다, {USER_DISPLAY_NAME}."}
        parts = [f"{t['name']} {_fmt_price(t['ticker'], t['price'])} "
                 f"{'이상' if t['direction'] == 'above' else '이하'}" for t in ts]
        return {"success": True, "count": len(ts),
                "message": "현재 지정가 알림은 " + ", ".join(parts) + " 입니다."}

    if tool_name == "cancel_price_alert":
        ticker = (arguments.get("ticker") or "").strip()
        if not ticker:
            return {"success": False, "message": "해제할 종목을 말씀해 주세요."}
        res = resolve_alert_query(ticker)
        tk = res["ticker"] if res["confident"] else _resolve_ticker(ticker)
        n = _worker_instance.cancel_target(tk)
        return {"success": True,
                "message": (f"{ticker} 지정가 알림 {n}건을 해제했습니다."
                            if n else f"{ticker}에 설정된 지정가 알림이 없습니다.")}

    return {"success": False, "message": f"알 수 없는 도구: {tool_name}"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  단독 실행 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    w = PriceAlertWorker(watch_list=WATCH_LIST or ["AAPL", "NVDA"])
    print("감시 종목:", w.watch_list)
    for tk in w.watch_list:
        snap = _fetch_snapshot(_resolve_ticker(tk))
        if snap:
            v = snap["values"]
            print(f"  {tk}: RSI={v.get('rsi')}, price={v.get('price')}, "
                  f"BB_low={v.get('bb_lower')}")
