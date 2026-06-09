"""media_pipeline — 환경 설정."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 캡처·추출 산출물은 샌드박스 내부로만 (외부 경로 주입 금지)
try:
    from sandbox import SANDBOX_ROOT, init_sandbox

    init_sandbox(create_standard_dirs=False)
    MEDIA_ROOT = (SANDBOX_ROOT / "media_captures").resolve()
except Exception:
    MEDIA_ROOT = (PROJECT_ROOT / "data" / "sandbox" / "media_captures").resolve()

MEDIA_ROOT.mkdir(parents=True, exist_ok=True)

MEDIA_PIPELINE_ENABLED = os.getenv("MEDIA_PIPELINE_ENABLED", "1").strip().lower() not in (
    "0", "false", "off",
)
MAX_REDIRECTS = int(os.getenv("MEDIA_MAX_REDIRECTS", "10"))
CAPTURE_TIMEOUT_SEC = int(os.getenv("MEDIA_CAPTURE_TIMEOUT_SEC", "600"))
CAPTURE_MAX_SECONDS = int(os.getenv("MEDIA_CAPTURE_MAX_SECONDS", "3600"))
PROC_TERMINATE_GRACE_SEC = float(os.getenv("MEDIA_PROC_GRACE_SEC", "3"))
PREFLIGHT_REDIRECTS = os.getenv("MEDIA_PREFLIGHT_REDIRECTS", "1").strip().lower() not in (
    "0", "false", "off",
)
DEFAULT_STREAM_QUALITY = os.getenv("MEDIA_STREAM_QUALITY", "best")
PREP_SAMPLE_RATE = int(os.getenv("MEDIA_PREP_SAMPLE_RATE", "48000"))
PREP_CHANNELS = int(os.getenv("MEDIA_PREP_CHANNELS", "2"))
WHISPER_SAMPLE_RATE = int(os.getenv("MEDIA_WHISPER_SAMPLE_RATE", "16000"))
