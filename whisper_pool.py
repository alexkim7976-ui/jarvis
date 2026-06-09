"""
J.A.R.V.I.S — Whisper 모델 공용 풀 (메모리 절감 / Apple Silicon 최적화)
──────────────────────────────────────────────
웨이크워드 감지(wake_word.py)와 본 STT(jarvis_v2.py)가 각각 별도의 Whisper
모델을 메모리에 올리면 RAM이 두 배로 든다(tiny ~75MB + small ~500MB).

이 모듈은 (백엔드, 모델, compute, device) 조합별로 인스턴스를 1개만 만들어
동일 설정이면 두 호출자가 같은 모델을 공유하도록 한다.

백엔드:
  faster   faster-whisper(CTranslate2) — 기본. CPU(ARM NEON) int8로 메모리 최소.
  mlx      mlx-whisper(Apple Metal/GPU·ANE) — M1~M5 전용. 더 빠르고 양자화 모델로
           RAM도 절감 가능. `pip install mlx-whisper` 필요. 미설치/실패 시 faster로 폴백.

환경변수:
  JARVIS_WHISPER_BACKEND      auto | faster | mlx   (기본 auto=Apple Silicon→mlx, 그 외 faster)
  JARVIS_WHISPER_MODEL        본 STT 모델 (기본 small)
  JARVIS_WAKE_WHISPER_MODEL   웨이크워드 모델 (기본 tiny)
  JARVIS_WHISPER_COMPUTE      faster 백엔드 양자화 (기본 int8 — 메모리 최소)
  JARVIS_WHISPER_DEVICE       cpu | cuda (기본 cpu). ※ Mac은 CTranslate2 GPU 미지원 → cpu 고정 권장
  JARVIS_WHISPER_MLX_REPO     mlx 백엔드 HF 리포 강제 지정 (예: mlx-community/whisper-small-mlx-q4)
  JARVIS_LOW_MEM=1            저메모리 모드: 두 모델 모두 tiny로 강제 → 단일 인스턴스 공유
"""
from __future__ import annotations

import os
import threading

from resource_profile import is_apple_silicon

_models: dict[tuple, object] = {}
_lock = threading.Lock()


def _low_mem() -> bool:
    return os.getenv("JARVIS_LOW_MEM", "").strip().lower() in ("1", "true", "yes", "on")


def _backend() -> str:
    b = os.getenv("JARVIS_WHISPER_BACKEND", "auto").strip().lower()
    if b in ("faster", "ctranslate2", "ct2"):
        return "faster"
    if b == "mlx":
        return "mlx"
    # auto: Apple Silicon → MLX(Metal/GPU·ANE). 그 외 → faster-whisper(CPU int8).
    return "mlx" if is_apple_silicon() else "faster"


def _resolve_size(role: str, override: str | None) -> str:
    if override:
        return override
    if _low_mem():
        return "tiny"
    if role == "wake":
        return os.getenv("JARVIS_WAKE_WHISPER_MODEL") or os.getenv("JARVIS_WHISPER_MODEL", "tiny")
    return os.getenv("JARVIS_WHISPER_MODEL", "small")


# ── MLX(Apple Metal) 어댑터 — faster-whisper와 동일한 .transcribe() 인터페이스 제공 ──
class _Seg:
    __slots__ = ("text",)

    def __init__(self, text: str):
        self.text = text


class _MLXWhisperAdapter:
    """mlx-whisper를 faster-whisper처럼 (segments, info) 형태로 감싼다."""

    def __init__(self, repo: str):
        import mlx_whisper  # 미설치 시 ImportError → 호출부에서 faster로 폴백
        self._mlx = mlx_whisper
        self._repo = repo

    def transcribe(self, audio, language=None, **_ignored):
        # faster-whisper 전용 인자(beam_size 등)는 무시한다.
        res = self._mlx.transcribe(audio, path_or_hf_repo=self._repo, language=language)
        raw = res.get("segments") or []
        segs = [_Seg(s.get("text", "")) for s in raw]
        if not segs:
            segs = [_Seg(res.get("text", ""))]
        return segs, res


def _mlx_repo(size: str) -> str:
    override = os.getenv("JARVIS_WHISPER_MLX_REPO", "").strip()
    if override:
        return override
    # Apple Silicon: 4-bit 양자화 리포가 RAM·대역폭 모두 유리 (tiny는 full도 충분히 가벼움)
    if is_apple_silicon() and size not in ("tiny", "base"):
        return f"mlx-community/whisper-{size}-mlx-q4"
    return f"mlx-community/whisper-{size}-mlx"


def get_whisper_model(size: str | None = None, *, role: str = "main"):
    """공유 Whisper 모델 반환. 동일 설정이면 단일 인스턴스 재사용."""
    size = _resolve_size(role, size)
    backend = _backend()

    if backend == "mlx":
        repo = _mlx_repo(size)
        key = ("mlx", repo)
        with _lock:
            model = _models.get(key)
            if model is not None:
                return model
            try:
                model = _MLXWhisperAdapter(repo)
                _models[key] = model
                return model
            except Exception:
                # mlx-whisper 미설치/로드 실패 → faster-whisper로 안전 폴백
                backend = "faster"

    compute = os.getenv("JARVIS_WHISPER_COMPUTE", "int8")
    device = os.getenv("JARVIS_WHISPER_DEVICE", "cpu")
    key = ("faster", size, compute, device)
    with _lock:
        model = _models.get(key)
        if model is None:
            from faster_whisper import WhisperModel
            model = WhisperModel(size, device=device, compute_type=compute)
            _models[key] = model
        return model


def loaded_models() -> list[str]:
    with _lock:
        return ["/".join(str(p) for p in k) for k in _models]
