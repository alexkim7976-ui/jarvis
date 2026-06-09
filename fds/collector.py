"""
J.A.R.V.I.S — FDS: 행동 이벤트 수집기(Collector)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DeviceGuard 서명 검증을 통과한 요청을 정규화된 행동 이벤트로 수집한다.
주체(sub)별 슬라이딩 윈도우(deque)로 속도/시퀀스 시그널의 원천을 제공.
인메모리·스레드 세이프(RLock) — IDP/registry 와 동일 정렬.
"""
from __future__ import annotations
import time, threading
from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass
class BehaviorEvent:
    """행동 위험평가의 입력 단위(인증·기기검증 이후의 '행동')."""
    sub: str
    command: str                       # 명령 종류(예: rebalance_execute)
    device_id: str = ""
    scope: str = ""
    amount: float = 0.0                # 금액/리스크 파라미터(없으면 0)
    ip: str = ""
    ts: float = field(default_factory=time.time)


class EventCollector:
    def __init__(self, window_sec: int = 60, max_per_sub: int = 512):
        self._lock = threading.RLock()
        self._events: dict[str, deque] = defaultdict(lambda: deque(maxlen=max_per_sub))
        self.window_sec = window_sec

    def record(self, ev: BehaviorEvent):
        with self._lock:
            self._events[ev.sub].append(ev)

    def recent(self, sub: str, within_sec: float | None = None,
               now: float | None = None) -> list[BehaviorEvent]:
        now = now if now is not None else time.time()
        w = self.window_sec if within_sec is None else within_sec
        with self._lock:
            return [e for e in self._events.get(sub, ()) if now - e.ts <= w]

    def count(self, sub: str, within_sec: float | None = None,
              now: float | None = None) -> int:
        return len(self.recent(sub, within_sec, now))

    def previous_command(self, sub: str) -> str | None:
        """가장 최근 기록(=현재 이벤트) 직전 명령 — 시퀀스 규칙용."""
        with self._lock:
            dq = self._events.get(sub)
            if not dq or len(dq) < 2:
                return None
            return dq[-2].command

    def stats(self) -> dict:
        with self._lock:
            return {"subjects": len(self._events),
                    "events": sum(len(d) for d in self._events.values())}
