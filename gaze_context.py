"""
시선 기반 상황 인지 공유 컨텍스트 (Gaze-Triggered Context / Shared State)

- 화면 UI 영역(종목 HUD 패널 등)의 Bounding Box 레지스트리를 관리한다.
- 외부에서 들어온 시선 좌표(screen_x, screen_y)에 대해 3초 dwell 디바운스를 수행,
  같은 영역을 임계 시간 이상 응시하면 `current_focused_ticker` 를 확정하고
  `GazeFocusedTickerChanged` 이벤트를 발행한다.
- 이 모듈은 프로젝트 내부 의존성이 전혀 없다(표준 라이브러리만 사용) → face_module,
  stock_quant, jarvis_v2 어디서 import 해도 순환 참조가 생기지 않는다.

스레드 안전: 모든 상태 변경은 RLock 으로 보호된다. 이벤트 콜백은 락을 푼 뒤 호출한다.
"""

from __future__ import annotations

import threading
import time as _time
from typing import Callable, Optional, Tuple

# 3초 이상 응시 유지 시 포커스 확정 (디바운스 임계)
DWELL_SECONDS = 3.0
# 확정된 포커스의 신선도. 이보다 오래되면 만료로 간주(오래된 시선으로 오동작 방지)
FOCUS_TTL_SECONDS = 90.0


class GazeContext:
    """시선 영역 레지스트리 + dwell 디바운스 + 포커스 공유 상태 + 이벤트 버스."""

    def __init__(self, dwell_seconds: float = DWELL_SECONDS,
                 ttl_seconds: float = FOCUS_TTL_SECONDS):
        self._lock = threading.RLock()
        self._dwell_seconds = float(dwell_seconds)
        self._ttl_seconds = float(ttl_seconds)

        # region_id -> {"bbox": (x0, y0, x1, y1), "ticker": str, "label": str}
        self._regions: dict[str, dict] = {}

        # dwell 디바운스 상태
        self._dwell_region: Optional[str] = None
        self._dwell_start: float = 0.0

        # 확정된 포커스 (= current_focused_ticker)
        self._focused_ticker: Optional[str] = None
        self._focused_label: str = ""
        self._focused_at: float = 0.0

        # GazeFocusedTickerChanged 구독자
        self._observers: list[Callable[[dict], None]] = []

    # ──────────────────────────────────────────────
    #  화면 UI 영역 레지스트리 (HUD/오버레이가 패널 위치를 등록)
    # ──────────────────────────────────────────────
    def register_region(self, region_id: str, bbox: Tuple[float, float, float, float],
                        ticker: str, label: str = "") -> None:
        """종목 패널 등 UI 영역의 화면 Bounding Box 를 등록/갱신한다.

        bbox: (x0, y0, x1, y1) — 화면(렌더 프레임) 좌표.
        """
        x0, y0, x1, y1 = bbox
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        ticker = (ticker or "").strip()
        with self._lock:
            self._regions[str(region_id)] = {
                "bbox": (float(x0), float(y0), float(x1), float(y1)),
                "ticker": ticker,
                "label": str(label or ticker),
            }

    def unregister_region(self, region_id: str) -> None:
        with self._lock:
            self._regions.pop(str(region_id), None)
            if self._dwell_region == str(region_id):
                self._dwell_region = None

    def clear_regions(self) -> None:
        with self._lock:
            self._regions.clear()
            self._dwell_region = None

    def regions_snapshot(self) -> dict:
        with self._lock:
            return {k: dict(v) for k, v in self._regions.items()}

    def _region_at(self, x: float, y: float):
        # 겹치는 영역이 있으면 마지막(가장 위에 그려진) 영역 우선
        hit = (None, None)
        for rid, r in self._regions.items():
            x0, y0, x1, y1 = r["bbox"]
            if x0 <= x <= x1 and y0 <= y <= y1:
                hit = (rid, r)
        return hit

    # ──────────────────────────────────────────────
    #  시선 좌표 입력 + dwell 디바운스
    # ──────────────────────────────────────────────
    def update_gaze(self, x: float, y: float, now: Optional[float] = None) -> Optional[str]:
        """시선 좌표(x, y)를 받아 dwell 디바운스를 수행한다.

        같은 영역을 dwell_seconds 이상 연속 응시하면 포커스를 확정하고
        GazeFocusedTickerChanged 이벤트를 발행한다.

        반환값: 이번 호출에서 '새 포커스가 확정'되면 해당 ticker, 아니면 None.
        """
        now = _time.time() if now is None else now
        fire: Optional[Tuple[str, str]] = None

        with self._lock:
            rid, r = self._region_at(x, y)
            if rid is None:
                # 영역 밖 응시 — dwell 리셋. (이미 확정된 포커스는 TTL 까지 유지)
                self._dwell_region = None
                return None

            if rid != self._dwell_region:
                # 새 영역으로 시선 이동 → 디바운스 타이머 재시작
                self._dwell_region = rid
                self._dwell_start = now
                return None

            # 동일 영역 계속 응시 중
            if (now - self._dwell_start) >= self._dwell_seconds:
                ticker = r["ticker"]
                label = r["label"]
                if ticker and ticker != self._focused_ticker:
                    self._focused_ticker = ticker
                    self._focused_label = label
                    self._focused_at = now
                    fire = (ticker, label)
                else:
                    # 동일 종목을 계속 보는 중이면 신선도만 갱신
                    self._focused_at = now

        if fire:
            self._emit(fire[0], fire[1], source="gaze")
            return fire[0]
        return None

    # ──────────────────────────────────────────────
    #  포커스 직접 설정/조회 (테스트·수동 제어용)
    # ──────────────────────────────────────────────
    def set_focused_ticker(self, ticker: str, label: str = "",
                           source: str = "manual", now: Optional[float] = None) -> Optional[str]:
        now = _time.time() if now is None else now
        ticker = (ticker or "").strip()
        with self._lock:
            changed = ticker != self._focused_ticker
            self._focused_ticker = ticker or None
            self._focused_label = (label or ticker) if ticker else ""
            self._focused_at = now
        if ticker and changed:
            self._emit(ticker, label or ticker, source)
        return ticker or None

    def get_focused_ticker(self, max_age: Optional[float] = None,
                           now: Optional[float] = None) -> Optional[str]:
        """신선한(TTL 이내) 포커스 티커를 반환. 없거나 만료면 None.

        now: 기준 시각 주입(테스트용). 미지정 시 현재 시각.
        """
        max_age = self._ttl_seconds if max_age is None else max_age
        now = _time.time() if now is None else now
        with self._lock:
            if not self._focused_ticker:
                return None
            if max_age is not None and (now - self._focused_at) > max_age:
                return None
            return self._focused_ticker

    def get_focused_label(self, max_age: Optional[float] = None,
                          now: Optional[float] = None) -> str:
        max_age = self._ttl_seconds if max_age is None else max_age
        now = _time.time() if now is None else now
        with self._lock:
            if not self._focused_ticker:
                return ""
            if max_age is not None and (now - self._focused_at) > max_age:
                return ""
            return self._focused_label or self._focused_ticker

    def get_focus(self, max_age: Optional[float] = None,
                  now: Optional[float] = None) -> Tuple[Optional[str], str]:
        return self.get_focused_ticker(max_age, now), self.get_focused_label(max_age, now)

    def clear_focus(self) -> None:
        with self._lock:
            self._focused_ticker = None
            self._focused_label = ""
            self._dwell_region = None

    # ──────────────────────────────────────────────
    #  이벤트 (GazeFocusedTickerChanged)
    # ──────────────────────────────────────────────
    def subscribe(self, callback: Callable[[dict], None]) -> None:
        with self._lock:
            if callback not in self._observers:
                self._observers.append(callback)

    def unsubscribe(self, callback: Callable[[dict], None]) -> None:
        with self._lock:
            if callback in self._observers:
                self._observers.remove(callback)

    def _emit(self, ticker: str, label: str, source: str) -> None:
        payload = {
            "event": "GazeFocusedTickerChanged",
            "ticker": ticker,
            "label": label,
            "source": source,
            "ts": _time.time(),
        }
        with self._lock:
            observers = list(self._observers)
        for cb in observers:
            try:
                cb(payload)
            except Exception:
                # 한 구독자의 예외가 다른 구독자/메인 루프를 막지 않도록 격리
                pass


