"""
media_pipeline — ffmpeg 오디오 추출·AI/MIDI 전처리
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
캡처 파일 → 고품질 master WAV + Whisper mono 16k + sidecar JSON.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import MEDIA_ROOT, PREP_CHANNELS, PREP_SAMPLE_RATE, WHISPER_SAMPLE_RATE
from .errors import PrepError
from .events import PrepArtifact
from .proc import ManagedProcess, require_binaries


class AudioPrepPipeline:
    """캡처 미디어 → 다운스트림 AI/GarageBand용 포맷 큐잉."""

    def __init__(
        self,
        *,
        output_dir: Path | None = None,
        sample_rate: int = PREP_SAMPLE_RATE,
        channels: int = PREP_CHANNELS,
    ):
        self.output_dir = Path(output_dir or MEDIA_ROOT)
        self.sample_rate = sample_rate
        self.channels = channels

    def _prep_dir(self, job_id: str) -> Path:
        d = (self.output_dir / job_id / "prep").resolve()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def build_extract_cmd(self, input_path: Path, output_path: Path) -> list[str]:
        bins = require_binaries("ffmpeg")
        return [
            bins["ffmpeg"],
            "-hide_banner", "-loglevel", "error",
            "-y",
            "-i", str(input_path),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", str(self.sample_rate),
            "-ac", str(self.channels),
            str(output_path),
        ]

    def build_whisper_cmd(self, input_path: Path, output_path: Path) -> list[str]:
        bins = require_binaries("ffmpeg")
        return [
            bins["ffmpeg"],
            "-hide_banner", "-loglevel", "error",
            "-y",
            "-i", str(input_path),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", str(WHISPER_SAMPLE_RATE),
            "-ac", "1",
            str(output_path),
        ]

    def _run_ffmpeg(self, argv: list[str], *, stage: str, timeout: float = 300) -> None:
        proc = ManagedProcess(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        proc.start()
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate(force=True)
            raise PrepError(f"{stage} 타임아웃", stage=stage)
        if rc != 0:
            err = proc.read_stderr_tail()
            raise PrepError(f"{stage} 실패(rc={rc}): {err[:200]}", stage=stage)
        proc.terminate(force=False)

    def prepare(
        self,
        input_path: Path,
        *,
        job_id: str,
        title: str = "",
        url: str = "",
    ) -> PrepArtifact:
        """캡처 파일에서 master WAV + whisper WAV + sidecar JSON 생성."""
        input_path = Path(input_path)
        if not input_path.exists():
            raise PrepError("입력 파일 없음", stage="input")

        prep_dir = self._prep_dir(job_id)
        master_wav = prep_dir / "master_48k.wav"
        whisper_wav = prep_dir / "whisper_16k_mono.wav"
        sidecar = prep_dir / "prep_manifest.json"

        self._run_ffmpeg(self.build_extract_cmd(input_path, master_wav), stage="extract_master")
        self._run_ffmpeg(self.build_whisper_cmd(master_wav, whisper_wav), stage="extract_whisper")

        manifest = {
            "job_id": job_id,
            "title": title,
            "url": url[:200],
            "master_wav": str(master_wav),
            "whisper_wav": str(whisper_wav),
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "whisper_sample_rate": WHISPER_SAMPLE_RATE,
            "downstream": {
                "transcription": str(whisper_wav),
                "midi_engine": str(master_wav),
                "garageband_template": str(master_wav),
            },
        }
        sidecar.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        return PrepArtifact(
            job_id=job_id,
            master_wav=str(master_wav),
            whisper_wav=str(whisper_wav),
            sidecar_json=str(sidecar),
            sample_rate=self.sample_rate,
            channels=self.channels,
        )
