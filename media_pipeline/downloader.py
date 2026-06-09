"""
media_pipeline — streamlink + ffmpeg 캡처 다운로더
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
validate_url 통과 URL만 streamlink -O | ffmpeg 로 로컬 덤프.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .config import (
    CAPTURE_MAX_SECONDS,
    CAPTURE_TIMEOUT_SEC,
    DEFAULT_STREAM_QUALITY,
    MEDIA_ROOT,
)
from .errors import CaptureError
from .proc import ManagedProcess, ProcessPipeline, require_binaries
from .url_guard import preflight_media_url


class StreamCapture:
    """외부 라이브/VOD URL → 로컬 미디어 파일 캡처."""

    def __init__(
        self,
        *,
        output_dir: Path | None = None,
        quality: str = DEFAULT_STREAM_QUALITY,
        max_seconds: int = CAPTURE_MAX_SECONDS,
        timeout_sec: int = CAPTURE_TIMEOUT_SEC,
    ):
        self.output_dir = Path(output_dir or MEDIA_ROOT)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.quality = quality
        self.max_seconds = max(1, int(max_seconds))
        self.timeout_sec = max(30, int(timeout_sec))

    def _job_dir(self, job_id: str) -> Path:
        d = (self.output_dir / job_id).resolve()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def build_pipeline(self, url: str, output_path: Path) -> ProcessPipeline:
        """테스트·검증용 — streamlink|ffmpeg 명령 조립."""
        bins = require_binaries("streamlink", "ffmpeg")
        streamlink_cmd = [
            bins["streamlink"],
            "--loglevel", "error",
            "-O", url, self.quality,
        ]
        ffmpeg_cmd = [
            bins["ffmpeg"],
            "-hide_banner", "-loglevel", "error",
            "-y",
            "-i", "pipe:0",
            "-t", str(self.max_seconds),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "48000",
            "-ac", "2",
            str(output_path),
        ]
        upstream = ManagedProcess(streamlink_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        downstream = ManagedProcess(
            ffmpeg_cmd,
            stdin=upstream.stdout_pipe,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        return ProcessPipeline(upstream, downstream)

    def capture(self, url: str, *, job_id: str, title: str = "") -> Path:
        """
        URL을 검증한 뒤 오디오 스트림을 output_dir/{job_id}/capture.* 에 덤프.
        Returns: 캡처 파일 경로
        """
        final_url, _ = preflight_media_url(url)
        job_dir = self._job_dir(job_id)
        out_path = job_dir / "capture.wav"
        pipeline = self.build_pipeline(final_url, out_path)

        pipeline.start()
        try:
            pipeline.wait(timeout=self.timeout_sec)
        except subprocess.TimeoutExpired:
            pipeline.terminate(force=True)
            raise CaptureError(f"캡처 타임아웃({self.timeout_sec}s)", stage="timeout")
        except CaptureError:
            pipeline.terminate(force=True)
            raise
        except Exception as exc:
            pipeline.terminate(force=True)
            raise CaptureError(str(exc), stage="capture") from exc
        finally:
            pipeline.terminate(force=False)

        if not out_path.exists() or out_path.stat().st_size < 128:
            raise CaptureError("캡처 파일이 비어 있거나 생성되지 않음", stage="output")

        return out_path
