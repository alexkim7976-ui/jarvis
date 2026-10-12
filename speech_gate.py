"""
J.A.R.V.I.S — 전역 음성 출력 게이트 (한 번에 하나만 말하기)
────────────────────────────────────────────────────────────
모든 음성 출력 경로(speak_text / emergency_voice_briefing / 시작 인사 등)는
begin_speech() → (재생) → end_speech() 로 이 세마포어를 공유한다.
동시에 두 발화가 재생돼 목소리가 겹치는 것을 원천 차단한다.
"""
from __future__ import annotations

import threading
import time

_speech_sem = threading.Semaphore(1)
_state_lock = threading.Lock()
_holders = 0            # 현재 게이트를 점유한(=말하는 중이거나 직전) 발화 수


def begin_speech(*, wait: bool = True, timeout: float = 180.0) -> bool:
    """음성 출력 시작 — 다른 발화가 끝날 때까지 대기 후 게이트 점유."""
    if wait:
        ok = _speech_sem.acquire(timeout=timeout)
    else:
        ok = _speech_sem.acquire(blocking=False)
    if ok:
        global _holders
        with _state_lock:
            _holders += 1
    return ok


def end_speech() -> None:
    """게이트 반환 — begin_speech 로 점유한 발화가 끝났을 때만 호출."""
    global _holders
    with _state_lock:
        if _holders <= 0:
            return
        _holders -= 1
    try:
        _speech_sem.release()
    except ValueError:
        pass


def speech_active() -> bool:
    """현재 누군가 말하는 중(또는 게이트를 잡고 시작 직전)인지."""
    with _state_lock:
        return _holders > 0


def reset_speech_gate() -> None:
    """상태머신 워치독 전용 — 고착 시 게이트를 강제 초기화(반드시 재생 중단 후 호출)."""
    global _speech_sem, _holders
    with _state_lock:
        _holders = 0
        _speech_sem = threading.Semaphore(1)


def wait_audio_idle(
    *,
    tts_pipeline=None,
    player=None,
    timeout: float = 180.0,
) -> bool:
    """재생 중인 오디오가 모두 끝날 때까지 대기."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        busy = False
        if tts_pipeline is not None and getattr(tts_pipeline, "is_playing", False):
            busy = True
        if player is not None and getattr(player, "is_playing", False):
            busy = True
        if not busy:
            return True
        time.sleep(0.05)
    return False
