"""
J.A.R.V.I.S — 기기 바인딩: 자원 서버(RS) 검증 의존성(DeviceGuard)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
민감 명령 라우트(매크로 실행/시스템 제어/금융 차단 해제 등)에 건다.
ResourceGuard(액세스 토큰 RS256 검증) 위에 기기 결속 검증을 합성:

  Bearer 검증 → cnf.jkt 존재 → 기기 조회/상태 → jkt 일치 → 요청 서명 검증

헤더 계약:
  Authorization: Bearer <access_token>   (cnf.jkt 박힌 기기 바인딩 토큰)
  X-JARVIS-Device: <device_id>
  X-JARVIS-Proof:  <ES256 요청 증명 JWS>
"""
from __future__ import annotations

from fastapi import Request

from auth_idp import OAuthError                      # 표준 OAuth 오류(install_error_handler 가 JSON화)
from .registry import DeviceRegistry
from .proof import ProofReplayCache, verify_proof


def _canonical_url(request: Request) -> str:
    """htu 정규화 — scheme://host[:port]/path (쿼리·프래그먼트 제외, DPoP 관행)."""
    u = request.url
    return f"{u.scheme}://{u.netloc}{u.path}"


class DeviceGuard:
    def __init__(self, idp, registry: DeviceRegistry, replay: ProofReplayCache | None = None):
        self.idp = idp
        self.registry = registry
        self.replay = replay or ProofReplayCache()

    def require(self, scope=None):
        async def _dep(request: Request) -> dict:
            # 1) Bearer 액세스 토큰 RS256 검증(+스코프) — 기존 RS 계약 재사용
            h = request.headers.get("authorization", "")
            if not h.lower().startswith("bearer "):
                raise OAuthError("invalid_token", "Bearer 액세스 토큰 필요", 401)
            token = h[7:].strip()
            claims = self.idp.verify_access(token, required_scope=scope)

            # 2) 기기 바인딩 토큰인가(cnf.jkt) — 비바인딩 토큰은 이 라우트 진입 불가
            jkt = (claims.get("cnf") or {}).get("jkt")
            if not jkt:
                raise OAuthError("invalid_token", "기기 바인딩되지 않은 토큰(cnf 없음)", 401)

            # 3) 기기 식별/서명 헤더
            device_id = request.headers.get("x-jarvis-device", "").strip()
            proof = request.headers.get("x-jarvis-proof", "").strip()
            if not device_id or not proof:
                raise OAuthError("invalid_request", "기기 식별/서명 헤더(X-JARVIS-Device/Proof) 필요", 401)

            # 4) 등록 기기 조회 + 상태 + 토큰↔기기 결속 확인
            dev = self.registry.get(device_id)
            if not dev:
                raise OAuthError("invalid_token", "미등록 기기", 401)
            if dev["status"] != "active":
                raise OAuthError("invalid_token", "폐기된 기기", 401)
            if dev["jkt"] != jkt:
                raise OAuthError("invalid_token", "토큰-기기 바인딩 불일치(탈취 의심)", 401)

            # 5) 요청 증명 서명 검증(메서드/URL/바디/시각/리플레이)
            body = await request.body()
            ok, reason, _ = verify_proof(
                proof, dev["jwk"], method=request.method,
                url=_canonical_url(request), access_token=token,
                body=body, replay=self.replay)
            if not ok:
                raise OAuthError("invalid_token", f"기기 요청 서명 검증 실패: {reason}", 401)

            self.registry.touch(device_id)
            return claims
        return _dep
