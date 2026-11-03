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


def _tools_supported_heuristic(model: str) -> bool:
    m = model.lower()
    if "gemma3" in m and "gemma4" not in m:
        return False
    return any(k in m for k in ("gemma4", "gemma2", "qwen", "llama3", "llama3.1", "llama3.2", "nemotron", "mistral"))


_tool_probe_cache: dict[str, bool] = {}


def model_supports_tools(model: str | None = None, *, probe: bool = True) -> bool:
    """Ollama 모델 tools 지원 여부 (gemma3 등 미지원 모델 방지, 모델별 1회만 확인/기본 휴리스틱 우선)."""
    name = model or OLLAMA_MODEL
    if not _tools_supported_heuristic(name):
        return False
    if name in _tool_probe_cache:
        return _tool_probe_cache[name]
    if not probe or os.getenv("JARVIS_SKIP_TOOL_PROBE", "1").strip().lower() in ("1", "true", "yes"):
        _tool_probe_cache[name] = True
        return True
    timeout = float(os.getenv("OLLAMA_TOOL_PROBE_TIMEOUT", "8"))
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": name,
                "messages": [{"role": "user", "content": "ping"}],
                "tools": [{"type": "function", "function": {
                    "name": "ping", "description": "ping",
                    "parameters": {"type": "object", "properties": {}},
                }}],
                "stream": False,
                "options": {"num_predict": 1},
            },
            timeout=timeout,
        )
        ok = r.status_code == 200 and "does not support tools" not in (r.text or "").lower()
        _tool_probe_cache[name] = ok
        return ok
    except Exception:
        # 첫 로드·GPU 대기 등으로 타임아웃 — 휴리스틱으로 진행 (시작 막지 않음)
        res = _tools_supported_heuristic(name)
        _tool_probe_cache[name] = res
        return res


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
    Ollama 로컬 LLM + function calling 루프 (실시간 스트리밍 지원).
    첫 문장이 생성되는 즉시 pipeline.feed()로 TTS 합성을 시작하여 체감 응답 지연을 대폭 줄인다.
    """
    import time as _time

    win = get_context_window(history) if get_context_window else (history or [])
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for m in win:
        role = "assistant" if m.get("role") == "assistant" else "user"
        messages.append({"role": role, "content": m.get("content", "")})
    messages.append({"role": "user", "content": user_text})

    if pipeline:
        pipeline.start()

    final_text = ""
    fed_any = False
    for i in range(max_iters):
        t_req = _time.perf_counter()
        t_first_tok = None
        payload = {
            "model": OLLAMA_MODEL,
            "messages": messages,
            "tools": tools,
            "stream": True,
            "options": _ollama_options(),
        }
        content_parts: list[str] = []
        raw_tool_calls: list[dict] = []
        try:
            r = requests.post(
                f"{OLLAMA_URL}/api/chat",
                json=payload,
                stream=True,
                timeout=OLLAMA_TIMEOUT,
            )
            r.raise_for_status()
            for raw_line in r.iter_lines():
                if not raw_line:
                    continue
                try:
                    chunk = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                msg_chunk = chunk.get("message") or {}
                tcs = msg_chunk.get("tool_calls")
                if tcs:
                    raw_tool_calls.extend(tcs)
                tok = msg_chunk.get("content") or ""
                if tok:
                    if t_first_tok is None:
                        t_first_tok = _time.perf_counter()
                        if log_fn and os.getenv("JARVIS_TIMING_LOG", "0").strip().lower() in ("1", "true", "yes", "on"):
                            log_fn("INFO", f"⚡ [Timing] LLM first token in {(t_first_tok - t_req)*1000:.0f}ms")
                    content_parts.append(tok)
                    # 도구 호출이 없는 일반 응답이면 토큰 도착 즉시 TTS 파이프라인에 스트리밍
                    if pipeline and not raw_tool_calls:
                        for ch in tok:
                            pipeline.feed(ch)
                        fed_any = True
        except Exception as e:
            if final_text:
                if pipeline:
                    pipeline.finish()
                return final_text
            if log_fn:
                log_fn("WARN", f"Ollama call failed -> falling back to offline: {e}")
            try:
                from runtime_backtrack import ollama_failure_reply
                ans = ollama_failure_reply(user_text, e)
            except Exception:
                ans = f"I apologize, {USER_DISPLAY_NAME}. Failed to connect to the local AI engine."
            if pipeline and ans:
                for ch in ans:
                    pipeline.feed(ch)
                pipeline.finish()
            return ans

        content = "".join(content_parts).strip()
        tool_calls = _normalize_tool_calls({"tool_calls": raw_tool_calls})

        if not tool_calls:
            final_text = content or final_text
            break

        assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
        if raw_tool_calls:
            assistant_msg["tool_calls"] = raw_tool_calls
        messages.append(assistant_msg)
        for tc in tool_calls:
            name = tc["name"]
            args = tc["arguments"]
            if log_fn:
                log_fn("TOOL", f"[Ollama] {name}({json.dumps(args, ensure_ascii=False)[:80]})")
            t_tool = _time.perf_counter()
            result = execute_tool(name, args)
            if log_fn and os.getenv("JARVIS_TIMING_LOG", "0").strip().lower() in ("1", "true", "yes", "on"):
                log_fn("INFO", f"⚡ [Timing] Tool {name} finished in {(_time.perf_counter() - t_tool)*1000:.0f}ms")
            messages.append({
                "role": "tool",
                "content": json.dumps(result, ensure_ascii=False)[:8000],
                "tool_name": name,
            })
        if content:
            final_text = content
    else:
        if not final_text:
            final_text = f"I processed your request, {USER_DISPLAY_NAME}, but could not generate a spoken response."

    if not final_text and messages:
        # 도구 결과만 있고 모델 텍스트 없음 → 선택된 언어로 한 줄 요약 스트리밍 재요청
        try:
            from i18n import get_lang_meta
            _lang_name = get_lang_meta()["llm_language"]
        except Exception:
            _lang_name = "English"
        try:
            messages.append({
                "role": "user",
                "content": f"Summarize the tool result above in 2 concise {_lang_name} sentences suitable for voice playback.",
            })
            r = requests.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": messages,
                    "stream": True,
                    "options": _ollama_options(),
                },
                stream=True,
                timeout=OLLAMA_TIMEOUT,
            )
            r.raise_for_status()
            summary_parts: list[str] = []
            for raw_line in r.iter_lines():
                if not raw_line:
                    continue
                try:
                    chunk = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                tok = (chunk.get("message") or {}).get("content") or ""
                if tok:
                    summary_parts.append(tok)
                    if pipeline:
                        for ch in tok:
                            pipeline.feed(ch)
                        fed_any = True
            final_text = "".join(summary_parts).strip()
        except Exception:
            pass

    if pipeline:
        if final_text and not fed_any:
            for ch in final_text:
                pipeline.feed(ch)
        pipeline.finish()

    return final_text
