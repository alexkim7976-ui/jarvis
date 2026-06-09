"""
J.A.R.V.I.S — 선제적 매크로 가드레일 감시 (Proactive Macro Risk Watcher)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
사용자의 요청이 없어도, 매크로 시장 등급이 "위험"으로 격상되는 순간 자비스가
먼저 음성으로 경고·자산 배분 가이드를 브리핑하도록 이벤트를 발생시키는 백그라운드
감시 스레드.

설계 포인트:
  · stock_quant.get_macro_market_status() 의 5분 TTL 캐시(force_refresh=False)와 연동
    → 폴링 주기를 짧게 잡아도 야후 파이낸스 API를 과도하게 호출하지 않는다.
  · 중복 방지(디바운스): "위험" 1회 브리핑 후, 안정/경계로 회복했다가 다시 "위험"으로
    격상될 때만 재알림. (last_macro_status / is_briefed)
  · 스레드 안전: 내부 상태는 Lock, 이벤트 전달은 queue.Queue 로 메인 루프와 분리해
    기존 음성 명령 파이프라인(wake_word → RECORDING)을 교란하지 않는다.
  · 테스트 용이성: status_fn(상태 조회 함수)을 주입할 수 있어 야후 API 없이 단위 검증 가능.
"""
from __future__ import annotations

import os
import queue
import threading

# 감시 주기(초) — 테스트/운영에서 환경변수로 분리 조정
MACRO_POLL_SEC = float(os.getenv("JARVIS_MACRO_POLL_SEC", "60"))
# 안정(multiplier=1.0) 시 매수 비중 상한(%). 위험(0.5)→15%, 경계(0.75)→약 22%로 동적 산출.
MACRO_BASE_BUY_CAP = int(os.getenv("JARVIS_MACRO_BASE_BUY_CAP", "30"))
# 선제 브리핑을 발동할 등급 (기본: 위험)
MACRO_ALERT_LEVEL = os.getenv("JARVIS_MACRO_ALERT_LEVEL", "위험")


def build_macro_briefing(macro: dict, base_cap: int = MACRO_BASE_BUY_CAP) -> str:
    """매크로 상태 dict에서 위험 경고 + 자산 배분 가이드 브리핑 텍스트를 동적으로 생성."""
    mult = macro.get("allocation_multiplier", 0.5)
    try:
        mult = float(mult)
    except (TypeError, ValueError):
        mult = 0.5
    buy_cap = max(0, int(round(base_cap * mult)))
    reasons = macro.get("reasons") or []
    reason_phrase = ", ".join(str(r) for r in reasons[:2]) if reasons else "환율 및 변동성 지표 급등"
    return (
        f"토니, 매크로 가드레일 위험 신호가 발생했습니다. "
        f"{reason_phrase}으로 인해 보유 포트폴리오의 매수 비중 상한선이 즉시 {buy_cap}%로 제한됩니다. "
        f"신규 진입은 보수적으로 접근하시기 바랍니다."
    )


class MacroGuardWatcher(threading.Thread):
    """IDLE 상태에서 매크로 위험도를 주기적으로 감시하다가 '위험' 격상 시 선제 브리핑 이벤트 생성."""

    def __init__(self, status_fn=None, poll_sec: float = MACRO_POLL_SEC,
                 logger=None, base_cap: int = MACRO_BASE_BUY_CAP,
                 alert_level: str = MACRO_ALERT_LEVEL):
        super().__init__(daemon=True)
        self._status_fn = status_fn               # 주입 가능(테스트). 미지정 시 stock_quant 사용
        self.poll_sec = max(5.0, float(poll_sec))
        self._log = logger or (lambda lvl, msg: None)
        self.base_cap = base_cap
        self.alert_level = alert_level

        self._events: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        # 중복 알림 방지용 상태 플래그
        self.last_macro_status: str | None = None
        self.is_briefed: bool = False

    # ── 상태 조회 (캐시 연동) ──
    def _get_status(self) -> dict | None:
        try:
            if self._status_fn is not None:
                return self._status_fn(force_refresh=False)
            from stock_quant import get_macro_market_status
            return get_macro_market_status(force_refresh=False)
        except Exception as e:
            self._log("WARN", f"매크로 감시 조회 실패: {e}")
            return None

    # ── 메인 루프 연동 인터페이스 (다른 백그라운드 워커와 동일 패턴) ──
    def get_events(self) -> list:
        """대기 중인 선제 브리핑 이벤트를 모두 꺼낸다(비움)."""
        out = []
        while True:
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                break
        return out

    def requeue(self, evts: list) -> None:
        """메인 루프가 바빠 처리 못한 이벤트를 되돌린다(다음 IDLE 사이클 재시도)."""
        for e in evts:
            self._events.put(e)

    def stop(self) -> None:
        self._stop.set()

    def current_level(self) -> str:
        """현재 매크로 등급(안정/경계/위험)을 Thread-safe 하게 반환. HUD 테마 연동용."""
        with self._lock:
            return self.last_macro_status or "안정"

    # ── 핵심: 1회 평가 (테스트에서 직접 호출 가능) ──
    def evaluate_once(self) -> dict | None:
        """매크로 상태를 1회 평가. '위험'으로 격상되면 브리핑 이벤트를 큐에 넣고 반환.
        Returns: 새로 생성한 이벤트 dict 또는 None."""
        macro = self._get_status()
        if not macro or not macro.get("success", False):
            return None
        level = macro.get("risk_level", "안정")

        with self._lock:
            prev = self.last_macro_status
            self.last_macro_status = level

            if level == self.alert_level:
                if self.is_briefed:
                    return None  # 이미 같은 위험 구간에서 브리핑함 → 중복 차단
                self.is_briefed = True
                evt = {
                    "message": build_macro_briefing(macro, self.base_cap),
                    "overlay_table": macro.get("overlay_table"),
                    "risk_level": level,
                    "prev_level": prev,
                    "alert_text": "🚨 MACRO RISK GUARDRAIL",
                }
                self._events.put(evt)
                self._log("INTR", f"🚨 매크로 위험 격상 감지(이전={prev}) — 선제 브리핑 큐잉")
                return evt
            else:
                # 안정/경계로 회복 → 디바운스 해제(다음 '위험' 격상 시 재알림)
                if self.is_briefed:
                    self._log("INFO", f"매크로 회복: {level} — 알림 디바운스 해제")
                self.is_briefed = False
                return None

    def run(self) -> None:
        # 부팅 직후 즉시 1회 평가 후 주기적으로 폴링
        while not self._stop.is_set():
            try:
                self.evaluate_once()
            except Exception as e:
                self._log("WARN", f"매크로 감시 루프 오류: {e}")
            self._stop.wait(self.poll_sec)
