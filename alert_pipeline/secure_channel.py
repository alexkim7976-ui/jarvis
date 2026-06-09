"""
J.A.R.V.I.S — 알림 파이프라인: 제로 트러스트 전송(Secure Channel)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
외부로 나가는 알림은 '인증된 세션 + 바인딩된 기기' 컨텍스트에서만 전송한다.

전송 전 게이트(순서 고정):
  1) OIDC 액세스 토큰 검증(verify_access) — 만료/서명/스코프(alert.push)
  2) 기기 바인딩 확인 — cnf.jkt 존재(DeviceGuard 가 발급한 PoP 토큰만 허용)
  3) 이그레스 검증 — security.validate_url 로 SSRF/내부망 차단(DNS 리바인딩 포함)
  4) 호스트 화이트리스트(설정 시) — 알림 수신처를 명시 도메인으로 제한
  5) 전송 — 토큰은 절대 URL/쿼리에 싣지 않고 Authorization: Bearer 헤더로만

token_verifier(token, required_scope) -> claims   (auth_idp.IdentityProvider.verify_access 호환)
url_validator(url) -> (ok, reason)                 (security.validate_url 호환)
transport(url, json=, headers=) -> resp            (net.post 호환; 미주입 시 드라이런)
"""
from __future__ import annotations
from urllib.parse import urlparse


class SecureChannelError(Exception):
    """전송 게이트 거부 — 사유(reason)를 호출자에게 노출(민감값 미포함)."""
    def __init__(self, reason: str, stage: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.stage = stage


class SecureChannel:
    def __init__(self, endpoint: str, *, token_verifier,
                 required_scope: str = "alert.push",
                 url_validator=None, transport=None,
                 allow_hosts: frozenset | set | None = None,
                 require_device_binding: bool = True):
        self.endpoint = endpoint
        self.token_verifier = token_verifier
        self.required_scope = required_scope
        self.url_validator = url_validator
        self.transport = transport
        self.allow_hosts = frozenset(allow_hosts) if allow_hosts else None
        self.require_device_binding = require_device_binding

    def send(self, payload: dict, *, sender_token: str) -> dict:
        """알림 payload 를 보안 채널로 전송. Returns 전송 메타(민감값 제외)."""
        # 1) OIDC 토큰 검증 + 스코프 — 실패 시 OAuthError 가 그대로 전파(401/403)
        claims = self.token_verifier(sender_token, self.required_scope)

        # 2) 기기 바인딩(PoP) — cnf.jkt 가 있어야 '바인딩된 기기 세션'
        if self.require_device_binding:
            jkt = ((claims or {}).get("cnf") or {}).get("jkt")
            if not jkt:
                raise SecureChannelError("기기 바인딩 누락(cnf.jkt 없음)", stage="device")

        # 3) 이그레스 검증(SSRF)
        validator = self.url_validator
        if validator is not None:
            ok, reason = validator(self.endpoint)
            if not ok:
                raise SecureChannelError(f"이그레스 차단: {reason}", stage="egress")

        # 4) 호스트 화이트리스트
        host = (urlparse(self.endpoint).hostname or "").lower().rstrip(".")
        if self.allow_hosts is not None and host not in self.allow_hosts:
            raise SecureChannelError(f"허용되지 않은 수신 호스트: {host}", stage="allowlist")

        # 5) 전송 — 토큰은 헤더로만(URL/쿼리/바디 금지)
        headers = {"Authorization": f"Bearer {sender_token}",
                   "Content-Type": "application/json"}
        meta = {"endpoint": self.endpoint, "host": host, "sub": (claims or {}).get("sub", ""),
                "scope": self.required_scope, "delivered": False}
        if self.transport is None:                 # 드라이런(테스트/오프라인) — 게이트만 통과
            meta["dry_run"] = True
            return meta
        resp = self.transport(self.endpoint, json=payload, headers=headers)
        meta["delivered"] = True
        meta["status"] = getattr(resp, "status_code", None)
        return meta

    def as_push_sink(self, sender_token: str):
        """AlertDispatcher.push_sink(text, ctx) 시그니처로 어댑트하는 클로저."""
        def _sink(text: str, ctx: dict):
            return self.send({"text": text, "context": ctx}, sender_token=sender_token)
        return _sink
