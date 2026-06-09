"""
J.A.R.V.I.S — 경량 자급제 OIDC Identity Provider 패키지.

빅테크 스타일 OAuth 2.0 / OIDC 인증을 외부 무거운 솔루션(Keycloak 등) 없이
기존 Python/FastAPI 스택 + PyJWT + cryptography 만으로 제공한다.

구성:
  KeyManager           — RSA-2048 비대칭키 + JWKS + 키 회전
  RefreshTokenStore    — 리프레시 토큰 RTR(회전) + 재사용 도난 탐지
  IdentityProvider     — 인증 서버(AS): 클라이언트/그랜트/발급/검증
  build_app            — /oauth/token, /.well-known/* FastAPI 앱
  ResourceGuard        — 자원 서버(RS) Bearer 검증 의존성
  bind_jarvis_link     — 승인코드 ↔ Authorization Code 브리지
"""
from .keys import KeyManager
from .store import RefreshTokenStore
from .idp import IdentityProvider, OAuthError, ISSUER, AUDIENCE, ACCESS_TTL, REFRESH_TTL
from .server import build_app, ResourceGuard, bind_jarvis_link, install_error_handler

__all__ = [
    "KeyManager", "RefreshTokenStore", "IdentityProvider", "OAuthError",
    "ISSUER", "AUDIENCE", "ACCESS_TTL", "REFRESH_TTL",
    "build_app", "ResourceGuard", "bind_jarvis_link", "install_error_handler",
]
