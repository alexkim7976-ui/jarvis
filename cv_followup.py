"""
J.A.R.V.I.S — 주식 2단계 분석 (예비 답변 → 백그라운드 교차검증 + 멀티 AI 정밀 분석)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
사용자가 종목을 물으면:
  1) 메인 흐름이 빠른 예비 답변(단일 실시간 시세/캐시)을 먼저 음성으로 전달하고
  2) 이 모듈이 백그라운드 스레드에서 모든 무료 데이터 소스를 교차검증 +
     Nemotron 멀티에이전트(기술 A / 가치 B / 오케스트레이터) 정밀 분석 수행 (Gemini 0)
  3) 완료되면 get_events() 로 후속 브리핑을 큐에 넣어 메인 루프가 음성으로 정정/확정한다.

price_alert / macro_guard / stock_monitor 와 동일한 get_events()/requeue() 계약을 따른다.

활성화: 환경변수 JARVIS_STOCK_TWO_PHASE=1 (jarvis_v2 시작 시 자동 설정).
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any

from speech_style import USER_DISPLAY_NAME, polish_for_voice


def two_phase_enabled() -> bool:
    """2단계(예비→교차검증) 분석 사용 여부."""
    return os.getenv("JARVIS_STOCK_TWO_PHASE", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


# 예비가 대비 합의가 편차가 이 값 이상이면 '정정' 톤, 미만이면 '확정' 톤
_CORRECTION_TOL = float(os.getenv("CV_FOLLOWUP_CORRECTION_TOL", "0.01"))
# 동일 종목 재요청 디바운스(초) — 짧은 시간 중복 후속 방지
_DEBOUNCE_SEC = float(os.getenv("CV_FOLLOWUP_DEBOUNCE_SEC", "8"))


def _fmt_price(v: float | None, currency: str) -> str:
    if v is None:
        return ""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ""
    if currency == "KRW":
        return f"{v:,.0f}원"
    if currency == "USD":
        return f"${v:,.2f}"
    return f"{v:,.2f}"


def build_followup_briefing(
    query: str,
    *,
    preliminary_price: float | None = None,
    currency: str = "",
    timeout: float = 15.0,
) -> str:
    """
    모든 무료 데이터 소스 교차검증 + 멀티에이전트(Nemotron) 정밀 분석 → 음성 후속 브리핑.

    Gemini 0회. 멀티에이전트 실패 시 순수 교차검증 브리핑으로 폴백.
    """
    narrative = ""
    consensus: float | None = None
    sources: list[str] = []
    name = query
    cur = currency

    # ① 모든 무료 AI(멀티에이전트) + 전체 데이터 소스 교차검증 정밀 분석
    try:
        from local_intelligence import refresh_local_analysis_report
        rep = refresh_local_analysis_report(query, timeout=timeout, force_ollama=True)
        narrative = (getattr(rep, "narrative", "") or "").strip()
        consensus = getattr(rep, "consensus_price", None)
        sources = list(getattr(rep, "sources_ok", []) or [])
        name = getattr(rep, "name", None) or query
    except Exception:
        pass

    # ② 폴백 — 멀티에이전트 내러티브가 없으면 순수 교차검증 브리핑
    if not narrative:
        try:
            from cross_validation import (
                fetch_all_parallel, cross_validate, format_briefing,
            )
            report = cross_validate(fetch_all_parallel(query, timeout=timeout), query=query)
            if consensus is None:
                consensus = report.price.final
            sources = report.sources_succeeded or sources
            name = report.name or name
            cur = report.currency or cur
            narrative = format_briefing(report)
        except Exception:
            return ""

    if not narrative and consensus is None:
        return ""

    n_src = len(sources)

    # ③ 예비가 대비 정정/확정 리드 문구
    if preliminary_price and consensus:
        try:
            dev = abs(float(consensus) - float(preliminary_price)) / float(preliminary_price)
        except (TypeError, ValueError, ZeroDivisionError):
            dev = 0.0
        price_txt = _fmt_price(consensus, cur)
        if dev >= _CORRECTION_TOL:
            lead = (
                f"{USER_DISPLAY_NAME}, 방금 예비로 알려드린 값을 정정할게요. "
                f"{n_src}개 무료 채널을 교차검증해 보니 {name} 합의가는 {price_txt}이에요. "
            )
        else:
            lead = (
                f"{USER_DISPLAY_NAME}, 교차검증까지 끝냈어요. "
                f"{n_src}개 채널 값이 거의 일치해요. "
            )
    elif n_src:
        lead = f"{USER_DISPLAY_NAME}, {n_src}개 무료 채널을 교차검증하고 AI 정밀 분석까지 마쳤어요. "
    else:
        lead = f"{USER_DISPLAY_NAME}, 교차검증 분석을 마쳤어요. "

    return polish_for_voice((lead + narrative).strip())


class CrossValFollowup:
    """예비 답변 이후 백그라운드 교차검증+AI 후속 브리핑을 큐로 전달."""

    def __init__(self, logger=None):
        self._q: deque = deque()
        self._lock = threading.Lock()
        self._active: set[str] = set()          # 현재 분석 진행 중인 종목
        self._last_start: dict[str, float] = {}  # query -> 마지막 시작시각 (디바운스)
        self._log = logger or (lambda lvl, msg: None)

    # ── 이벤트 큐 (price_alert 계약과 동일) ──
    def get_events(self) -> list[dict]:
        with self._lock:
            out = list(self._q)
            self._q.clear()
        return out

    def requeue(self, events) -> None:
        with self._lock:
            for e in reversed(events):
                self._q.appendleft(e)

    def _enqueue(self, evt: dict) -> None:
        with self._lock:
            self._q.append(evt)

    def pending(self) -> int:
        with self._lock:
            return len(self._q)

    # ── 2단계 시작 (백그라운드 정밀 분석 스레드) ──
    def start(
        self,
        query: str,
        *,
        preliminary_price: float | None = None,
        currency: str = "",
        timeout: float = 15.0,
    ) -> bool:
        q = (query or "").strip()
        if not q:
            return False
        key = q.lower()
        now = time.time()
        with self._lock:
            if key in self._active:
                return False   # 이미 분석 진행 중 — 중복 스레드 방지
            last = self._last_start.get(key)
            if last is not None and (now - last) < _DEBOUNCE_SEC:
                return False   # 최근 동일 요청 — 디바운스
            self._active.add(key)
            self._last_start[key] = now
        threading.Thread(
            target=self._run,
            args=(q, preliminary_price, currency, timeout),
            daemon=True,
        ).start()
        return True

    def _run(self, query, preliminary_price, currency, timeout):
        try:
            msg = build_followup_briefing(
                query,
                preliminary_price=preliminary_price,
                currency=currency,
                timeout=timeout,
            )
            if msg:
                self._enqueue({
                    "message": msg,
                    "query": query,
                    "name": query,
                    "ts": time.time(),
                })
                self._log("INFO", f"🔎 교차검증 후속 브리핑 준비: {query}")
        except Exception as e:
            self._log("WARN", f"교차검증 후속 실패: {e}")
        finally:
            with self._lock:
                self._active.discard(query.lower())


_INSTANCE: CrossValFollowup | None = None


def get_cv_followup(logger=None) -> CrossValFollowup:
    """프로세스 공용 싱글턴 — 도구 실행부와 메인 루프가 동일 인스턴스를 공유."""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = CrossValFollowup(logger=logger)
    elif logger is not None:
        _INSTANCE._log = logger
    return _INSTANCE


__all__ = [
    "two_phase_enabled",
    "build_followup_briefing",
    "CrossValFollowup",
    "get_cv_followup",
]
