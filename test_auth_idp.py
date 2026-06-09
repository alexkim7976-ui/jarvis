"""
J.A.R.V.I.S — OIDC IDP 인증 흐름 테스트 (기존 44개와 독립 실행)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
실행: .venv/bin/python test_auth_idp.py
검증:
  · 3대 그랜트(client_credentials / authorization_code / refresh)
  · JWKS 공개키만으로 RS256 검증(자원 서버 계약)
  · RTR 회전 + 재사용 도난 탐지 → family 폐기
  · 만료 토큰 / 변조 JWT / 잘못된 클라이언트 / 스코프 부족 차단
"""
import base64, tempfile, time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from auth_idp import (KeyManager, IdentityProvider, build_app, ResourceGuard,
                      install_error_handler, bind_jarvis_link,
                      ISSUER, AUDIENCE)

CLIENT_ID, CLIENT_SECRET = "jarvis-mobile", "s3cr3t-shh"


def _fresh_idp(verifier=None) -> IdentityProvider:
    """매 테스트 임시 키파일로 격리된 IDP 생성(.certs 오염 방지)."""
    tmp = Path(tempfile.mkdtemp()) / "idp_test_key.pem"
    idp = IdentityProvider(keys=KeyManager(key_path=str(tmp)),
                           authorization_code_verifier=verifier)
    idp.register_client(CLIENT_ID, CLIENT_SECRET,
                        grants=["client_credentials", "authorization_code", "refresh_token"],
                        scope="openid profile rebalance")
    return idp


def _rs_app(idp) -> TestClient:
    """자원 서버(RS) — JWKS 공개키 검증만 수행하는 분리된 앱."""
    rs = FastAPI()
    install_error_handler(rs)
    guard = ResourceGuard(idp)

    @rs.get("/secure")
    async def secure(claims: dict = Depends(guard.require("rebalance"))):
        return {"sub": claims["sub"], "scope": claims["scope"]}

    @rs.get("/any")
    async def any_(claims: dict = Depends(guard.require())):
        return {"sub": claims["sub"]}

    return TestClient(rs)


def _b64u_int(s: str) -> int:
    return int.from_bytes(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)), "big")


# ── 1. JWKS / 디스커버리 ─────────────────────────────────────────
def test_jwks_and_discovery():
    idp = _fresh_idp()
    c = TestClient(build_app(idp))
    jwks = c.get("/.well-known/jwks.json").json()
    assert jwks["keys"] and jwks["keys"][0]["kty"] == "RSA"
    assert jwks["keys"][0]["alg"] == "RS256" and jwks["keys"][0]["kid"]
    disc = c.get("/.well-known/openid-configuration").json()
    assert disc["issuer"] == ISSUER
    assert "authorization_code" in disc["grant_types_supported"]
    assert disc["id_token_signing_alg_values_supported"] == ["RS256"]
    print("✓ JWKS 공개키 노출 + OIDC 디스커버리 메타데이터")


