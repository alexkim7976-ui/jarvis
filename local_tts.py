"""
J.A.R.V.I.S — macOS Local TTS (No API Key Required)

Two-stage synthesis pipeline:
  1) AVSpeechSynthesizer helper (jarvis_tts) — evaluates voice quality
     (premium > enhanced > default) to deterministically pick the most natural
     English voice (e.g., Daniel or Samantha) at natural speech rate.
  2) macOS `say` fallback — guarantees voice output even if the helper is unavailable.
"""
from __future__ import annotations

import os
import sys
import time
import shutil
import threading
import subprocess
from pathlib import Path
from datetime import datetime


_active_procs_lock = threading.Lock()
_active_procs: set[subprocess.Popen] = set()
_tts_shutting_down = threading.Event()


def stop_all_local_tts() -> None:
    """즉시 모든 진행 중인 TTS 합성(jarvis_tts, say) 프로세스를 종료하고 신규 합성을 차단한다."""
    _tts_shutting_down.set()
    with _active_procs_lock:
        procs = list(_active_procs)
        _active_procs.clear()
    for p in procs:
        try:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=0.15)
                except Exception:
                    p.kill()
        except Exception:
            pass


def _run_tracked_cmd(cmd: list[str], *, timeout: float) -> tuple[int, str]:
    if _tts_shutting_down.is_set():
        return -1, "shutting down"
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    with _active_procs_lock:
        _active_procs.add(proc)
    try:
        _, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, stderr or ""
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        raise
    finally:
        with _active_procs_lock:
            _active_procs.discard(proc)


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] 🔊 [LocalTTS] {msg}")


_PROJECT_DIR = Path(__file__).resolve().parent
_SWIFT_SRC = _PROJECT_DIR / "scripts" / "jarvis_tts.swift"

# 컴파일된 헬퍼/음성 품질 캐시 (프로세스 1회)
_swift_bin_cache: str | None = None
_swift_bin_checked = False


def macos_tts_available() -> bool:
    if sys.platform != "darwin":
        return False
    return shutil.which("say") is not None


def _av_enabled() -> bool:
    """AVSpeechSynthesizer 헬퍼 사용 여부 (기본 ON). JARVIS_TTS_USE_AV=0 이면 say만."""
    return os.getenv("JARVIS_TTS_USE_AV", "1").strip().lower() not in (
        "0", "false", "off", "no",
    )


def _tts_lang() -> str:
    explicit = (os.getenv("JARVIS_TTS_LANG") or os.getenv("JARVIS_LANG_LOCALE") or "").strip()
    if explicit:
        return explicit
    try:
        from i18n import get_lang_meta
        return str(get_lang_meta()["tts_locale"])
    except Exception:
        return "en-GB"


def _default_voice() -> str:
    explicit = (os.getenv("JARVIS_MAC_VOICE") or os.getenv("JARVIS_VOICE") or os.getenv("TTS_VOICE") or "").strip()
    if explicit:
        return explicit
    try:
        from i18n import get_lang_meta
        return str(get_lang_meta()["tts_voice"])
    except Exception:
        return "Daniel"


def _fallback_voices(primary: str) -> list[str]:
    out = [primary]
    try:
        from i18n import get_lang_meta
        for v in get_lang_meta().get("tts_fallbacks") or []:
            if v and v not in out:
                out.append(v)
    except Exception:
        pass
    return out


def _cache_dir() -> Path:
    """컴파일된 헬퍼/임시 파일을 둘 쓰기 가능한 디렉터리."""
    for cand in (
        os.getenv("JARVIS_TTS_BIN_DIR"),
        os.path.expanduser("~/Library/Caches/JARVIS/tts"),
        str(_PROJECT_DIR / ".tts_cache"),
    ):
        if not cand:
            continue
        try:
            p = Path(cand)
            p.mkdir(parents=True, exist_ok=True)
            # 쓰기 가능 확인
            test = p / ".w"
            test.write_text("1")
            test.unlink(missing_ok=True)
            return p
        except Exception:
            continue
    return _PROJECT_DIR


