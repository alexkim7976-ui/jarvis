"""성능 프로필(절전/균형/고품질) — Application Support .env 키만 갱신."""
from __future__ import annotations

import os
import re
from pathlib import Path

PRESET_ECO = "eco"
PRESET_BALANCED = "balanced"
PRESET_QUALITY = "quality"

PRESET_LABELS = {
    PRESET_ECO: "Eco",
    PRESET_BALANCED: "Balanced",
    PRESET_QUALITY: "High Quality",
}

# 프로필별 .env upsert (재시작 후 STT·카메라 해상도 완전 반영)
_PRESETS: dict[str, dict[str, str]] = {
    PRESET_ECO: {
        "JARVIS_PERF_PROFILE": "eco",
        "JARVIS_MAIN_DEVICE": "cpu",
        "OLLAMA_NUM_GPU": "0",
        "OLLAMA_NUM_CTX": "4096",
        "OLLAMA_NUM_PREDICT": "768",
        "JARVIS_CAM_WIDTH": "640",
        "JARVIS_CAM_HEIGHT": "480",
        "JARVIS_MAIN_LOOP_FPS": "20",
        "JARVIS_HAND_COMPLEXITY": "0",
        "JARVIS_HANDS_ENABLED": "0",
        "JARVIS_FACE_INTERVAL": "1.2",
        "JARVIS_WHISPER_MODEL": "small",
        "JARVIS_STT_PROCESS": "1",
        "JARVIS_LOW_MEM": "0",
    },
    PRESET_BALANCED: {
        "JARVIS_PERF_PROFILE": "balanced",
        "JARVIS_MAIN_DEVICE": "metal",
        "OLLAMA_NUM_GPU": "0",
        "OLLAMA_NUM_CTX": "4096",
        "OLLAMA_NUM_PREDICT": "1024",
        "JARVIS_CAM_WIDTH": "960",
        "JARVIS_CAM_HEIGHT": "540",
        "JARVIS_MAIN_LOOP_FPS": "24",
        "JARVIS_HAND_COMPLEXITY": "0",
        "JARVIS_HANDS_ENABLED": "0",
        "JARVIS_FACE_INTERVAL": "0.8",
        "JARVIS_WHISPER_MODEL": "small",
        "JARVIS_STT_PROCESS": "1",
        "JARVIS_LOW_MEM": "0",
    },
    PRESET_QUALITY: {
        "JARVIS_PERF_PROFILE": "quality",
        "JARVIS_MAIN_DEVICE": "metal",
        "OLLAMA_NUM_GPU": "-1",
        "OLLAMA_NUM_CTX": "8192",
        "OLLAMA_NUM_PREDICT": "1536",
        "JARVIS_CAM_WIDTH": "1280",
        "JARVIS_CAM_HEIGHT": "720",
        "JARVIS_MAIN_LOOP_FPS": "30",
        "JARVIS_HAND_COMPLEXITY": "1",
        "JARVIS_HANDS_ENABLED": "0",
        "JARVIS_FACE_INTERVAL": "0.5",
        "JARVIS_WHISPER_MODEL": "base",
        "JARVIS_STT_PROCESS": "1",
        "JARVIS_LOW_MEM": "0",
    },
}


def resolve_env_path() -> Path:
    data = os.getenv("JARVIS_DATA_DIR", "").strip()
    if data:
        p = Path(data).expanduser() / ".env"
        if p.is_file():
            return p
    for candidate in (
        Path(__file__).resolve().parent / ".env",
        Path.home() / "Library/Application Support/JARVIS/.env",
    ):
        if candidate.is_file():
            return candidate
    if data:
        return Path(data).expanduser() / ".env"
    return Path(__file__).resolve().parent / ".env"


def read_env_value(key: str, env_path: Path | None = None) -> str:
    path = env_path or resolve_env_path()
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""


def current_profile(env_path: Path | None = None) -> str:
    raw = read_env_value("JARVIS_PERF_PROFILE", env_path).lower()
    if raw in _PRESETS:
        return raw
    return PRESET_BALANCED


def upsert_env_keys(updates: dict[str, str], env_path: Path | None = None) -> Path:
    path = env_path or resolve_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        lines = ["# J.A.R.V.I.S"]

    def upsert(key: str, val: str) -> None:
        nonlocal lines
        prefix = f"{key}="
        for i, line in enumerate(lines):
            if line.startswith(prefix):
                lines[i] = f"{key}={val}"
                return
        lines.append(f"{key}={val}")

    for k, v in updates.items():
        upsert(k, v)
    text = "\n".join(lines).rstrip() + "\n"
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    for k, v in updates.items():
        os.environ[k] = v
    return path


def apply_preset(name: str, env_path: Path | None = None) -> str:
    key = (name or "").strip().lower()
    if key not in _PRESETS:
        return f"Unknown performance profile. ({', '.join(PRESET_LABELS.values())})"
    upsert_env_keys(_PRESETS[key], env_path)
    label = PRESET_LABELS[key]
    return (
        f"Performance profile saved as '{label}'. "
        f"Camera and speech recognition settings will take full effect after restarting JARVIS."
    )


def status_line(env_path: Path | None = None) -> str:
    prof = current_profile(env_path)
    return f"Performance: {PRESET_LABELS.get(prof, prof)}"


def interpret_performance_command(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if re.search(r"(?:성능\s*(?:프로필|모드|설정)?\s*(?:목록|상태|뭐|확인|보여)|performance\s*(?:profile|mode|status))", raw, re.I):
        return status_line() + " — Press P and use keys 1, 2, or 3 to switch profiles."
    m = re.search(
        r"(?:성능\s*(?:을|를)?|performance\s*(?:profile|mode)?\s*(?:to)?)\s*(절전|균형|고품질|에코|eco|balanced|quality|high\s*quality)",
        raw,
        re.I,
    )
    if m:
        token = m.group(1).lower().replace(" ", "")
        mapping = {
            "절전": PRESET_ECO,
            "에코": PRESET_ECO,
            "eco": PRESET_ECO,
            "균형": PRESET_BALANCED,
            "balanced": PRESET_BALANCED,
            "고품질": PRESET_QUALITY,
            "quality": PRESET_QUALITY,
            "highquality": PRESET_QUALITY,
        }
        return apply_preset(mapping.get(token, PRESET_BALANCED))
    if re.fullmatch(r"성능\s*(?:절전|균형|고품질)", raw):
        token = raw.split()[-1]
        mapping = {"절전": PRESET_ECO, "균형": PRESET_BALANCED, "고품질": PRESET_QUALITY}
        return apply_preset(mapping[token])
    return None
