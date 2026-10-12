"""
J.A.R.V.I.S — 완전 무료 모드 (유료 API 호출 차단)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
JARVIS_FREE_ONLY=1 이면 Gemini·OpenAI·Fish Audio·Tavily 등
유료/클라우드 API 대신 Mac 로컬·무료 공개 소스만 사용한다.

  LLM   → Ollama (로컬)
  STT   → mlx-whisper / faster-whisper (로컬)
  TTS   → macOS say + afplay
  검색  → DuckDuckGo HTML (API 키 불필요)
  뉴스  → yfinance + 로컬 키워드 감성 (Gemini 0)
"""
from __future__ import annotations

import os
import sys


def free_only_enabled() -> bool:
    return os.getenv("JARVIS_FREE_ONLY", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


_FREE_DEFAULTS = {
    "JARVIS_NO_GEMINI": "1",
    "JARVIS_PREFER_LOCAL_LLM": "1",
    "LLM_PROVIDER": "ollama",
    "JARVIS_STT_BACKEND": "local",
    "HYBRID_DEFAULT_TIER": "local",
    "GEMINI_CACHE_ENABLED": "0",
    "CV_CACHE_BACKGROUND_NO_GEMINI": "1",
}


def apply_free_mode_defaults() -> bool:
    """무료 모드 env 기본값 주입 (사용자 명시값은 보존)."""
    if not free_only_enabled():
        return False
    for key, val in _FREE_DEFAULTS.items():
        if not os.getenv(key):
            os.environ[key] = val
    return True


def use_paid_llm() -> bool:
    return not free_only_enabled()


def use_paid_tts() -> bool:
    if free_only_enabled():
        return False
    fish = os.getenv("FISH_AUDIO_API_KEY", "")
    voice = os.getenv("FISH_VOICE_ID", "")
    if not fish or fish == "STORED_IN_KEYCHAIN":
        return False
    if not voice or voice == "STORED_IN_KEYCHAIN":
        return False
    return True


def use_paid_search() -> bool:
    if free_only_enabled():
        return False
    key = os.getenv("TAVILY_API_KEY", "")
    return bool(key and key != "STORED_IN_KEYCHAIN")


def use_paid_stt() -> bool:
    if free_only_enabled():
        return False
    return os.getenv("JARVIS_STT_BACKEND", "auto").strip().lower() in (
        "api", "openai", "remote", "cloud",
    )


class LocalSttOpenAIStub:
    """웨이크/STT가 로컬 전용일 때 OpenAI 클라이언트 자리."""

    api_key = ""

    class audio:
        class transcriptions:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("OpenAI STT는 무료 모드에서 비활성화되어 있습니다.")


def openai_client_for_stt(real_key: str = ""):
    """STT 모드에 맞는 OpenAI 클라이언트 또는 스텁 반환."""
    mode = os.getenv("JARVIS_STT_BACKEND", "auto").strip().lower()
    if free_only_enabled() or mode in ("local", "whisper", "faster"):
        return LocalSttOpenAIStub()
    if real_key:
        from openai import OpenAI
        return OpenAI(api_key=real_key, max_retries=3, timeout=30.0)
    return LocalSttOpenAIStub()


def free_mode_summary() -> dict:
    return {
        "enabled": free_only_enabled(),
        "llm": f"ollama/{os.getenv('OLLAMA_MODEL', 'gemma4:latest')}",
        "stock_llm": f"ollama/{os.getenv('OLLAMA_STOCK_MODEL', 'qwen2.5:7b-instruct')}",
        "vision_llm": f"ollama/{os.getenv('OLLAMA_VISION_MODEL', 'llama3.2-vision')}",
        "stt": "local",
        "tts": "macos_say" if sys.platform == "darwin" else "none",
        "search": "duckduckgo",
        "news_scoring": "local_keywords",
    }
