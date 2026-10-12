"""
J.A.R.V.I.S — 전력/유휴 관리
백그라운드(미사용) 시 CPU·배터리 0 — 프로세스 종료, 앱 클릭으로 재기동.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

_PROJECT = Path(__file__).resolve().parent


def _runtime_dir() -> Path:
    """앱 런처(JARVIS_DATA_DIR)와 동일 위치에 PID·웨이크 플래그."""
    dd = (os.getenv("JARVIS_DATA_DIR") or "").strip()
    return Path(dd) if dd else _PROJECT


PID_FILE = _runtime_dir() / ".jarvis.pid"
WAKE_FLAG = _runtime_dir() / ".jarvis.wake"


def auto_quit_idle_seconds() -> int:
    try:
        return max(0, int(os.getenv("JARVIS_AUTO_QUIT_IDLE_SEC", "300")))
    except ValueError:
        return 300


def write_pid(pid: int) -> None:
    PID_FILE.write_text(str(pid), encoding="utf-8")


def read_pid() -> int | None:
    try:
        if PID_FILE.is_file():
            return int(PID_FILE.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        pass
    return None


def clear_pid() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def is_running() -> bool:
    pid = read_pid()
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        clear_pid()
        return False


def request_wake() -> None:
    """실행 중인 Jarvis에 깨우기/포커스 요청."""
    WAKE_FLAG.write_text(str(time.time()), encoding="utf-8")


def consume_wake_request() -> bool:
    if WAKE_FLAG.is_file():
        try:
            WAKE_FLAG.unlink()
        except OSError:
            pass
        return True
    return False


class IdleTracker:
    """마지막 사용자 활동 시각 추적."""

    def __init__(self) -> None:
        self._last = time.time()

    def touch(self) -> None:
        self._last = time.time()

    def idle_seconds(self) -> float:
        return time.time() - self._last

    def should_auto_quit(self) -> bool:
        limit = auto_quit_idle_seconds()
        return limit > 0 and self.idle_seconds() >= limit