# ── 2. client_credentials + JWKS 공개키 독립검증 ─────────────────
def test_client_credentials_and_jwks_verify():
    idp = _fresh_idp()
    c = TestClient(build_app(idp))
    r = c.post("/oauth/token", data={"grant_type": "client_credentials",
                                     "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 900
    assert body["access_token"] and body["refresh_token"]

    # RS 입장: 비공개키 없이 JWKS 공개키(n,e)만으로 RS256 검증 성공해야 함
    jwk = c.get("/.well-known/jwks.json").json()["keys"][0]
    pub = RSAPublicNumbers(_b64u_int(jwk["e"]), _b64u_int(jwk["n"])).public_key()
    claims = jwt.decode(body["access_token"], pub, algorithms=["RS256"],
                        audience=AUDIENCE, issuer=ISSUER)
    assert claims["typ"] == "access" and claims["client_id"] == CLIENT_ID
    print("✓ client_credentials 발급 + JWKS 공개키만으로 RS256 검증")


# ── 3. authorization_code (jarvis_link 승인코드 역할) ────────────
def test_authorization_code_flow():
    issued = {"GOODCODE7X": (True, {"sub": "user-7", "scope": "openid rebalance"})}
    verifier = lambda code: issued.get(code, (False, None))
    idp = _fresh_idp(verifier=verifier)
    c = TestClient(build_app(idp))

    r = c.post("/oauth/token", data={"grant_type": "authorization_code", "code": "GOODCODE7X",
                                     "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "id_token" in body, "openid 스코프 → id_token 발급되어야 함"
    claims = idp.verify_access(body["access_token"])
    assert claims["sub"] == "user-7" and "rebalance" in claims["scope"]

    # 잘못된 코드 → invalid_grant
    bad = c.post("/oauth/token", data={"grant_type": "authorization_code", "code": "NOPE",
                                       "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_grant", bad.text
    print("✓ authorization_code 그랜트(승인코드→토큰) + 잘못된 코드 차단")


# ── 4. Refresh Token Rotation(RTR) + 재사용 도난 탐지 ────────────
def test_refresh_rotation_and_reuse_detection():
    idp = _fresh_idp()
    c = TestClient(build_app(idp))
    creds = {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}

    r1 = c.post("/oauth/token", data={"grant_type": "client_credentials", **creds}).json()
    R1 = r1["refresh_token"]

    # 정상 회전: R1 → 새 쌍(R2)
    r2 = c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": R1, **creds})
    assert r2.status_code == 200, r2.text
    R2 = r2.json()["refresh_token"]
    assert R2 != R1, "회전 시 새 refresh 토큰이어야 함"

    # 도난 시나리오: 이미 쓴 R1 재사용 → reuse_detected, family 폐기
    reuse = c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": R1, **creds})
    assert reuse.status_code == 400 and reuse.json()["error"] == "invalid_grant", reuse.text
    assert "reuse" in reuse.json()["error_description"]

    # family 폐기로 정상 회전분 R2 도 무효화되어야 함(도난 차단의 핵심)
    after = c.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": R2, **creds})
    assert after.status_code == 400, "재사용 탐지 후 family 전체가 폐기되어야 함"
    print("✓ RTR 회전 + 재사용 도난 탐지 → family 전체 폐기")


# ── 5. 만료 토큰 차단 ────────────────────────────────────────────
def test_expired_access_rejected():
    idp = _fresh_idp()
    rs = _rs_app(idp)
    k = idp.keys.active
    now = int(time.time())
    expired = jwt.encode(
        {"iss": ISSUER, "sub": "u", "aud": AUDIENCE, "client_id": "svc",
         "iat": now - 1000, "nbf": now - 1000, "exp": now - 600,  # leeway(30) 초과
         "jti": "exp1", "typ": "access", "scope": "rebalance"},
        k.private, algorithm="RS256", headers={"kid": k.kid})
    resp = rs.get("/any", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401 and resp.json()["error"] == "invalid_token", resp.text
    assert "만료" in resp.json()["error_description"]
    print("✓ 만료된 액세스 토큰 401 차단")


# ── 6. 변조 JWT 차단 ─────────────────────────────────────────────
def test_tampered_jwt_rejected():
    idp = _fresh_idp()
    c = TestClient(build_app(idp))
    rs = _rs_app(idp)
    tok = c.post("/oauth/token", data={"grant_type": "client_credentials",
                                       "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET}).json()["access_token"]
    # 서명부 마지막 4글자 변조 → 서명 불일치
    tampered = tok[:-4] + ("AAAA" if not tok.endswith("AAAA") else "BBBB")
    resp = rs.get("/any", headers={"Authorization": f"Bearer {tampered}"})
    assert resp.status_code == 401 and resp.json()["error"] == "invalid_token", resp.text
    print("✓ 변조된 JWT(서명 불일치) 401 차단")


# ── 7. 스코프 부족 차단 ──────────────────────────────────────────
def test_scope_enforcement():
    idp = _fresh_idp()
    # rebalance 없는 좁은 스코프 클라이언트
    idp.register_client("narrow", "pw", grants=["client_credentials"], scope="openid profile")
    c = TestClient(build_app(idp))
    rs = _rs_app(idp)
    tok = c.post("/oauth/token", data={"grant_type": "client_credentials",
                                       "client_id": "narrow", "client_secret": "pw"}).json()["access_token"]
    ok = rs.get("/any", headers={"Authorization": f"Bearer {tok}"})
    assert ok.status_code == 200, ok.text
    denied = rs.get("/secure", headers={"Authorization": f"Bearer {tok}"})
    assert denied.status_code == 403 and denied.json()["error"] == "insufficient_scope", denied.text
    print("✓ 스코프 부족 시 403 insufficient_scope (RS 권한 분리)")


# ── 8. 잘못된 클라이언트 자격 차단 ───────────────────────────────
def test_invalid_client():
    idp = _fresh_idp()
    c = TestClient(build_app(idp))
    bad = c.post("/oauth/token", data={"grant_type": "client_credentials",
                                       "client_id": CLIENT_ID, "client_secret": "WRONG"})
    assert bad.status_code == 401 and bad.json()["error"] == "invalid_client", bad.text
    print("✓ 잘못된 client_secret 401 invalid_client")


# ── 9. jarvis_link 브리지 배선 ───────────────────────────────────
def test_jarvis_link_bridge():
    class _FakeLink:
        def __init__(self): self._pending = {"ABCD1234": {"context": {"sub": "owner", "scope": "openid rebalance"}}}
        def _consume_approval_code(self, code):
            rec = self._pending.pop(code, None)
            return (True, rec["context"]) if rec else (False, None)
    idp = _fresh_idp()
    bind_jarvis_link(idp, _FakeLink())
    c = TestClient(build_app(idp))
    r = c.post("/oauth/token", data={"grant_type": "authorization_code", "code": "ABCD1234",
                                     "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
    assert r.status_code == 200 and idp.verify_access(r.json()["access_token"])["sub"] == "owner"
    # 일회성: 같은 코드 재사용 불가
    again = c.post("/oauth/token", data={"grant_type": "authorization_code", "code": "ABCD1234",
                                         "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
    assert again.status_code == 400, "승인코드는 1회성이어야 함"
    print("✓ jarvis_link 승인코드 ↔ Authorization Code 브리지(1회성)")


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"\n✅ ALL {len(tests)} TESTS PASSED")


if __name__ == "__main__":
    _run()