# ──────────────────────────────────────────────
#  프로세스 전역 싱글톤
# ──────────────────────────────────────────────
_GAZE_SINGLETON: Optional[GazeContext] = None
_SINGLETON_LOCK = threading.Lock()


def get_gaze_context() -> GazeContext:
    """프로세스 전역 GazeContext 싱글톤."""
    global _GAZE_SINGLETON
    if _GAZE_SINGLETON is None:
        with _SINGLETON_LOCK:
            if _GAZE_SINGLETON is None:
                _GAZE_SINGLETON = GazeContext()
    return _GAZE_SINGLETON


# 편의 모듈 함수 (싱글톤 위임)
def register_region(region_id, bbox, ticker, label=""):
    return get_gaze_context().register_region(region_id, bbox, ticker, label)


def update_gaze(x, y, now=None):
    return get_gaze_context().update_gaze(x, y, now)


def get_focused_ticker(max_age=None):
    return get_gaze_context().get_focused_ticker(max_age)


def set_focused_ticker(ticker, label="", source="manual"):
    return get_gaze_context().set_focused_ticker(ticker, label, source)


def subscribe(callback):
    return get_gaze_context().subscribe(callback)


__all__ = [
    "GazeContext", "get_gaze_context", "register_region", "update_gaze",
    "get_focused_ticker", "set_focused_ticker", "subscribe",
    "DWELL_SECONDS", "FOCUS_TTL_SECONDS",
]
