"""
media_pipeline — 런타임 오케스트레이터
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
jarvis_v2 폴링 계약(get_events/requeue) + submit API.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Callable

from .config import MEDIA_PIPELINE_ENABLED
from .events import MediaJob, PrepArtifact, job_event
from .worker import MediaPipelineWorker


class MediaPipelineRuntime:
    """외부 미디어 스트리밍·오디오 파싱 백그라운드 파이프라인."""

    def __init__(
        self,
        *,
        worker: MediaPipelineWorker | None = None,
        logger: Callable[[str, str], None] | None = None,
    ):
        self._log = logger or (lambda _lvl, _msg: None)
        self._lock = threading.RLock()
        self._event_q: deque = deque(maxlen=100)
        self._prep_q: deque[PrepArtifact] = deque(maxlen=32)

        if worker is None:
            worker = MediaPipelineWorker(
                on_complete=self._on_job_complete,
                on_error=self._on_job_error,
                logger=logger,
            )
        self.worker = worker

    def _on_job_complete(self, job: MediaJob, artifact: PrepArtifact | None) -> None:
        with self._lock:
            self._event_q.append(job_event(job, kind="media_ready"))
            if artifact:
                self._prep_q.append(artifact)

    def _on_job_error(self, job: MediaJob, exc: Exception) -> None:
        with self._lock:
            self._event_q.append(job_event(job, kind="media_failed"))

    def start(self) -> None:
        self.worker.start()
        self._log("OK", "📡 Media Pipeline worker started")

    def stop(self) -> None:
        self.worker.stop()

    def submit_url(self, url: str, *, title: str = "", meta: dict | None = None) -> MediaJob:
        return self.worker.submit(url, title=title, meta=meta)

    def get_job(self, job_id: str) -> MediaJob | None:
        return self.worker.get_job(job_id)

    def cancel_job(self, job_id: str) -> bool:
        return self.worker.cancel(job_id)

    def drain_prep_queue(self) -> list[dict]:
        """GarageBand/MIDI/AI 엔진용 전처리 산출물 일괄 반환."""
        with self._lock:
            out = [a.as_dict() for a in self._prep_q]
            self._prep_q.clear()
            return out

    def get_events(self) -> list:
        with self._lock:
            out = list(self._event_q)
            self._event_q.clear()
            return out

    def requeue(self, events) -> None:
        with self._lock:
            for evt in reversed(events):
                self._event_q.appendleft(evt)

    def stats(self) -> dict:
        return {"worker": self.worker.stats(), "prep_pending": len(self._prep_q)}


_runtime: MediaPipelineRuntime | None = None
_runtime_lock = threading.Lock()


def create_media_runtime(*, logger: Callable[[str, str], None] | None = None) -> MediaPipelineRuntime:
    return MediaPipelineRuntime(logger=logger)


def init_media_runtime(rt: MediaPipelineRuntime) -> MediaPipelineRuntime:
    global _runtime
    with _runtime_lock:
        _runtime = rt
    rt.start()
    return rt


def get_media_runtime() -> MediaPipelineRuntime | None:
    return _runtime
