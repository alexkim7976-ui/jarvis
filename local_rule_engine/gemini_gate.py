"""local_rule_engine — Gemini 호출 게이트 (컴팩트 컨텍스트만)."""
from __future__ import annotations

import concurrent.futures
from typing import Callable

_GEMINI_CALL_COUNT = 0
_GEMINI_HOOK: Callable[[], None] | None = None


def reset_gemini_call_count() -> None:
    global _GEMINI_CALL_COUNT
    _GEMINI_CALL_COUNT = 0


def get_gemini_call_count() -> int:
    return _GEMINI_CALL_COUNT


def set_gemini_call_hook(hook: Callable[[], None] | None) -> None:
    global _GEMINI_HOOK
    _GEMINI_HOOK = hook


def _bump_gemini_calls() -> None:
    global _GEMINI_CALL_COUNT
    _GEMINI_CALL_COUNT += 1
    if _GEMINI_HOOK:
        _GEMINI_HOOK()


def fetch_gemini_brief(
    ticker: str,
    name: str,
    *,
    compact_context: str,
    timeout: int = 8,
) -> dict | None:
    """
    임계치 통과 후에만 호출. compact_context(지표 스냅샷 1~2문장)만 Gemini에 전달.
    Returns: {summary, headlines, gemini_called: True} or None
    """
    if not compact_context:
        return None

    try:
        from news_scraper import fetch_ticker_headlines, _gemini_summarize, FALLBACK_SUMMARY
    except ImportError:
        return None

    try:
        headlines = fetch_ticker_headlines(ticker, name, n=3)
        if not headlines:
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(
                _gemini_summarize,
                headlines,
                ticker,
                name,
                compact_context,
            )
            summary = fut.result(timeout=timeout)

        _bump_gemini_calls()

        if not summary or summary == FALLBACK_SUMMARY:
            return {"summary": "", "headlines": headlines, "gemini_called": True}

        return {
            "summary": summary,
            "headlines": headlines,
            "gemini_called": True,
        }
    except concurrent.futures.TimeoutError:
        return None
    except Exception:
        return None
