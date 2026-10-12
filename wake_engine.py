"""
J.A.R.V.I.S — 웨이크워드 엔진 (openWakeWord + Whisper fuzzy 매칭)

환경변수:
  JARVIS_WAKE_ENGINE       off | whisper | openwakeword | hybrid (기본 hybrid)
  JARVIS_OWW_THRESHOLD     openWakeWord 점수 임계 (기본 0.55)
  JARVIS_OWW_MODELS        콤마 구분 모델명 (기본 hey_jarvis)
  WAKE_WORDS               콤마 구분 STT 키워드 (자비스야 포함 권장)
"""
from __future__ import annotations

import os
import re
import unicodedata
from typing import Optional

import numpy as np

_OWW_CHUNK = 1280  # 16kHz × 80ms


def wake_engine_mode() -> str:
    return os.getenv("JARVIS_WAKE_ENGINE", "hybrid").strip().lower()


def oww_enabled() -> bool:
    mode = wake_engine_mode()
    return mode in ("openwakeword", "oww", "hybrid", "both")


def whisper_confirm_required() -> bool:
    mode = wake_engine_mode()
    return mode in ("whisper", "hybrid", "both", "openwakeword", "oww")


def oww_threshold() -> float:
    try:
        return float(os.getenv("JARVIS_OWW_THRESHOLD", "0.55"))
    except ValueError:
        return 0.55


def oww_model_names() -> list[str]:
    raw = os.getenv("JARVIS_OWW_MODELS", "hey_jarvis").strip()
    return [m.strip() for m in raw.split(",") if m.strip()]


def default_wake_words() -> list[str]:
    raw = os.getenv(
        "WAKE_WORDS",
        "자비스야,자비스,jarvis,hey jarvis,쟈비스,자바스,저비스",
    )
    return [w.strip().lower() for w in raw.split(",") if w.strip()]


def normalize_wake_text(text: str) -> str:
    """STT 결과 정규화 — 공백·특수문자·한글 자모 분리 보정."""
    t = unicodedata.normalize("NFKC", (text or "").strip().lower())
    t = re.sub(r"[^\w\s가-힣]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# Whisper가 자주 내는 한국어/영어 변형
_WAKE_ALIASES = (
    "자비스야", "자비스", "자비스아", "자비스 여", "자비스요",
    "쟈비스", "자바스", "저비스", "jarvis", "hey jarvis", "heyjarvis",
    "자 bi 스", "자비",
)


def _compact_hangul(text: str) -> str:
    return re.sub(r"\s+", "", text)


def matches_wake_word(text: str, wake_words: list[str] | None = None) -> bool:
    """fuzzy 웨이크워드 매칭 — '자비스야'·'hey jarvis'·STT 오인식 허용."""
    norm = normalize_wake_text(text)
    if not norm:
        return False

    compact = _compact_hangul(norm)
    pool = set(wake_words or default_wake_words())
    pool.update(_WAKE_ALIASES)
    pool = {normalize_wake_text(w) for w in pool if w}

    for w in pool:
        if not w:
            continue
        if w in norm or w in compact:
            return True
        wc = _compact_hangul(w)
        if wc and wc in compact:
            return True

    # '야' 어미만 붙은 자비스 계열
    if re.search(r"(자비스|쟈비스|jarvis)", compact, re.I):
        if re.search(r"(야|요|아)$", compact) or "jarvis" in compact:
            return True
    return False


class OpenWakeWordFrontEnd:
    """openWakeWord ONNX 프론트엔드 — 영어 'hey jarvis' 저지연 감지."""

    def __init__(self):
        self._model = None
        self._ready = False
        self._err = ""

    @property
    def available(self) -> bool:
        return self._ready and self._model is not None

    def load(self) -> bool:
        if not oww_enabled():
            return False
        if self._ready:
            return self.available
        try:
            from openwakeword.model import Model
            names = oww_model_names()
            self._model = Model(wakeword_models=names)
            self._ready = True
            return True
        except Exception as e:
            self._err = str(e)
            self._ready = True
            return False

    def score_pcm(self, pcm_bytes: bytes) -> dict[str, float]:
        """16kHz mono int16 PCM → 모델별 최대 점수."""
        if not self.available:
            if not self.load():
                return {}
        arr = np.frombuffer(pcm_bytes, dtype=np.int16)
        if len(arr) < _OWW_CHUNK:
            return {}
        scores: dict[str, float] = {}
        model = self._model
        for i in range(0, len(arr) - _OWW_CHUNK + 1, _OWW_CHUNK):
            pred = model.predict(arr[i : i + _OWW_CHUNK])
            for k, v in pred.items():
                scores[k] = max(scores.get(k, 0.0), float(v))
        return scores

    def is_wake(self, scores: dict[str, float]) -> bool:
        if not scores:
            return False
        th = oww_threshold()
        return any(v >= th for v in scores.values())

    def best_label(self, scores: dict[str, float]) -> str:
        if not scores:
            return ""
        k, v = max(scores.items(), key=lambda x: x[1])
        return f"{k}({v:.2f})"


_oww_singleton: Optional[OpenWakeWordFrontEnd] = None


def get_oww_frontend() -> OpenWakeWordFrontEnd:
    global _oww_singleton
    if _oww_singleton is None:
        _oww_singleton = OpenWakeWordFrontEnd()
    return _oww_singleton


def detect_wake_from_pcm(
    pcm_bytes: bytes,
    *,
    stt_text: str = "",
    wake_words: list[str] | None = None,
) -> tuple[bool, str, str]:
    """
    PCM + (선택) Whisper STT 결과로 웨이크 판정.
    Returns: (detected, source, detail)
      source: openwakeword | whisper | none
    """
    mode = wake_engine_mode()
    if mode in ("off", "0", "false"):
        return False, "none", ""

    words = wake_words or default_wake_words()

    if mode in ("openwakeword", "oww", "hybrid", "both"):
        oww = get_oww_frontend()
        scores = oww.score_pcm(pcm_bytes) if oww_enabled() else {}
        if scores and oww.is_wake(scores):
            label = oww.best_label(scores)
            return True, "openwakeword", label

    if mode in ("whisper", "hybrid", "both", "openwakeword", "oww"):
        if stt_text and matches_wake_word(stt_text, words):
            return True, "whisper", normalize_wake_text(stt_text)

    return False, "none", ""