def _resolve_swift_bin() -> str | None:
    """AVSpeech 헬퍼 실행 파일 경로 확보 — 명시경로 → 번들 → 캐시 → (필요시) 컴파일."""
    global _swift_bin_cache, _swift_bin_checked
    if _swift_bin_checked:
        return _swift_bin_cache
    _swift_bin_checked = True

    if sys.platform != "darwin" or not _av_enabled():
        _swift_bin_cache = None
        return None

    cache_bin = _cache_dir() / "jarvis_tts"

    # 1) 미리 빌드된 바이너리 (env 지정 / 앱 번들 bin) — 소스보다 최신일 때만 사용
    for c in (os.getenv("JARVIS_TTS_BIN"), str(_PROJECT_DIR / "bin" / "jarvis_tts")):
        if c and Path(c).exists() and os.access(c, os.X_OK):
            try:
                if not _SWIFT_SRC.exists() or Path(c).stat().st_mtime >= _SWIFT_SRC.stat().st_mtime:
                    _swift_bin_cache = c
                    return _swift_bin_cache
            except Exception:
                _swift_bin_cache = c
                return _swift_bin_cache

    # 2) 캐시 바이너리 — 소스보다 최신일 때만 재사용, 아니면 재컴파일
    try:
        if cache_bin.exists() and os.access(cache_bin, os.X_OK) and (
            not _SWIFT_SRC.exists()
            or cache_bin.stat().st_mtime >= _SWIFT_SRC.stat().st_mtime
        ):
            _swift_bin_cache = str(cache_bin)
            return _swift_bin_cache
    except Exception:
        pass

    # 3) 런타임 컴파일 (swiftc 필요) — 실패 시 None → say 폴백
    _swift_bin_cache = _compile_swift()
    return _swift_bin_cache


def _compile_swift() -> str | None:
    if not _SWIFT_SRC.exists() or shutil.which("swiftc") is None:
        return None
    out = _cache_dir() / "jarvis_tts"
    try:
        r = subprocess.run(
            ["swiftc", "-O", "-o", str(out), str(_SWIFT_SRC)],
            capture_output=True, text=True, timeout=180,
        )
        if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
            _log("Natural voice engine (AVSpeech) ready.")
            return str(out)
        _log(f"AVSpeech engine compile failed (falling back to say): {(r.stderr or '')[:120]}")
    except Exception as e:
        _log(f"AVSpeech engine compile error (falling back to say): {e}")
    return None


def list_en_voices() -> list[tuple[str, str, str]]:
    """Installed English voices [(quality, name, identifier)] — for diagnostics."""
    binp = _resolve_swift_bin()
    if not binp:
        return []
    try:
        r = subprocess.run([binp, "--list", "--lang", _tts_lang()],
                           capture_output=True, text=True, timeout=15)
        rows = []
        for line in (r.stdout or "").splitlines():
            parts = line.split("\t")
            if len(parts) >= 4:
                rows.append((parts[0], parts[1], parts[3]))
        return rows
    except Exception:
        return []


def list_ko_voices() -> list[tuple[str, str, str]]:
    """Backward-compatible alias."""
    return list_en_voices()


def natural_en_voice_installed() -> bool:
    """Whether an enhanced/premium English voice is installed."""
    for q, _name, _id in list_en_voices():
        if q in ("premium", "enhanced"):
            return True
    return False


def natural_ko_voice_installed() -> bool:
    return natural_en_voice_installed()


def _av_rate() -> str | None:
    """AVSpeech 속도(0.0~1.0). 미설정/auto면 헬퍼가 기본(가장 자연스러운) 속도 사용."""
    r = os.getenv("JARVIS_TTS_RATE", "").strip().lower()
    if not r or r == "auto":
        return None
    return r


