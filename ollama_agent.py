"""
J.A.R.V.I.S — Ollama 로컬 에이전트 (Mac Metal GPU, Gemini 0)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ollama /api/chat + tools 로 Jarvis 도구 호출을 로컬에서 수행한다.
"""
from __future__ import annotations

from speech_style import USER_DISPLAY_NAME
import json
import os
from typing import Any, Callable

import requests

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:latest")
OLLAMA_STOCK_MODEL = os.getenv(
    "OLLAMA_STOCK_MODEL",
    os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct"),
)
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "llama3.2-vision")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "4096"))
OLLAMA_NUM_GPU = int(os.getenv("OLLAMA_NUM_GPU", "-1"))
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_CHAT_TIMEOUT", "120"))


_stock_model_cache: dict[str, str] = {}


def _model_installed(model: str) -> bool:
    """Ollama 에 해당 모델이 설치돼 있는지. 확인 불가(서버 다운 등) 시 True(설정값 신뢰)."""
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=2.0)
        if r.status_code != 200:
            return True
        names: set[str] = set()
        for m in r.json().get("models") or []:
            n = (m.get("name") or "").strip()
            if n:
                names.add(n)
                names.add(n.split(":")[0])
        return model in names or model.split(":")[0] in names
    except Exception:
        return True


def get_stock_model() -> str:
    """주식 내러티브·멀티에이전트용 모델.

    설정(OLLAMA_STOCK_MODEL)이 미설치면 대화 모델(OLLAMA_MODEL)로 안전 폴백한다.
    결과는 설정값 기준으로 1회 캐시 (핫패스 네트워크 비용 방지)."""
    want = (os.getenv("OLLAMA_STOCK_MODEL") or os.getenv("OLLAMA_MODEL") or OLLAMA_STOCK_MODEL).strip()
    if want in _stock_model_cache:
        return _stock_model_cache[want]
    fallback = (os.getenv("OLLAMA_MODEL") or OLLAMA_MODEL or "qwen2.5:7b-instruct").strip()
    resolved = want
    if want and want != fallback and not _model_installed(want):
        resolved = fallback
    _stock_model_cache[want] = resolved
    return resolved


def get_vision_model() -> str:
    return OLLAMA_VISION_MODEL


def ollama_available() -> bool:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=1.5)
        return r.status_code == 200
    except Exception:
        return False


def model_supports_tools(model: str | None = None) -> bool:
    """Ollama 모델 tools 지원 여부 (gemma3 등 미지원 모델 방지)."""
    m = (model or OLLAMA_MODEL).lower()
    if "gemma3" in m and "gemma4" not in m:
        return False
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model or OLLAMA_MODEL,
                "messages": [{"role": "user", "content": "ping"}],
                "tools": [{"type": "function", "function": {
                    "name": "ping", "description": "ping",
                    "parameters": {"type": "object", "properties": {}},
                }}],
                "stream": False,
            },
            timeout=15,
        )
        if r.status_code != 200:
            return False
        return "does not support tools" not in (r.text or "").lower()
    except Exception:
        return False


def _ollama_options() -> dict:
    try:
        from ai_accuracy import ollama_accuracy_options
        return ollama_accuracy_options()
    except ImportError:
        opts = {"num_ctx": OLLAMA_NUM_CTX}
        if OLLAMA_NUM_GPU != 0:
            opts["num_gpu"] = OLLAMA_NUM_GPU
        return opts


def _normalize_tool_calls(message: dict) -> list[dict]:
    """Ollama 응답 tool_calls → {name, arguments} 리스트."""
    out = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        name = fn.get("name") or tc.get("name")
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        if name:
            out.append({"name": name, "arguments": args})
    return out


def chat_with_tools(
    user_text: str,
    history: list,
    *,
    system_prompt: str,
    tools: list,
    execute_tool: Callable[[str, dict], dict],
    pipeline=None,
    get_context_window: Callable[[list], list] | None = None,
    max_iters: int = 8,
    log_fn: Callable[[str, str], None] | None = None,
) -> str:
    """
    Ollama 로컬 LLM + function calling 루프.
    execute_tool(name, args) → 도구 결과 dict.
    """
    win = get_context_window(history) if get_context_window else (history or [])
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for m in win:
        role = "assistant" if m.get("role") == "assistant" else "user"
        messages.append({"role": role, "content": m.get("content", "")})
    messages.append({"role": "user", "content": user_text})

    if pipeline:
        pipeline.start()

    final_text = ""
    for i in range(max_iters):
        payload = {
            "model": OLLAMA_MODEL,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "options": _ollama_options(),
        }
        try:
            r = requests.post(
                f"{OLLAMA_URL}/api/chat",
                json=payload,
                timeout=OLLAMA_TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            if log_fn:
                log_fn("ERR", f"Ollama 호출 실패: {e}")
            return final_text or f"죄송합니다, {USER_DISPLAY_NAME}. 로컬 AI 연결에 실패했습니다."

        msg = data.get("message") or {}
        content = (msg.get("content") or "").strip()
        tool_calls = _normalize_tool_calls(msg)

        if not tool_calls:
            final_text = content or final_text
            break

        messages.append(msg)
        for tc in tool_calls:
            name = tc["name"]
            args = tc["arguments"]
            if log_fn:
                log_fn("TOOL", f"[Ollama] {name}({json.dumps(args, ensure_ascii=False)[:80]})")
            result = execute_tool(name, args)
            messages.append({
                "role": "tool",
                "content": json.dumps(result, ensure_ascii=False)[:8000],
                "tool_name": name,
            })
        if content:
            final_text = content
    else:
        if not final_text:
            final_text = f"요청을 처리했지만 음성 답변을 생성하지 못했습니다, {USER_DISPLAY_NAME}."

    if not final_text and messages:
        # 도구 결과만 있고 모델 텍스트 없음 → 한 줄 요약 재요청
        try:
            messages.append({
                "role": "user",
                "content": "위 도구 결과 briefing을 2문장 한국어로 요약해 주세요.",
            })
            r = requests.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": messages,
                    "stream": False,
                    "options": _ollama_options(),
                },
                timeout=OLLAMA_TIMEOUT,
            )
            r.raise_for_status()
            final_text = (r.json().get("message", {}) or {}).get("content", "").strip()
        except Exception:
            pass

    if pipeline and final_text:
        for ch in final_text:
            pipeline.feed(ch)
        pipeline.finish()

    return final_text
