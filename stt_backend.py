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

_worker_model = None
_process_pool: ProcessPoolExecutor | None = None
_pool_lock = threading.Lock()


def _log(level: str, msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    icons = {"INFO": "◈", "OK": "✓", "WARN": "⚠", "ERR": "✗"}
    print(f"[{ts}] {icons.get(level, '·')} [STT] {msg}")


def resolve_stt_mode(openai_api_key: str = "") -> str:
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
    return resolve_stt_mode() == "local" and stt_process_enabled()


def should_preload_local_whisper() -> bool:
    return resolve_stt_mode() == "local"


def _worker_init():
    global _worker_model
    from whisper_pool import get_whisper_model
    _worker_model = get_whisper_model(role="main")


def _worker_transcribe(wav_path: str, language: str = "ko") -> str:
    global _worker_model
    if _worker_model is None:
        _worker_init()
    segments, _ = _worker_model.transcribe(wav_path, language=language)
    return " ".join(seg.text.strip() for seg in segments).strip()


def _get_process_pool() -> ProcessPoolExecutor:
    global _process_pool
    with _pool_lock:
        if _process_pool is None:
            _process_pool = ProcessPoolExecutor(max_workers=1, initializer=_worker_init)
        return _process_pool


def shutdown_process_pool() -> None:
    global _process_pool
    with _pool_lock:
        if _process_pool is not None:
            try:
                _process_pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                _process_pool.shutdown(wait=False)
            _process_pool = None


def transcribe_audio_api(wav_path: str, client, language: str = "ko") -> str:
    if client is None:
        _log("WARN", "OpenAI 클라이언트 없음 — API STT 불가")
        return ""
    try:
        with open(wav_path, "rb") as f:
            resp = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                response_format="text",
                language=language,
            )
        text = (resp if isinstance(resp, str) else getattr(resp, "text", "") or "").strip()
        if text:
            _log("OK", f"API 인식: {text}")
        return text
    except Exception as e:
        _log("ERR", f"API STT 실패: {e}")
        return ""


def transcribe_audio_local(wav_path: str, *, role: str = "main", language: str = "ko") -> str:
    try:
        from whisper_pool import get_whisper_model
        model = get_whisper_model(role=role)
        segments, _ = model.transcribe(wav_path, language=language)
        text = " ".join(seg.text.strip() for seg in segments).strip()
        if text:
            _log("OK", f"로컬 인식: {text}")
        return text
    except Exception as e:
        _log("ERR", f"로컬 STT 실패: {e}")
        return ""


def transcribe_audio(
    wav_path: str,
    openai_client=None,
    *,
    role: str = "main",
    language: str = "ko",
) -> str:
    mode = resolve_stt_mode(
        getattr(openai_client, "api_key", None) or os.getenv("OPENAI_API_KEY", "")
    )
    _log("INFO", f"STT ({mode}{'+proc' if mode == 'local' and use_process_pool() else ''})...")

    if mode == "api":
        text = transcribe_audio_api(wav_path, openai_client, language=language)
        if text:
            return text
        _log("WARN", "API STT 빈 결과/실패 → 로컬 폴백 시도")
        return transcribe_audio_local(wav_path, role=role, language=language)

    if use_process_pool():
        try:
            pool = _get_process_pool()
            return pool.submit(_worker_transcribe, wav_path, language).result(timeout=120)
        except Exception as e:
            _log("WARN", f"프로세스 STT 실패 → 인프로세스 폴백: {e}")

    return transcribe_audio_local(wav_path, role=role, language=language)


def transcribe_audio_async(
    wav_path: str,
    openai_client=None,
    on_done: Optional[Callable[[str], None]] = None,
    on_error: Optional[Callable[[Exception], None]] = None,
    *,
    role: str = "main",
    language: str = "ko",
) -> threading.Thread:
    def _run():
        try:
            text = transcribe_audio(
                wav_path, openai_client, role=role, language=language,
            )
            if on_done:
                on_done(text)
        except Exception as e:
            _log("ERR", f"비동기 STT: {e}")
            if on_error:
                on_error(e)
            elif on_done:
                on_done("")

    t = threading.Thread(target=_run, daemon=True, name="stt-async")
    t.start()
    return t


def transcribe_wake_clip(
    wav_path: str,
    openai_client,
    wake_words: list[str],
) -> tuple[bool, bool, str]:
    mode = resolve_stt_mode(
        getattr(openai_client, "api_key", None) or os.getenv("OPENAI_API_KEY", "")
    )

    if mode == "api":
        try:
            text = transcribe_audio_api(wav_path, openai_client, language="ko").lower()
            if not text:
                return False, True, text
            for w in wake_words:
                if w in text:
                    return True, True, text
            return False, True, text
        except Exception as e:
            _log("WARN", f"웨이크 API STT 실패: {e}")
            return False, False, ""

    if use_process_pool():
        try:
            pool = _get_process_pool()
            text = pool.submit(
                _worker_transcribe, wav_path, "ko",
            ).result(timeout=30).strip().lower()
            if not text:
                return False, True, text
            for w in wake_words:
                if w in text:
                    return True, True, text
            return False, True, text
        except Exception as e:
            _log("WARN", f"웨이크 프로세스 STT 실패 → 인프로세스 폴백: {e}")

    try:
        from whisper_pool import get_whisper_model
        model = get_whisper_model(role="wake")
        segments, _ = model.transcribe(
            wav_path, language="ko", beam_size=1, condition_on_previous_text=False,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip().lower()
        if not text:
            return False, True, text
        for w in wake_words:
            if w in text:
                return True, True, text
        return False, True, text
    except Exception as e:
        _log("WARN", f"웨이크 로컬 STT 실패: {e}")
        if openai_client:
            text = transcribe_audio_api(wav_path, openai_client, language="ko").lower()
            if not text:
                return False, True, text
            for w in wake_words:
                if w in text:
                    return True, True, text
            return False, True, text
        return False, False, ""
