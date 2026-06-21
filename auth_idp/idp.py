"""
J.A.R.V.I.S — 경량 OIDC IDP 코어 (Authorization Server)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
인증 서버(AS) 책임만 담당:
  · 클라이언트 자격 검증(client_secret → Argon2id 해시 + 상수시간 검증)
  · 3대 그랜트: client_credentials / authorization_code / refresh_token(RTR)
  · RS256(비대칭) 토큰 발급 + 검증
자원 서버(RS)는 verify_access() 만 호출 → AS 와 논리적으로 분리.
"""
from __future__ import annotations
import os, time, secrets
from dataclasses import dataclass

import jwt  # PyJWT
from argon2 import PasswordHasher, Type  # argon2-cffi
from argon2.exceptions import VerificationError, VerifyMismatchError, InvalidHashError

from .keys import KeyManager
from .store import RefreshTokenStore

ISSUER      = os.getenv("JARVIS_OIDC_ISSUER", "https://jarvis.local")
AUDIENCE    = os.getenv("JARVIS_OIDC_AUDIENCE", "jarvis-resource")
ACCESS_TTL  = int(os.getenv("JARVIS_OIDC_ACCESS_TTL", "900"))       # 15분
REFRESH_TTL = int(os.getenv("JARVIS_OIDC_REFRESH_TTL", "604800"))   # 7일
_LEEWAY     = int(os.getenv("JARVIS_OIDC_LEEWAY", "30"))            # 시계 오차 허용(초)


class OAuthError(Exception):
    """RFC 6749 스타일 오류 — error/description/HTTP status 동반."""
    def __init__(self, error: str, description: str = "", status: int = 400):
        super().__init__(error)
        self.error = error
        self.description = description
        self.status = status


# Argon2id — 하드웨어 동원 무차별 대입(덤프) 공격 대응. 매 해시마다 salt 내장 →
# 레인보우/사전 공격 무력화. 파라미터는 argon2-cffi 기본값(time_cost=3,
# memory_cost=64MiB, parallelism=4)에 type=ID(Argon2id) 명시.
_ph = PasswordHasher(type=Type.ID)


def _hash_secret(secret: str) -> str:
    """클라이언트 시크릿 → Argon2id 해시. 검증은 반드시 _verify_secret 사용."""
    return _ph.hash(secret or "")


def _verify_secret(stored_hash: str, secret: str) -> bool:
    """Argon2id 상수시간 검증. 불일치·손상 해시는 모두 False로 흡수."""
    try:
        return _ph.verify(stored_hash, secret or "")
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        return False


@dataclass
class Client:
    client_id: str
    secret_hash: str
    grants: set
    scope: str = "openid profile"
    confidential: bool = True


