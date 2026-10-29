"""
J.A.R.V.I.S — STT 백엔드 (로컬 Whisper / OpenAI API / 서브프로세스)

환경변수:
  JARVIS_STT_BACKEND   auto | local | api
  JARVIS_STT_PROCESS   1 — local 모드에서 Whisper 추론만 별도 ProcessPool 워커로 실행
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from typing import Callable, Optional

from resource_profile import is_low_mem, stt_process_enabled
from runtime_backtrack import describe_error, main_uses_cpu

_worker_model = None
_process_pool: ProcessPoolExecutor | None = None
_wake_process_pool: ProcessPoolExecutor | None = None
_pool_lock = threading.Lock()
_stt_last_warn = 0.0
_stt_warn_interval = 30.0
_process_disabled = False


def _log(level: str, msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    icons = {"INFO": "◈", "OK": "✓", "WARN": "⚠", "ERR": "✗"}
    print(f"[{ts}] {icons.get(level, '·')} [STT] {msg}")


def _log_warn_throttled(msg: str) -> None:
    global _stt_last_warn
    now = __import__("time").time()
    if now - _stt_last_warn >= _stt_warn_interval:
        _stt_last_warn = now
        _log("WARN", msg)


def _power_save_stt_off() -> bool:
    try:
        from power_save_mode import is_active
        return is_active()
    except ImportError:
        return os.getenv("JARVIS_POWER_SAVE", "").strip().lower() in (
            "1", "true", "yes", "on",
        )


def resolve_stt_mode(openai_api_key: str = "") -> str:
    if _power_save_stt_off():
        return "off"
    try:
        from free_mode import free_only_enabled
        if free_only_enabled():
            return "local"
    except ImportError:
        pass
    mode = os.getenv("JARVIS_STT_BACKEND", "auto").strip().lower()
    if mode in ("api", "openai", "remote", "cloud"):
        return "api"
    if mode in ("local", "whisper", "faster"):
        return "local"
    key = openai_api_key or os.getenv("OPENAI_API_KEY", "")
    if is_low_mem() and key:
        return "api"
    return "local"


def use_process_pool() -> bool:
    if _process_disabled or main_uses_cpu():
        return False
    return resolve_stt_mode() == "local" and stt_process_enabled()


def should_preload_local_whisper() -> bool:
    if _power_save_stt_off():
        return False
    return resolve_stt_mode() == "local"


def _stt_lang() -> str:
    raw = (os.getenv("JARVIS_STT_LANG") or os.getenv("JARVIS_LANG") or "en").strip().lower()
    if raw.startswith("en"):
        return "en"
    if raw.startswith("ko"):
        return "ko"
    return raw or "en"


def _worker_init():
    global _worker_model
    from whisper_pool import get_whisper_model
    _worker_model = get_whisper_model(role="main")


def _wake_worker_init():
    global _worker_model
    from whisper_pool import get_whisper_model
    _worker_model = get_whisper_model(role="wake")


def _worker_transcribe(wav_path: str, language: str | None = None) -> str:
    global _worker_model
    if _worker_model is None:
        _worker_init()
    lang = language or _stt_lang()
    segments, _ = _worker_model.transcribe(wav_path, language=lang)
    return " ".join(seg.text.strip() for seg in segments).strip()


def _get_process_pool() -> ProcessPoolExecutor:
    global _process_pool
    with _pool_lock:
        if _process_pool is None:
            _process_pool = ProcessPoolExecutor(max_workers=1, initializer=_worker_init)
        return _process_pool


def _get_wake_process_pool() -> ProcessPoolExecutor:
    global _wake_process_pool
    with _pool_lock:
        if _wake_process_pool is None:
            _wake_process_pool = ProcessPoolExecutor(max_workers=1, initializer=_wake_worker_init)
        return _wake_process_pool


def shutdown_process_pool() -> None:
    global _process_pool, _wake_process_pool
    with _pool_lock:
        for pool in (_process_pool, _wake_process_pool):
            if pool is not None:
                try:
                    pool.shutdown(wait=False, cancel_futures=True)
                except TypeError:
                    pool.shutdown(wait=False)
        _process_pool = None
        _wake_process_pool = None


def _retire_process_pool() -> None:
    """워커가 빈 오류로 죽으면 풀을 버리고, 이후에는 맥 CPU에서만 인식한다."""
    global _process_disabled
    _process_disabled = True
    shutdown_process_pool()


def transcribe_audio_api(wav_path: str, client, language: str | None = None) -> str:
    if client is None:
        _log("WARN", "No OpenAI client — API STT unavailable")
        return ""
    lang = language or _stt_lang()
    try:
        with open(wav_path, "rb") as f:
            resp = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="text",
                language=lang,
            )
        text = (resp if isinstance(resp, str) else getattr(resp, "text", "") or "").strip()
        if text:
            _log("OK", f"API transcript: {text}")
        return text
    except Exception as e:
        _log("ERR", f"API STT failed: {e}")
        return ""


def transcribe_audio_local(wav_path: str, *, role: str = "main", language: str | None = None) -> str:
    from whisper_pool import get_whisper_model, invalidate_mlx_repo, _mlx_repo, _resolve_size

    lang = language or _stt_lang()
    for attempt in range(2):
        try:
            model = get_whisper_model(role=role)
            segments, _ = model.transcribe(wav_path, language=lang)
            text = " ".join(seg.text.strip() for seg in segments).strip()
            if text:
                _log("OK", f"Local transcript: {text}")
            return text
        except Exception as e:
            if attempt == 0:
                size = _resolve_size(role, None)
                invalidate_mlx_repo(_mlx_repo(size))
                continue
            _log("ERR", f"Local STT failed: {e}")
            return ""
    return ""


def transcribe_audio(
    wav_path: str,
    openai_client=None,
    *,
    role: str = "main",
    language: str | None = None,
) -> str:
    lang = language or _stt_lang()
    mode = resolve_stt_mode(
        getattr(openai_client, "api_key", None) or os.getenv("OPENAI_API_KEY", "")
    )
    _log("INFO", f"STT ({mode}{'+proc' if mode == 'local' and use_process_pool() else ''})...")

    if mode == "off":
        _log("INFO", "Power save mode — Whisper/STT disabled (use text input)")
        return ""

    if mode == "api":
        text = transcribe_audio_api(wav_path, openai_client, language=lang)
        if text:
            return text
        _log("WARN", "API STT empty/failed -> trying local fallback")
        return transcribe_audio_local(wav_path, role=role, language=lang)

    if use_process_pool():
        try:
            pool = _get_process_pool()
            return pool.submit(_worker_transcribe, wav_path, lang).result(timeout=120)
        except Exception as e:
            _retire_process_pool()
            _log_warn_throttled(f"Process STT failed -> falling back to Mac CPU: {describe_error(e)}")

    return transcribe_audio_local(wav_path, role=role, language=lang)


def transcribe_audio_async(
    wav_path: str,
    openai_client=None,
    on_done: Optional[Callable[[str], None]] = None,
    on_error: Optional[Callable[[Exception], None]] = None,
    *,
    role: str = "main",
    language: str | None = None,
) -> threading.Thread:
    lang = language or _stt_lang()
    def _run():
        try:
            text = transcribe_audio(
                wav_path, openai_client, role=role, language=lang,
            )
            if on_done:
                on_done(text)
        except Exception as e:
            _log("ERR", f"Async STT: {e}")
            if on_error:
                on_error(e)
            elif on_done:
                on_done("")

    t = threading.Thread(target=_run, daemon=True, name="stt-async")
    t.start()
    return t


def _wake_text_matches(text: str, wake_words: list[str]) -> bool:
    try:
        from wake_engine import matches_wake_word
        return matches_wake_word(text, wake_words)
    except ImportError:
        t = (text or "").lower()
        return any(w in t for w in wake_words)


def transcribe_wake_clip(
    wav_path: str,
    openai_client,
    wake_words: list[str],
) -> tuple[bool, bool, str]:
    mode = resolve_stt_mode(
        getattr(openai_client, "api_key", None) or os.getenv("OPENAI_API_KEY", "")
    )
    lang = _stt_lang()

    if mode == "api":
        try:
            text = transcribe_audio_api(wav_path, openai_client, language=lang).lower()
            if not text:
                return False, True, text
            if _wake_text_matches(text, wake_words):
                return True, True, text
            return False, True, text
        except Exception as e:
            _log("WARN", f"Wake API STT failed: {e}")
            return False, False, ""

    if use_process_pool():
        try:
            pool = _get_wake_process_pool()
            text = pool.submit(
                _worker_transcribe, wav_path, lang,
            ).result(timeout=30).strip().lower()
            if not text:
                return False, True, text
            if _wake_text_matches(text, wake_words):
                return True, True, text
            return False, True, text
        except Exception as e:
            _retire_process_pool()
            _log_warn_throttled(f"Wake process STT failed -> falling back to Mac CPU: {describe_error(e)}")

    try:
        from whisper_pool import get_whisper_model
        model = get_whisper_model(role="wake")
        segments, _ = model.transcribe(
            wav_path, language=lang, beam_size=1, condition_on_previous_text=False,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip().lower()
        if not text:
            return False, True, text
        if _wake_text_matches(text, wake_words):
            return True, True, text
        return False, True, text
    except Exception as e:
        _log("WARN", f"Wake local STT failed: {e}")
        if openai_client:
            text = transcribe_audio_api(wav_path, openai_client, language=lang).lower()
            if not text:
                return False, True, text
            if _wake_text_matches(text, wake_words):
                return True, True, text
            return False, True, text
        return False, False, ""