def _synth_av(text: str, out_path: Path, voice: str) -> str | None:
    if _tts_shutting_down.is_set():
        return None
    binp = _resolve_swift_bin()
    if not binp:
        return None
    cmd = [binp, "--out", str(out_path), "--voice", voice, "--lang", _tts_lang()]
    rate = _av_rate()
    if rate:
        cmd += ["--rate", rate]
    cmd += ["--text", text.strip()]
    try:
        rc, stderr = _run_tracked_cmd(
            cmd, timeout=float(os.getenv("JARVIS_SAY_TIMEOUT", "45")),
        )
        if rc == 0 and out_path.exists() and out_path.stat().st_size > 0:
            return str(out_path)
        if _tts_shutting_down.is_set():
            return None
        # 헬퍼 실패 → say 폴백
        if stderr.strip():
            _log(f"AVSpeech fallback to say: {stderr.strip()[:100]}")
        return None
    except subprocess.TimeoutExpired:
        _log("AVSpeech synthesis timed out -> falling back to say")
        return None
    except Exception as e:
        _log(f"AVSpeech synthesis error -> falling back to say: {e}")
        return None


def _synth_say(text: str, out_path: Path, voice: str) -> str | None:
    if _tts_shutting_down.is_set():
        return None
    if not macos_tts_available():
        _log("macOS say command is not available.")
        return None
    rate = os.getenv("JARVIS_MAC_SAY_RATE", "auto").strip().lower()
    timeout_sec = float(os.getenv("JARVIS_SAY_TIMEOUT", "45"))

    for cand_voice in _fallback_voices(voice):
        if _tts_shutting_down.is_set():
            return None
        cmd = ["say", "-v", cand_voice, "-o", str(out_path), text.strip()]
        if rate and rate not in ("auto", "natural", "default") and rate.isdigit():
            cmd = ["say", "-v", cand_voice, "-r", rate, "-o", str(out_path), text.strip()]
        try:
            rc, stderr = _run_tracked_cmd(cmd, timeout=timeout_sec)
            if rc == 0 and out_path.exists() and out_path.stat().st_size > 0:
                return str(out_path)
        except subprocess.TimeoutExpired:
            _log("say timed out")
            return None
        except Exception:
            continue

    # 최후 폴백: 시스템 기본 음성 (보이스 미지정)
    try:
        rc, stderr = _run_tracked_cmd(["say", "-o", str(out_path), text.strip()], timeout=timeout_sec)
        if rc == 0 and out_path.exists() and out_path.stat().st_size > 0:
            return str(out_path)
        if not _tts_shutting_down.is_set():
            _log(f"say error (rc={rc}): {stderr[:120]}")
    except Exception as e:
        _log(f"synthesis error: {e}")
    return None


def synthesize_macos(
    text: str,
    seq: int = 0,
    audio_dir: Path | None = None,
) -> str | None:
    """Synthesize speech to an audio file using macOS English voice.
    1st choice: AVSpeechSynthesizer helper -> fallback: macOS `say`."""
    if not text or not text.strip() or _tts_shutting_down.is_set():
        return None

    out_dir = audio_dir or Path(os.getenv("AUDIO_SAVE_DIR", "audio_cache"))
    out_dir.mkdir(exist_ok=True)
    voice = _default_voice()

    # 1) AVSpeech (preferred)
    av_path = out_dir / f"av_{int(time.time())}_{seq}.caf"
    got = _synth_av(text, av_path, voice)
    if got:
        return got

    # 2) say fallback
    say_path = out_dir / f"mac_{int(time.time())}_{seq}.aiff"
    return _synth_say(text, say_path, voice)


if __name__ == "__main__":
    print("macOS TTS available:", macos_tts_available())
    print("AVSpeech helper:", _resolve_swift_bin())
    voices = list_en_voices()
    if voices:
        print("Installed English voices (quality / name / identifier):")
        for q, name, ident in voices:
            mark = "  *" if q in ("premium", "enhanced") else ""
            print(f"  [{q:8}] {name:10} {ident}{mark}")
    print("Enhanced/Premium English voice installed:", natural_en_voice_installed())
