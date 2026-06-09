"""
J.A.R.V.I.S — FDS: 주체별 베이스라인 프로파일(Profile)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
주체(sub)의 '평소 패턴'을 온라인으로 누적한다(Welford 분산 → z-score).
  · 금액 분포(평균/표준편차) → 이탈률(z-score) 산출
  · 명령 빈도 / 사용 기기 집합 / 활동 시간대
인메모리·스레드 세이프. 차단(block)된 요청은 학습에서 제외(베이스라인 오염 방지).
"""
from __future__ import annotations
import math, time, threading
from collections import defaultdict, Counter


_MIN_SAMPLES = 5          # z-score 판정에 필요한 최소 표본(콜드스타트 보호)


def _new_sub() -> dict:
    return {"amount": {"n": 0, "mean": 0.0, "M2": 0.0},
            "commands": Counter(), "devices": set(),
            "hours": Counter(), "events": 0}


class BaselineProfile:
    def __init__(self, min_samples: int = _MIN_SAMPLES):
        self._lock = threading.RLock()
        self._subs: dict[str, dict] = defaultdict(_new_sub)
        self.min_samples = min_samples

    def update(self, ev):
        """허용/step-up 이벤트로 베이스라인 갱신(Welford 온라인 분산)."""
        with self._lock:
            s = self._subs[ev.sub]
            if ev.amount and ev.amount > 0:                 # 금액 있는 경우만 분포 누적
                a = s["amount"]
                a["n"] += 1
                delta = ev.amount - a["mean"]
                a["mean"] += delta / a["n"]
                a["M2"] += delta * (ev.amount - a["mean"])
            if ev.command:
                s["commands"][ev.command] += 1
            if ev.device_id:
                s["devices"].add(ev.device_id)
            s["hours"][time.localtime(ev.ts).tm_hour] += 1
            s["events"] += 1

    def zscore(self, sub: str, value: float) -> float | None:
        """평소 금액 분포 대비 이탈률. 표본 부족/분산 0 이면 None(판정 보류)."""
        with self._lock:
            a = self._subs.get(sub, {}).get("amount") if sub in self._subs else None
            if not a or a["n"] < self.min_samples:
                return None
            var = a["M2"] / (a["n"] - 1) if a["n"] > 1 else 0.0
            if var <= 0:
                return None
            return (value - a["mean"]) / math.sqrt(var)

    def is_known_device(self, sub: str, device_id: str) -> bool:
        with self._lock:
            return bool(device_id) and device_id in self._subs.get(sub, {}).get("devices", set())

    def command_count(self, sub: str, command: str) -> int:
        with self._lock:
            return self._subs.get(sub, {}).get("commands", Counter()).get(command, 0)

    def seen(self, sub: str) -> int:
        with self._lock:
            return self._subs.get(sub, {}).get("events", 0)

    def snapshot(self, sub: str) -> dict:
        with self._lock:
            s = self._subs.get(sub)
            if not s:
                return {}
            return {"events": s["events"], "amount_n": s["amount"]["n"],
                    "amount_mean": round(s["amount"]["mean"], 2),
                    "devices": sorted(s["devices"]),
                    "top_commands": s["commands"].most_common(3)}
