"""HUD용 CPU·메모리 실시간 샘플러 (메인 루프 블로킹 없음)."""
from __future__ import annotations

import threading
import time

_sampler: "ResourceSampler | None" = None
_lock = threading.Lock()


class ResourceSampler:
    def __init__(self, interval: float = 0.8):
        self._interval = max(0.3, interval)
        self._cpu = 0.0
        self._mem = 0.0
        self._jarvis_cpu = 0.0
        self._jarvis_mem = 0.0
        self._ok = False
        self._data_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="hud-resources")
        self._thread.start()

    def _loop(self) -> None:
        try:
            import os

            import psutil
        except ImportError:
            return
        psutil.cpu_percent(interval=None)
        proc = psutil.Process(os.getpid())
        while not self._stop.is_set():
            try:
                cpu = float(psutil.cpu_percent(interval=None))
                mem = float(psutil.virtual_memory().percent)
                j_cpu = float(proc.cpu_percent(interval=None))
                j_mem = float(proc.memory_percent())
                with self._data_lock:
                    self._cpu, self._mem = cpu, mem
                    self._jarvis_cpu, self._jarvis_mem = j_cpu, j_mem
                    self._ok = True
            except Exception:
                pass
            self._stop.wait(self._interval)

    def snapshot(self) -> tuple[float, float]:
        with self._data_lock:
            return self._cpu, self._mem

    def jarvis_snapshot(self) -> tuple[float, float]:
        with self._data_lock:
            return self._jarvis_cpu, self._jarvis_mem

    def ok(self) -> bool:
        with self._data_lock:
            return self._ok

    def stop(self) -> None:
        self._stop.set()


def get_resource_sampler() -> ResourceSampler:
    global _sampler
    with _lock:
        if _sampler is None:
            _sampler = ResourceSampler()
        return _sampler
