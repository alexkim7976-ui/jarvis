"""
J.A.R.V.I.S — 대화 컨텍스트 윈도우 / 요약 관리

환경변수:
  GEMINI_CONTEXT_TURNS       LLM 호출당 전달할 최 recent 메시지 수 (기본 10)
  JARVIS_MAX_HISTORY         메모리에 유지할 최대 메시지 수 (기본 40)
  JARVIS_HISTORY_SUMMARIZE   1 — 오래된 대화를 Gemini Flash로 압축 요약
  JARVIS_SUMMARIZE_CHARS     요약 트리거 문자 수 합계 (기본 12000)
"""
from __future__ import annotations

import os
from typing import Callable


def context_turn_limit() -> int:
    return max(2, int(os.getenv("GEMINI_CONTEXT_TURNS", "10")))


def max_history_messages() -> int:
    return max(4, int(os.getenv("JARVIS_MAX_HISTORY", "40")))


def summarize_enabled() -> bool:
    return os.getenv("JARVIS_HISTORY_SUMMARIZE", "1").strip().lower() in (
        "1", "true", "yes", "on",
    )


def summarize_char_threshold() -> int:
    return max(2000, int(os.getenv("JARVIS_SUMMARIZE_CHARS", "12000")))


def _history_char_count(history: list) -> int:
    return sum(len(str(m.get("content", ""))) for m in history)


def get_context_window(history: list) -> list:
    """LLM에 전달할 최근 대화 윈도우. Gemini 규칙: 첫 메시지는 user."""
    limit = context_turn_limit()
    win = history[-limit:] if limit > 0 else list(history)
    while win and win[0].get("role") != "user":
        win = win[1:]
    return win


def _summarize_old_messages(
    old: list,
    *,
    gemini_api_key: str = "",
    gemini_model: str = "",
    log_fn: Callable[[str, str], None] | None = None,
) -> str:
    """오래된 대화를 한 단락 한국어 요약으로 압축."""
    if not old:
        return ""
    key = gemini_api_key or os.getenv("GEMINI_API_KEY", "")
    if not key:
        return ""
    model_name = gemini_model or os.getenv("GEMINI_FLASH_MODEL", "gemini-3.5-flash")
    lines = []
    for m in old:
        role = "사용자" if m.get("role") == "user" else "JARVIS"
        text = str(m.get("content", "")).strip()
        if text:
            lines.append(f"{role}: {text[:400]}")
    if not lines:
        return ""
    blob = "\n".join(lines[-30:])
    try:
        import google.generativeai as genai
        genai.configure(api_key=key)
        model = genai.GenerativeModel(model_name=model_name)
        resp = model.generate_content(
            "아래 JARVIS 대화 기록을 한국어 3~5문장으로 압축 요약하세요. "
            "종목명·숫자·결정 사항만 남기고 불필요한 인사말은 생략.\n\n" + blob,
            generation_config={"max_output_tokens": 200, "temperature": 0.2},
        )
        summary = (resp.text or "").strip()
        if log_fn and summary:
            log_fn("INFO", f"대화 요약 생성 ({len(old)}건 → {len(summary)}자)")
        return summary
    except Exception as e:
        if log_fn:
            log_fn("WARN", f"대화 요약 실패 — 슬라이딩 윈도우만 적용: {e}")
        return ""


def maybe_summarize_and_trim(
    history: list,
    *,
    gemini_api_key: str = "",
    gemini_model: str = "",
    log_fn: Callable[[str, str], None] | None = None,
) -> None:
    """히스토리가 커지면 요약 후 최근 항목만 유지."""
    max_h = max_history_messages()
    if len(history) <= max_h and _history_char_count(history) <= summarize_char_threshold():
        return

    keep = max(context_turn_limit() * 2, 8)
    if len(history) <= keep:
        if len(history) > max_h:
            del history[:-max_h]
        return

    if summarize_enabled():
        old = history[:-keep]
        summary = _summarize_old_messages(
            old,
            gemini_api_key=gemini_api_key,
            gemini_model=gemini_model,
            log_fn=log_fn,
        )
        if summary:
            history[:] = (
                [{"role": "user", "content": f"[이전 대화 요약] {summary}"}]
                + history[-keep:]
            )

    if len(history) > max_h:
        del history[:-max_h]


def append_exchange(
    history: list,
    user_text: str,
    assistant_text: str,
    *,
    gemini_api_key: str = "",
    gemini_model: str = "",
    log_fn: Callable[[str, str], None] | None = None,
) -> None:
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": assistant_text})
    maybe_summarize_and_trim(
        history,
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
        log_fn=log_fn,
    )
