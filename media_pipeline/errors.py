"""media_pipeline — 예외 타입."""
from __future__ import annotations


class MediaPipelineError(Exception):
    """파이프라인 일반 오류."""

    def __init__(self, message: str, *, stage: str = ""):
        super().__init__(message)
        self.stage = stage
        self.message = message


class URLGuardError(MediaPipelineError):
    """SSRF/리다이렉트/DNS 가드 거부."""


class CaptureError(MediaPipelineError):
    """streamlink/ffmpeg 캡처 실패."""


class PrepError(MediaPipelineError):
    """오디오 추출·포맷팅 실패."""
