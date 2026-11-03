"""출시 앱 — Ollama 설치·실행·모델 pull 안내."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


def find_ollama_binary() -> Path | None:
    for name in ("ollama",):
        p = shutil.which(name)
        if p:
            return Path(p)
    app = Path("/Applications/Ollama.app/Contents/Resources/ollama")
    if app.is_file():
        return app
    return None


def ollama_api_ok(url: str | None = None, timeout: float = 2.5) -> bool:
    base = (url or os.getenv("OLLAMA_URL", "http://localhost:11434")).rstrip("/")
    try:
        req = urllib.request.Request(f"{base}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def list_local_models(url: str | None = None) -> list[str]:
    base = (url or os.getenv("OLLAMA_URL", "http://localhost:11434")).rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=4) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


def diagnose_ollama() -> dict:
    """마법사·로그용 상태."""
    bin_path = find_ollama_binary()
    url = os.getenv("OLLAMA_URL", "http://localhost:11434")
    api = ollama_api_ok(url)
    models = list_local_models(url) if api else []
    lines: list[str] = []
    if bin_path:
        lines.append(f"✓ Ollama 실행 파일: {bin_path}")
    else:
        lines.append("✗ Ollama CLI 없음 — https://ollama.com 에서 Ollama.app 설치")
    if api:
        lines.append(f"✓ Ollama API 응답 ({url})")
        if models:
            lines.append(f"  로컬 모델: {', '.join(models[:6])}" + (" …" if len(models) > 6 else ""))
        else:
            lines.append("  로컬 모델 없음 — 아래 pull 명령 실행")
    else:
        lines.append("✗ Ollama API 미응답 — Ollama.app 을 실행한 뒤 다시 시도")
        if bin_path and Path("/Applications/Ollama.app").is_dir():
            lines.append("  힌트: Spotlight에서 Ollama 실행 또는 터미널: open -a Ollama")
    return {
        "binary": str(bin_path) if bin_path else "",
        "api_ok": api,
        "models": models,
        "lines": lines,
    }


def pull_instructions(primary_model: str = "qwen2.5:7b-instruct") -> str:
    vision = os.getenv("OLLAMA_VISION_MODEL", "moondream")
    return (
        f"터미널에서:\n"
        f"  ollama pull {primary_model}\n"
        f"  ollama pull {vision}\n"
        f"(선택) ollama serve — 보통 Ollama.app 이 대신 실행합니다."
    )


def try_open_ollama_app() -> bool:
    if not Path("/Applications/Ollama.app").is_dir():
        return False
    try:
        subprocess.Popen(
            ["open", "-g", "-a", "Ollama"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except OSError:
        return False
