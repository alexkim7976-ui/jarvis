"""
J.A.R.V.I.S — Ollama HTTP 공용 호출 (정확도 옵션 통일)
"""
from __future__ import annotations

import os
import re
from typing import Any

import requests

from ai_accuracy import ollama_accuracy_options

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")

# 추론(thinking) 모델의 reasoning trace 가 음성/텍스트 답변으로 새는 것을 방지.
# Nemotron·Qwen-thinking 등은 <think>…</think> 블록을 content 에 넣을 수 있다.
_THINK_BLOCK = re.compile(r"<(think|thinking|reasoning)\b[^>]*>.*?</\1>", re.I | re.S)
_THINK_STRAY = re.compile(r"</?(think|thinking|reasoning)\b[^>]*>", re.I)


def strip_reasoning(text: str) -> str:
    """thinking 모델의 <think>…</think> reasoning trace 제거 (비추론 모델은 무해)."""
    if not text:
        return text or ""
    t = _THINK_BLOCK.sub("", text)
    # 닫는 태그가 없는(잘린) reasoning: 마지막 </think> 이후만 남긴다.
    low = t.lower()
    close = low.rfind("</think>")
    if close != -1:
        t = t[close + len("</think>"):]
    else:
        open_ = low.find("<think>")
        # 열린 태그만 있고 닫힘 없음 → trace 전체로 간주해 태그만 제거(내용 보존은 위험).
        if open_ != -1 and low.count("<think>") == 1:
            t = t[:open_] + t[open_ + len("<think>"):]
    t = _THINK_STRAY.sub("", t)
    return t.strip()


def _think_flag() -> bool | None:
    """OLLAMA_THINK 미설정 → None(모델 기본), 0/false → 비추론(저지연), 1/true → 추론."""
    raw = os.getenv("OLLAMA_THINK")
    if raw is None:
        return None
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


def ollama_chat(
    model: str,
    messages: list[dict[str, Any]],
    *,
    timeout: float | None = None,
    options: dict | None = None,
    images: list[str] | None = None,
    think: bool | None = None,
) -> str:
    """단일 /api/chat 호출 → assistant content (reasoning trace 제거)."""
    if images and messages:
        last = messages[-1]
        if last.get("role") == "user" and "images" not in last:
            last = dict(last)
            last["images"] = images
            messages = messages[:-1] + [last]

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": ollama_accuracy_options(options),
    }
    # 모델 상주 시간 — 콜드 재로딩(24GB 등) 방지로 체감 지연 감소. 미설정 시 Ollama 기본(5분).
    keep_alive = os.getenv("OLLAMA_KEEP_ALIVE", "").strip()
    if keep_alive:
        payload["keep_alive"] = keep_alive
    think_val = think if think is not None else _think_flag()
    if think_val is not None:
        payload["think"] = think_val
    t = timeout or float(os.getenv("OLLAMA_CHAT_TIMEOUT", "120"))
    r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=t)
    r.raise_for_status()
    return strip_reasoning((r.json().get("message") or {}).get("content", "").strip())
