"""
J.A.R.V.I.S — macOS 로컬 TTS (API 키 불필요)

두 단계 합성 경로:
  1) AVSpeechSynthesizer 헬퍼(jarvis_tts) — 음성 "품질"(premium > enhanced > default)까지
     보고 가장 Siri에 가까운 한국어 음성을 결정적으로 선택. 자연스러운 기본 속도 사용.
  2) macOS `say` 폴백 — 헬퍼를 못 쓰는 환경에서도 최소한의 음성 보장.

가장 Siri 같은 소리를 내려면 '향상된/프리미엄' 한국어 음성(예: Yuna) 설치가 필요하다:
  시스템 설정 → 손쉬운 사용 → 말하기 콘텐츠 → 시스템 음성 → 음성 관리…
  → 한국어(대한민국) → Yuna(향상된/프리미엄) 다운로드.
설치되면 헬퍼가 자동으로 그 음성을 선택한다.
"""
from __future__ import annotations

import os
import sys
import time
import shutil
import subprocess
from pathlib import Path
from datetime import datetime


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] 🔊 [로컬TTS] {msg}")


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

    # 1) 미리 빌드된 바이너리 (env 지정 / 앱 번들 bin) — 그대로 사용
    for c in (os.getenv("JARVIS_TTS_BIN"), str(_PROJECT_DIR / "bin" / "jarvis_tts")):
        if c and Path(c).exists() and os.access(c, os.X_OK):
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
            _log("자연스러운 음성 엔진(AVSpeech) 준비 완료.")
            return str(out)
        _log(f"AVSpeech 엔진 컴파일 실패(→ say 폴백): {(r.stderr or '')[:120]}")
    except Exception as e:
        _log(f"AVSpeech 엔진 컴파일 예외(→ say 폴백): {e}")
    return None


def list_ko_voices() -> list[tuple[str, str, str]]:
    """설치된 한국어 음성 목록 [(quality, name, identifier)] — 진단용."""
    binp = _resolve_swift_bin()
    if not binp:
        return []
    try:
        r = subprocess.run([binp, "--list", "--lang", "ko-KR"],
                           capture_output=True, text=True, timeout=15)
        rows = []
        for line in (r.stdout or "").splitlines():
            parts = line.split("\t")
            if len(parts) >= 4:
                rows.append((parts[0], parts[1], parts[3]))
        return rows
    except Exception:
        return []


def natural_ko_voice_installed() -> bool:
    """향상된/프리미엄 한국어 음성이 하나라도 설치돼 있는지 (Siri급 자연스러움 가능 여부)."""
    for q, _name, _id in list_ko_voices():
        if q in ("premium", "enhanced"):
            return True
    return False


def _av_rate() -> str | None:
    """AVSpeech 속도(0.0~1.0). 미설정/auto면 헬퍼가 기본(가장 자연스러운) 속도 사용."""
    r = os.getenv("JARVIS_TTS_RATE", "").strip().lower()
    if not r or r == "auto":
        return None
    return r


def _synth_av(text: str, out_path: Path, voice: str) -> str | None:
    binp = _resolve_swift_bin()
    if not binp:
        return None
    cmd = [binp, "--out", str(out_path), "--voice", voice, "--lang", "ko-KR"]
    rate = _av_rate()
    if rate:
        cmd += ["--rate", rate]
    cmd += ["--text", text.strip()]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=float(os.getenv("JARVIS_SAY_TIMEOUT", "45")),
        )
        if r.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
            return str(out_path)
        # 헬퍼 실패 → say 폴백
        if (r.stderr or "").strip():
            _log(f"AVSpeech 합성 폴백(say): {(r.stderr or '').strip()[:100]}")
        return None
    except subprocess.TimeoutExpired:
        _log("AVSpeech 합성 타임아웃 → say 폴백")
        return None
    except Exception as e:
        _log(f"AVSpeech 합성 예외 → say 폴백: {e}")
        return None


def _synth_say(text: str, out_path: Path, voice: str) -> str | None:
    if not macos_tts_available():
        _log("macOS say 명령을 사용할 수 없습니다.")
        return None
    rate = os.getenv("JARVIS_MAC_SAY_RATE", "auto").strip().lower()
    cmd = ["say", "-v", voice, "-o", str(out_path), text.strip()]
    if rate and rate not in ("auto", "natural", "default") and rate.isdigit():
        cmd = ["say", "-v", voice, "-r", rate, "-o", str(out_path), text.strip()]
    try:
        subprocess.run(cmd, check=True, capture_output=True,
                       timeout=float(os.getenv("JARVIS_SAY_TIMEOUT", "45")))
        if out_path.exists() and out_path.stat().st_size > 0:
            return str(out_path)
        _log("say 출력 파일이 비어 있습니다.")
        return None
    except subprocess.TimeoutExpired:
        _log("say 타임아웃")
        return None
    except subprocess.CalledProcessError as e:
        err = (e.stderr or b"").decode(errors="replace")[:120]
        _log(f"say 오류: {err}")
        return None
    except Exception as e:
        _log(f"합성 오류: {e}")
        return None


def synthesize_macos(
    text: str,
    seq: int = 0,
    audio_dir: Path | None = None,
) -> str | None:
    """가장 자연스러운(Siri급) macOS 음성으로 오디오 파일 생성.
    1순위 AVSpeechSynthesizer 헬퍼(품질 최상 음성 자동 선택) → 실패 시 say 폴백."""
    if not text or not text.strip():
        return None

    out_dir = audio_dir or Path(os.getenv("AUDIO_SAVE_DIR", "audio_cache"))
    out_dir.mkdir(exist_ok=True)
    voice = (os.getenv("JARVIS_MAC_VOICE", "Yuna").strip() or "Yuna")

    # 1) AVSpeech (권장)
    av_path = out_dir / f"av_{int(time.time())}_{seq}.caf"
    got = _synth_av(text, av_path, voice)
    if got:
        return got

    # 2) say 폴백
    say_path = out_dir / f"mac_{int(time.time())}_{seq}.aiff"
    return _synth_say(text, say_path, voice)


if __name__ == "__main__":
    # 진단: 설치된 한국어 음성/품질 표시
    print("macOS TTS 사용 가능:", macos_tts_available())
    print("AVSpeech 헬퍼:", _resolve_swift_bin())
    voices = list_ko_voices()
    if voices:
        print("설치된 한국어 음성 (품질/이름/식별자):")
        for q, name, ident in voices:
            mark = "  ⭐" if q in ("premium", "enhanced") else ""
            print(f"  [{q:8}] {name:10} {ident}{mark}")
    print("자연스러운(향상/프리미엄) 한국어 음성 설치됨:", natural_ko_voice_installed())
