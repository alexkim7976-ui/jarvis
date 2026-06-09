"""
media_pipeline — 백그라운드 워커
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
작업 큐 → 캡처 → 전처리 → 이벤트 발행. 실패·취소 시 프로세스·파일 정리.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

from .audio_prep import AudioPrepPipeline
from .downloader import StreamCapture
from .errors import MediaPipelineError
from .events import JobStatus, MediaJob, PrepArtifact, job_event


class MediaPipelineWorker:
    """단일 백그라운드 스레드 — 순차 작업 처리(리소스 폭주 방지)."""

    def __init__(
        self,
        *,
        capture: StreamCapture | None = None,
        prep: AudioPrepPipeline | None = None,
        on_complete: Callable[[MediaJob, PrepArtifact | None], None] | None = None,
        on_error: Callable[[MediaJob, Exception], None] | None = None,
        logger: Callable[[str, str], None] | None = None,
    ):
        self.capture = capture or StreamCapture()
        self.prep = prep or AudioPrepPipeline()
        self._on_complete = on_complete
        self._on_error = on_error
        self._log = logger or (lambda _lvl, _msg: None)
        self._lock = threading.RLock()
        self._queue: deque[MediaJob] = deque()
        self._jobs: dict[str, MediaJob] = {}
        self._thread: threading.Thread | None = None
        self._running = False
        self._current: MediaJob | None = None

    def submit(self, url: str, *, title: str = "", meta: dict | None = None) -> MediaJob:
        job = MediaJob(url=url.strip(), title=title or "외부 미디어", meta=dict(meta or {}))
        with self._lock:
            self._queue.append(job)
            self._jobs[job.job_id] = job
        self._log("INFO", f"📥 Media job queued — {job.job_id} {url[:60]}")
        return job

    def get_job(self, job_id: str) -> MediaJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            if job.status in (JobStatus.READY, JobStatus.FAILED, JobStatus.CANCELLED):
                return False
            if job.status == JobStatus.QUEUED:
                job.touch(status=JobStatus.CANCELLED, error="사용자 취소")
                try:
                    self._queue.remove(job)
                except ValueError:
                    pass
                return True
            job.touch(status=JobStatus.CANCELLED, error="진행 중 취소 요청")
            return True

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="MediaPipelineWorker", daemon=True)
        self._thread.start()

    def stop(self, *, join_timeout: float = 5.0) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=join_timeout)
            self._thread = None

    def pending(self) -> int:
        with self._lock:
            return len(self._queue)

    def stats(self) -> dict:
        with self._lock:
            by_status: dict[str, int] = {}
            for j in self._jobs.values():
                by_status[j.status.value] = by_status.get(j.status.value, 0) + 1
            return {
                "queued": len(self._queue),
                "total_jobs": len(self._jobs),
                "running": self._running,
                "current_job": self._current.job_id if self._current else None,
                "by_status": by_status,
            }

    def _loop(self) -> None:
        while self._running:
            job = None
            with self._lock:
                if self._queue:
                    job = self._queue.popleft()
            if not job:
                time.sleep(0.25)
                continue
            if job.status == JobStatus.CANCELLED:
                continue
            self._current = job
            try:
                self._process(job)
            except Exception as exc:
                self._fail(job, exc)
            finally:
                self._current = None

    def _process(self, job: MediaJob) -> None:
        job.touch(status=JobStatus.CAPTURING, stage="capture")
        self._log("INFO", f"🎬 캡처 시작 — {job.job_id}")

        capture_path = self.capture.capture(job.url, job_id=job.job_id, title=job.title)
        job.touch(capture_path=str(capture_path), stage="prep", status=JobStatus.PREPARING)

        artifact = self.prep.prepare(
            capture_path,
            job_id=job.job_id,
            title=job.title,
            url=job.url,
        )
        job.touch(
            status=JobStatus.READY,
            stage="ready",
            prep_paths=artifact.as_dict(),
            error="",
        )
        self._log("OK", f"✅ Media prep ready — {job.job_id}")
        if self._on_complete:
            try:
                self._on_complete(job, artifact)
            except Exception:
                pass

    def _fail(self, job: MediaJob, exc: Exception) -> None:
        stage = getattr(exc, "stage", "") or "worker"
        msg = getattr(exc, "message", str(exc))
        job.touch(status=JobStatus.FAILED, error=msg, stage=stage)
        self._log("WARN", f"Media job failed — {job.job_id}: {msg}")
        if self._on_error:
            try:
                self._on_error(job, exc if isinstance(exc, Exception) else MediaPipelineError(msg))
            except Exception:
                pass
