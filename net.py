"""
J.A.R.V.I.S — 공용 네트워크 계층 (느린/불안정한 WiFi 최적화)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
모든 외부 HTTP 호출을 하나의 튜닝된 requests.Session으로 통일한다.

느린 WiFi 대응 핵심:
  1. 연결 재사용(HTTP keep-alive + 커넥션 풀) — 매 호출 TCP+TLS 핸드셰이크 제거.
     고지연 회선에서 호출당 수백 ms~수 초 절감.
  2. 자동 재시도 + 지수 백오프 — 일시적 패킷 손실/타임아웃/5xx에 견딤.
     불안정한 WiFi에서 "한 번 실패 = 끝"이 아니라 자동 복구.
  3. 적응형 타임아웃 (connect, read) — 연결은 빨리 포기하고, 응답 본문은 인내.
  4. 동시 연결 상한 — 백그라운드 폴러들이 느린 회선 대역을 독점하지 않게.

사용:
  from net import get, post, SESSION
  r = get(url, params=..., timeout=(5, 15))   # timeout 생략 시 프로파일 기본값
  r = post(url, json=...)
"""
from __future__ import annotations
import os
import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except Exception:  # 구버전 호환
    from requests.packages.urllib3.util.retry import Retry  # type: ignore


# ── 타임아웃 프로파일 (connect, read) 초 ──
# NET_PROFILE=slow 로 더 인내심 있는 값 사용 가능 (매우 느린 회선)
_PROFILE = os.getenv("NET_PROFILE", "normal").strip().lower()
if _PROFILE == "slow":
    DEFAULT_TIMEOUT = (8, 45)
elif _PROFILE == "fast":
    DEFAULT_TIMEOUT = (3, 15)
else:
    DEFAULT_TIMEOUT = (5, 30)


def _build_retry() -> Retry:
    """urllib3 버전차를 흡수하는 Retry 생성 (allowed_methods vs method_whitelist)."""
    common = dict(
        total=3, connect=3, read=2,
        backoff_factor=0.6,                      # 0.6, 1.2, 2.4초 ... 지수 백오프
        status_forcelist=(408, 425, 429, 500, 502, 503, 504),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    methods = frozenset(["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"])
    try:
        return Retry(allowed_methods=methods, **common)
    except TypeError:
        return Retry(method_whitelist=methods, **common)  # type: ignore


def _build_session() -> requests.Session:
    s = requests.Session()
    adapter = HTTPAdapter(
        max_retries=_build_retry(),
        pool_connections=16,    # 호스트별 연결 풀
        pool_maxsize=32,        # 동시 연결 상한 (대역 독점 방지)
    )
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update({"Connection": "keep-alive"})
    # 리다이렉트 상한 — 기본 30은 과함. 리다이렉트 체인 남용/루프를 줄이되
    # 숏링크·다단계 인증 리다이렉트는 통과하도록 10으로 절충.
    s.max_redirects = 10
    return s


# 프로세스 전역 공용 세션 (스레드 세이프)
SESSION = _build_session()


def request(method: str, url: str, **kwargs):
    """공용 세션 요청 — timeout 미지정 시 프로파일 기본값 주입."""
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    _guard_egress(url)
    return SESSION.request(method, url, **kwargs)


def get(url: str, **kwargs):
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    _guard_egress(url)
    return SESSION.get(url, **kwargs)


def post(url: str, **kwargs):
    kwargs.setdefault("timeout", DEFAULT_TIMEOUT)
    _guard_egress(url)
    return SESSION.post(url, **kwargs)


def _guard_egress(url: str) -> None:
    """최대/엄격 보안 모드에서 모든 외부 HTTP 요청에 SSRF 검증 적용."""
    try:
        from security_policy import net_egress_guard_enabled
        if not net_egress_guard_enabled():
            return
        from security import validate_url
        safe, reason = validate_url(url)
        if not safe:
            raise PermissionError(f"네트워크 이그레스 차단: {reason} ({url[:80]})")
    except PermissionError:
        raise
    except Exception:
        pass
