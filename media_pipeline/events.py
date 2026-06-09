"""media_pipeline — 작업·산출물 이벤트 모델."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class JobStatus(str, Enum):
    QUEUED = "queued"
    CAPTURING = "capturing"
    PREPARING = "preparing"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class MediaJob:
    url: str
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = ""
    status: JobStatus = JobStatus.QUEUED
    error: str = ""
    stage: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    capture_path: str = ""
    prep_paths: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)

    def touch(self, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)
        self.updated_at = time.time()

    def as_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "url": self.url[:120],
            "title": self.title,
            "status": self.status.value,
            "error": self.error,
            "stage": self.stage,
            "capture_path": self.capture_path,
            "prep_paths": dict(self.prep_paths),
            "meta": dict(self.meta),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class PrepArtifact:
    """AI/MIDI/GarageBand 다운스트림용 전처리 산출물."""

    job_id: str
    master_wav: str
    whisper_wav: str = ""
    sidecar_json: str = ""
    format: str = "wav"
    sample_rate: int = 48000
    channels: int = 2

    def as_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "master_wav": self.master_wav,
            "whisper_wav": self.whisper_wav,
            "sidecar_json": self.sidecar_json,
            "format": self.format,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
        }


def job_event(job: MediaJob, *, kind: str = "media_job") -> dict:
    """jarvis_v2 폴링 호환 이벤트 dict."""
    return {
        "source": "media_pipeline",
        "type": kind,
        "job_id": job.job_id,
        "status": job.status.value,
        "message": job.error or job.title or f"미디어 작업 {job.status.value}",
        "artifact": dict(job.prep_paths),
        "meta": job.as_dict(),
    }