class IdentityProvider:
    def __init__(self, keys: KeyManager | None = None,
                 store: RefreshTokenStore | None = None,
                 authorization_code_verifier=None):
        self.keys = keys or KeyManager()
        self.store = store or RefreshTokenStore()
        self._clients: dict[str, Client] = {}
        # 주입형 승인코드 검증기: code -> (ok: bool, context: dict|None)
        # 운영 시 jarvis_link._consume_approval_code 를 연결(server.bind_jarvis_link).
        self.authorization_code_verifier = authorization_code_verifier

    # ── 클라이언트 ──────────────────────────────────────────────
    def register_client(self, client_id: str, secret: str, grants,
                        scope: str = "openid profile", confidential: bool = True) -> Client:
        c = Client(client_id, _hash_secret(secret), set(grants), scope, confidential)
        self._clients[client_id] = c
        return c

    def _auth_client(self, client_id: str | None, secret: str | None) -> Client:
        c = self._clients.get(client_id or "")
        if not c:
            raise OAuthError("invalid_client", "알 수 없는 클라이언트", 401)
        if c.confidential:
            if not secret or not _verify_secret(c.secret_hash, secret):
                raise OAuthError("invalid_client", "클라이언트 시크릿 불일치", 401)
        return c

    # ── 토큰 빌더 ───────────────────────────────────────────────
    def _now(self) -> int:
        return int(time.time())

    def _sign(self, claims: dict) -> str:
        k = self.keys.active
        return jwt.encode(claims, k.private, algorithm="RS256", headers={"kid": k.kid})

    def _base_claims(self, sub: str, aud: str, client_id: str) -> dict:
        now = self._now()
        return {"iss": ISSUER, "sub": sub, "aud": aud, "client_id": client_id,
                "iat": now, "nbf": now, "jti": secrets.token_urlsafe(12)}

    def _issue_access(self, sub, scope, client_id, cnf=None) -> str:
        c = self._base_claims(sub, AUDIENCE, client_id)
        c.update({"exp": c["iat"] + ACCESS_TTL, "typ": "access", "scope": scope})
        if cnf:                       # 기기 바인딩(RFC 7800 PoP) — 미전달 시 기존과 100% 동일
            c["cnf"] = cnf
        return self._sign(c)

    def _issue_id_token(self, sub, client_id, nonce=None) -> str:
        # OIDC id_token — aud = client_id
        c = self._base_claims(sub, client_id, client_id)
        c.update({"exp": c["iat"] + ACCESS_TTL, "typ": "id", "auth_time": c["iat"]})
        if nonce:
            c["nonce"] = nonce
        return self._sign(c)

    def _issue_refresh(self, sub, scope, client_id, family_id) -> str:
        c = self._base_claims(sub, ISSUER, client_id)   # refresh aud = issuer(자기 자신)
        c.update({"exp": c["iat"] + REFRESH_TTL, "typ": "refresh", "scope": scope, "fam": family_id})
        token = self._sign(c)
        self.store.register(c["jti"], sub=sub, family_id=family_id,
                            client_id=client_id, scope=scope, exp=c["exp"])
        return token

    def _token_response(self, sub, scope, client_id, family_id, with_id=False, nonce=None, cnf=None) -> dict:
        resp = {"access_token": self._issue_access(sub, scope, client_id, cnf=cnf),
                "token_type": "Bearer",
                "expires_in": ACCESS_TTL,
                "refresh_token": self._issue_refresh(sub, scope, client_id, family_id),
                "scope": scope}
        if with_id or "openid" in scope.split():
            resp["id_token"] = self._issue_id_token(sub, client_id, nonce)
        return resp

    # ── 그랜트 ─────────────────────────────────────────────────
    def grant_client_credentials(self, client_id, client_secret, scope=None, cnf=None) -> dict:
        c = self._auth_client(client_id, client_secret)
        if "client_credentials" not in c.grants:
            raise OAuthError("unauthorized_client", "허용되지 않은 그랜트", 400)
        scope = scope or c.scope
        return self._token_response(client_id, scope, client_id, self.store.new_family(), cnf=cnf)

    def grant_authorization_code(self, client_id, client_secret, code, scope=None, cnf=None) -> dict:
        c = self._auth_client(client_id, client_secret)
        if "authorization_code" not in c.grants:
            raise OAuthError("unauthorized_client", "허용되지 않은 그랜트", 400)
        if not self.authorization_code_verifier:
            raise OAuthError("server_error", "승인코드 검증기 미구성", 500)
        ok, ctx = self.authorization_code_verifier(code or "")
        if not ok:
            raise OAuthError("invalid_grant", "유효하지 않거나 만료된 승인 코드", 400)
        ctx = ctx or {}
        sub = str(ctx.get("sub") or ctx.get("user") or "jarvis-user")
        scope = scope or ctx.get("scope") or c.scope
        return self._token_response(sub, scope, client_id, self.store.new_family(), with_id=True, cnf=cnf)

    def grant_refresh_token(self, client_id, client_secret, refresh_token) -> dict:
        c = self._auth_client(client_id, client_secret)
        if "refresh_token" not in c.grants:
            raise OAuthError("unauthorized_client", "허용되지 않은 그랜트", 400)
        claims = self._verify(refresh_token, expected_typ="refresh", aud=ISSUER)
        if claims.get("client_id") != client_id:
            # 다른 클라이언트의 토큰 사용 시도 → family 폐기 후 거부
            self.store.revoke_family(claims.get("fam", ""))
            raise OAuthError("invalid_grant", "토큰-클라이언트 불일치", 400)
        ok, reason, rec = self.store.consume(claims["jti"])
        if not ok:
            raise OAuthError("invalid_grant", f"리프레시 거부: {reason}", 400)
        # RTR: 같은 family 로 새 토큰 쌍 발급(기존 jti 는 used 처리됨)
        return self._token_response(rec["sub"], rec["scope"], client_id, rec["family_id"])

    # ── 검증 (자원 서버가 사용) ────────────────────────────────
    def _verify(self, token: str, expected_typ: str, aud: str) -> dict:
        try:
            header = jwt.get_unverified_header(token)
        except Exception:
            raise OAuthError("invalid_token", "JWT 헤더 파싱 실패", 401)
        pub = self.keys.public_key_for(header.get("kid", ""))
        if pub is None:
            raise OAuthError("invalid_token", "알 수 없는 kid", 401)
        try:
            claims = jwt.decode(
                token, pub, algorithms=["RS256"], audience=aud, issuer=ISSUER,
                leeway=_LEEWAY, options={"require": ["exp", "iat", "iss", "aud", "jti"]})
        except jwt.ExpiredSignatureError:
            raise OAuthError("invalid_token", "만료된 토큰", 401)
        except jwt.InvalidSignatureError:
            raise OAuthError("invalid_token", "서명 검증 실패(변조 의심)", 401)
        except Exception:
            raise OAuthError("invalid_token", "토큰 검증 실패", 401)
        if claims.get("typ") != expected_typ:
            raise OAuthError("invalid_token", f"토큰 타입 불일치(expected {expected_typ})", 401)
        return claims

    def verify_access(self, token: str, required_scope=None) -> dict:
        claims = self._verify(token, expected_typ="access", aud=AUDIENCE)
        if required_scope:
            have = set((claims.get("scope") or "").split())
            need = (required_scope if isinstance(required_scope, (set, list, tuple))
                    else [required_scope])
            if not set(need).issubset(have):
                raise OAuthError("insufficient_scope", f"필요 스코프: {sorted(need)}", 403)
        return claims

    # ── 메타데이터 ─────────────────────────────────────────────
    def jwks(self) -> dict:
        return self.keys.jwks()

    def discovery(self) -> dict:
        return {
            "issuer": ISSUER,
            "token_endpoint": f"{ISSUER}/oauth/token",
            "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
            "grant_types_supported": ["client_credentials", "authorization_code", "refresh_token"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
            "response_types_supported": ["token", "code"],
            "scopes_supported": ["openid", "profile", "rebalance"],
            "subject_types_supported": ["public"],
        }
