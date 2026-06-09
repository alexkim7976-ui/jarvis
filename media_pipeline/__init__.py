"""
J.A.R.V.I.S — 외부 미디어 스트리밍·오디오 파싱 백그라운드 파이프라인
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
YouTube/라이브 URL → validate_url(SSRF) → streamlink|ffmpeg 캡처
→ ffmpeg 고품질 추출 → AI/MIDI/GarageBand 전처리 큐.

  사용자 URL ──▶ url_guard (SSRF + 리다이렉트≤10) ──▶ StreamCapture
                                                          │
                                                          ▼
                                                    AudioPrepPipeline
                                                          │
                                                          ▼
                                              PrepQueue / get_events()

설계 원칙:
  · ir_interpreter / risk_engine / jarvis_v2 비수정 — 격리 패키지.
  · subprocess 리스트 인자만, shell=False, 프로세스 그룹 SIGTERM→SIGKILL.
  · 산출물은 sandbox/media_captures/ 내부로만.
"""
from .config import MEDIA_ROOT, MEDIA_PIPELINE_ENABLED
from .errors import CaptureError, MediaPipelineError, PrepError, URLGuardError
from .events import JobStatus, MediaJob, PrepArtifact, job_event
from .url_guard import guard_media_url, preflight_media_url
from .proc import ManagedProcess, ProcessPipeline, require_binaries
from .downloader import StreamCapture
from .audio_prep import AudioPrepPipeline
from .worker import MediaPipelineWorker
from .runtime import (
    MediaPipelineRuntime,
    create_media_runtime,
    get_media_runtime,
    init_media_runtime,
)

__all__ = [
    "MEDIA_ROOT", "MEDIA_PIPELINE_ENABLED",
    "MediaPipelineError", "URLGuardError", "CaptureError", "PrepError",
    "JobStatus", "MediaJob", "PrepArtifact", "job_event",
    "guard_media_url", "preflight_media_url",
    "ManagedProcess", "ProcessPipeline", "require_binaries",
    "StreamCapture", "AudioPrepPipeline",
    "MediaPipelineWorker", "MediaPipelineRuntime",
    "create_media_runtime", "get_media_runtime", "init_media_runtime",
]
