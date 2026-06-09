"""
media_pipeline — URL 가드 (ir_interpreter:130 SSRF 가드 재사용)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
validate_url → (선택) 리다이렉트 체인 사전검증(최대 10회, net.py 와 동일 상한).
"""
from __future__ import annotations

import os
from urllib.parse import urljoin, urlparse

from .config import MAX_REDIRECTS, PREFLIGHT_REDIRECTS
from .errors import URLGuardError

try:
    from security import validate_url, chain_event
except ImportError:
    def validate_url(url: str) -> tuple[bool, str]:
        parsed = urlparse((url or "").strip())
        if parsed.scheme not in ("http", "https", "rtmp", "rtmps"):
            return False, "허용되지 않은 스킴"
        return True, "OK"

    def chain_event(event: str, detail: dict | None = None):
        return None


_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})


def guard_media_url(url: str) -> tuple[bool, str]:
    """단일 URL SSRF 검증 — 차단 시 감사 체인 기록."""
    safe, reason = validate_url(url)
    if not safe:
        try:
            chain_event("SSRF_BLOCK", {"url": (url or "")[:80], "reason": reason, "source": "media_pipeline"})
        except Exception:
            pass
        return False, reason
    return True, "OK"


def preflight_media_url(url: str, *, max_redirects: int | None = None) -> tuple[str, str]:
    """
    URL 검증 + (옵션) HEAD 리다이렉트 체인 검증.
    Returns: (final_url, reason) — 실패 시 URLGuardError 발생.
    """
    url = (url or "").strip()
    if not url:
        raise URLGuardError("빈 URL", stage="url")

    safe, reason = guard_media_url(url)
    if not safe:
        raise URLGuardError(f"보안 차단(SSRF 방지): {reason}", stage="ssrf")

    limit = max_redirects if max_redirects is not None else MAX_REDIRECTS
    if not PREFLIGHT_REDIRECTS:
        return url, "OK"

    current = url
    hops = 0
    try:
        import net
    except ImportError:
        return url, "OK"

    while hops <= limit:
        safe, reason = guard_media_url(current)
        if not safe:
            raise URLGuardError(f"리다이렉트 대상 차단: {reason}", stage="redirect")

        try:
            resp = net.SESSION.head(current, allow_redirects=False, timeout=(5, 15))
        except Exception as exc:
            strict = os.getenv("JARVIS_SSRF_STRICT", "0").strip().lower() in ("1", "true", "yes", "on")
            if strict:
                raise URLGuardError(f"DNS/연결 실패 — 엄격 모드: {exc}", stage="dns") from exc
            return current, "OK"

        if resp.status_code not in _REDIRECT_STATUS:
            return current, "OK"

        location = resp.headers.get("Location") or resp.headers.get("location")
        if not location:
            return current, "OK"

        current = urljoin(current, location.strip())
        hops += 1
        if hops > limit:
            raise URLGuardError(f"리다이렉트 {limit}회 초과", stage="redirect")

    return current, "OK"
