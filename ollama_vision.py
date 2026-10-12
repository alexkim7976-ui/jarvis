"""
J.A.R.V.I.S — Ollama 로컬 비전 (차트 이미지 분석, Gemini 0)
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

import requests

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "moondream")
OLLAMA_VISION_TIMEOUT = float(os.getenv("OLLAMA_VISION_TIMEOUT", "90"))
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# model -> True | False (실제 추론 프로브 결과)
_probe_cache: dict[str, bool] = {}
_resolved_model: str | None = None


def _vision_options() -> dict:
    try:
        from ai_accuracy import ollama_accuracy_options
        return ollama_accuracy_options({"temperature": float(os.getenv("OLLAMA_VISION_TEMPERATURE", "0.05"))})
    except ImportError:
        num_ctx = int(os.getenv("OLLAMA_VISION_NUM_CTX", os.getenv("OLLAMA_NUM_CTX", "8192")))
        num_gpu = int(os.getenv("OLLAMA_NUM_GPU", "-1"))
        opts = {"num_ctx": num_ctx, "temperature": 0.05}
        if num_gpu != 0:
            opts["num_gpu"] = num_gpu
        return opts


CHART_ANALYSIS_USER_PROMPT = """이 차트를 옆에서 편하게 설명하듯 분석해 주세요.

캔들·추세, 이평선, 지지·저항, 매수/관망/매도 중 하나.
말투: 부드럽고 자연스러운 한국어 존댓말/해요체 3~5문장. 반말 금지, 딱딱한 ~습니다체·번역투·번호 목록도 금지.
차트가 안 보이면 추측 말고 다시 보여달라고만 해 주세요."""


def ollama_available() -> bool:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=1.5)
        return r.status_code == 200
    except Exception:
        return False


def list_installed_models() -> set[str]:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=3)
        if r.status_code != 200:
            return set()
        names = set()
        for m in r.json().get("models") or []:
            name = m.get("name") or ""
            if name:
                names.add(name)
                if ":" in name:
                    names.add(name.split(":")[0])
        return names
    except Exception:
        return set()


def _is_mllama_error(text: str) -> bool:
    t = (text or "").lower()
    return "mllama" in t or "unknown model architecture" in t


def humanize_vision_error(raw: str, model: str = "") -> str:
    """사용자에게 보여줄 한국어 안내 (JSON 원문 숨김)."""
    if _is_mllama_error(raw):
        return (
            f"차트 비전 모델({model or 'llama3.2-vision'})이 현재 Ollama와 호환되지 않습니다. "
            "Ollama 앱을 최신으로 업데이트하거나, 터미널에서 "
            "ollama pull moondream 후 .env 의 OLLAMA_VISION_MODEL=moondream 으로 바꿔 주세요. "
            "지금은 종목명(예: 키움증권)을 말씀해 주시면 RSI·MACD 등 지표를 텍스트로 분석해 드리겠습니다."
        )
    if "404" in raw or "not found" in raw.lower():
        return (
            f"비전 모델 '{model}' 을 찾을 수 없습니다. "
            f"터미널: ollama pull {model or 'moondream'}"
        )
    if len(raw) > 160:
        return raw[:160] + "…"
    return raw or "로컬 차트 분석에 실패했습니다."


def _vision_candidates(preferred: str | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        n = (name or "").strip()
        if not n or n in seen:
            return
        seen.add(n)
        out.append(n)

    _add(preferred or OLLAMA_VISION_MODEL)
    for part in os.getenv(
        "OLLAMA_VISION_FALLBACK_MODELS",
        "moondream,llava:7b,llama3.2-vision",
    ).split(","):
        _add(part)
    return out


def probe_vision_model(model: str, *, force: bool = False) -> tuple[bool, str]:
    """실제 /api/chat 1회 호출로 모델 로드 가능 여부 확인."""
    if not force and model in _probe_cache:
        return _probe_cache[model], ("ok" if _probe_cache[model] else "cached fail")

    if not ollama_available():
        return False, "Ollama 미실행"

    installed = list_installed_models()
    base = model.split(":")[0] if ":" in model else model
    if not (
        model in installed
        or base in installed
        or any(x.startswith(base) for x in installed)
    ):
        _probe_cache[model] = False
        return False, f"not installed: {model}"

    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "messages": [{
                    "role": "user",
                    "content": "ok",
                    "images": [_TINY_PNG_B64],
                }],
                "stream": False,
                "options": {"num_predict": 8, "temperature": 0},
            },
            timeout=min(45.0, OLLAMA_VISION_TIMEOUT),
        )
        if r.status_code == 200 and (r.json().get("message") or {}).get("content") is not None:
            _probe_cache[model] = True
            return True, "ok"
        err = (r.text or "")[:400]
        _probe_cache[model] = False
        return False, err
    except requests.Timeout:
        _probe_cache[model] = False
        return False, "timeout"
    except Exception as e:
        _probe_cache[model] = False
        return False, str(e)


def resolve_working_vision_model(preferred: str | None = None) -> tuple[str | None, str]:
    """설치·프로브 통과한 첫 비전 모델."""
    global _resolved_model
    if _resolved_model and _probe_cache.get(_resolved_model):
        return _resolved_model, _resolved_model

    last_err = ""
    for model in _vision_candidates(preferred):
        ok, msg = probe_vision_model(model)
        if ok:
            _resolved_model = model
            return model, model
        last_err = msg
    return None, last_err


def vision_model_ready(model: str | None = None) -> tuple[bool, str]:
    """(ready, message) — 태그 존재 + 프로브 통과."""
    m = model or OLLAMA_VISION_MODEL
    if not ollama_available():
        return False, "Ollama가 실행 중이 아닙니다. Ollama 앱을 켜 주세요."
    resolved, err = resolve_working_vision_model(m)
    if resolved:
        return True, resolved
    if _is_mllama_error(err):
        return False, humanize_vision_error(err, m)
    return False, (
        f"비전 모델을 사용할 수 없습니다. "
        f"ollama pull moondream 후 OLLAMA_VISION_MODEL=moondream 권장. ({err[:80]})"
    )


def _encode_image(path: str) -> tuple[str, str]:
    p = Path(path)
    suffix = p.suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix, "image/png")
    data = base64.b64encode(p.read_bytes()).decode("ascii")
    return data, mime


def analyze_chart_with_ollama(
    image_path: str,
    *,
    system_prompt: str = "",
    user_prompt: str = CHART_ANALYSIS_USER_PROMPT,
    model: str | None = None,
) -> dict:
    """
    Ollama 비전 모델로 차트 PNG/JPEG 분석.

    Returns:
        {"success": bool, "text": str, "model": str, "message": str}
    """
    resolved, err = resolve_working_vision_model(model)
    if not resolved:
        return {
            "success": False,
            "text": "",
            "model": model or OLLAMA_VISION_MODEL,
            "message": humanize_vision_error(err, model or OLLAMA_VISION_MODEL),
        }

    if not Path(image_path).is_file():
        return {
            "success": False,
            "text": "",
            "model": resolved,
            "message": f"이미지 파일을 찾을 수 없습니다: {image_path}",
        }

    try:
        b64, _mime = _encode_image(image_path)
    except Exception as e:
        return {"success": False, "text": "", "model": resolved,
                "message": f"이미지 읽기 실패: {e}"}

    messages: list[dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({
        "role": "user",
        "content": user_prompt,
        "images": [b64],
    })

    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": resolved,
                "messages": messages,
                "stream": False,
                "options": _vision_options(),
            },
            timeout=OLLAMA_VISION_TIMEOUT,
        )
        if r.status_code != 200:
            err = (r.text or "")[:400]
            if _is_mllama_error(err):
                _probe_cache[resolved] = False
                global _resolved_model
                _resolved_model = None
                retry, _ = resolve_working_vision_model()
                if retry and retry != resolved:
                    return analyze_chart_with_ollama(
                        image_path,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        model=retry,
                    )
            return {
                "success": False,
                "text": "",
                "model": resolved,
                "message": humanize_vision_error(err, resolved),
            }
        text = (r.json().get("message") or {}).get("content", "").strip()
        if not text:
            return {
                "success": False,
                "text": "",
                "model": resolved,
                "message": "비전 모델이 빈 응답을 반환했습니다. 차트를 더 가까이·밝게 보여 주세요.",
            }
        return {"success": True, "text": text, "model": resolved, "message": "ok"}
    except requests.Timeout:
        return {
            "success": False,
            "text": "",
            "model": resolved,
            "message": "차트 분석 시간 초과 — 이미지를 더 작게 하거나 OLLAMA_VISION_TIMEOUT 을 늘려 보세요.",
        }
    except Exception as e:
        return {"success": False, "text": "", "model": resolved,
                "message": humanize_vision_error(str(e), resolved)}
