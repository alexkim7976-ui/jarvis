"""
J.A.R.V.I.S — 기기 바인딩 / 제로 트러스트 테스트 (기존 53개와 독립 실행)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
실행: .venv/bin/python test_device_trust.py
검증:
  · cnf(jkt) 기기 바인딩 토큰 발급 + 정상 기기 서명 요청 통과
  · 탈취 토큰을 '다른 기기'에서 사용 → 원격 거부(바인딩 불일치/서명 불가)
  · 바디 변조 / 리플레이 / 만료 타임스탬프 / 미등록·폐기 기기 차단
  · 비바인딩(cnf 없는) 토큰은 기존 ResourceGuard 라우트에서 그대로 동작(하위호환)
"""
import tempfile, time
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from auth_idp import (KeyManager, IdentityProvider, ResourceGuard,
                      install_error_handler)
from device_trust import (DeviceRegistry, DeviceGuard, build_proof,
                          public_jwk, jkt_from_jwk)

CLIENT_ID, CLIENT_SECRET = "jarvis-mobile", "s3cr3t-shh"
BASE = "http://testserver"


def _fresh_idp() -> IdentityProvider:
    tmp = Path(tempfile.mkdtemp()) / "dev_test_key.pem"
    idp = IdentityProvider(keys=KeyManager(key_path=str(tmp)))
    idp.register_client(CLIENT_ID, CLIENT_SECRET,
                        grants=["client_credentials", "authorization_code", "refresh_token"],
                        scope="openid profile rebalance")
    return idp


def _new_device():
    """Secure Enclave 시뮬레이션: P-256 키쌍 + UUID 생성."""
    priv = ec.generate_private_key(ec.SECP256R1())
    return priv, public_jwk(priv)


def _bound_token(idp, jwk) -> str:
    """해당 기기 지문(jkt)에 cnf 로 결속된 access_token 발급."""
    resp = idp.grant_client_credentials(CLIENT_ID, CLIENT_SECRET,
                                        cnf={"jkt": jkt_from_jwk(jwk)})
    return resp["access_token"]


def _rs_app(idp, registry) -> tuple[TestClient, DeviceGuard]:
    rs = FastAPI()
    install_error_handler(rs)
    guard = DeviceGuard(idp, registry)

    @rs.post("/macro/run")
    async def run(claims: dict = Depends(guard.require("rebalance"))):
        return {"sub": claims["sub"], "ok": True}

    # 하위호환 확인용: 기기 바인딩 없는 일반 RS 라우트
    plain = ResourceGuard(idp)

    @rs.get("/plain")
    async def plain_(claims: dict = Depends(plain.require("rebalance"))):
        return {"sub": claims["sub"]}

    return TestClient(rs), guard


def _signed_headers(priv, token, *, body=b"", path="/macro/run", method="POST",
                    iat=None, jti=None, device_id="dev-1"):
    proof = build_proof(priv, method=method, url=f"{BASE}{path}",
                        access_token=token, body=body, iat=iat, jti=jti)
    return {"Authorization": f"Bearer {token}",
            "X-JARVIS-Device": device_id, "X-JARVIS-Proof": proof}


# ── 1. 정상 흐름: 등록된 기기의 서명 요청 통과 ───────────────────
def test_happy_path_signed_request():
    idp = _fresh_idp(); reg = DeviceRegistry()
    priv, jwk = _new_device()
    reg.bind("dev-1", jwk, owner_sub=CLIENT_ID, platform="ios")
    c, _ = _rs_app(idp, reg)
    tok = _bound_token(idp, jwk)
    body = b'{"target":"AAPL","action":"rebalance"}'
    r = c.post("/macro/run", content=body, headers=_signed_headers(priv, tok, body=body))
    assert r.status_code == 200 and r.json()["ok"], r.text
    print("✓ 등록 기기 ES256 요청 서명 → 민감 명령 실행 허용")


# ── 2. 탈취 토큰을 '다른 기기'에서 사용 → 거부 ──────────────────
def test_stolen_token_other_device_rejected():
    idp = _fresh_idp(); reg = DeviceRegistry()
    victim_priv, victim_jwk = _new_device()
    reg.bind("dev-victim", victim_jwk, owner_sub=CLIENT_ID)
    tok = _bound_token(idp, victim_jwk)            # 피해자 기기에 결속된 토큰

    # 공격자: 자기 기기 등록 + 자기 키로 서명 + 피해자 토큰 제시
    atk_priv, atk_jwk = _new_device()
    reg.bind("dev-attacker", atk_jwk, owner_sub="attacker")
    c, _ = _rs_app(idp, reg)
    hdr = _signed_headers(atk_priv, tok, device_id="dev-attacker")
    r = c.post("/macro/run", content=b"", headers=hdr)
    assert r.status_code == 401 and r.json()["error"] == "invalid_token", r.text
    assert "바인딩 불일치" in r.json()["error_description"]
    print("✓ 탈취 토큰 + 타 기기 → cnf 바인딩 불일치 401 원격 거부")


# ── 3. 피해자 기기로 위장(올바른 device_id, 키는 공격자) → 서명불가 ─
def test_stolen_token_forged_signature_rejected():
    idp = _fresh_idp(); reg = DeviceRegistry()
    victim_priv, victim_jwk = _new_device()
    reg.bind("dev-victim", victim_jwk, owner_sub=CLIENT_ID)
    tok = _bound_token(idp, victim_jwk)
    atk_priv, _ = _new_device()                    # 공격자는 피해자 비공개키가 없음
    c, _ = _rs_app(idp, reg)
    hdr = _signed_headers(atk_priv, tok, device_id="dev-victim")  # 등록 ID 도용
    r = c.post("/macro/run", content=b"", headers=hdr)
    assert r.status_code == 401, r.text
    assert "서명 검증 실패" in r.json()["error_description"]
    print("✓ 기기 ID 도용했지만 비공개키 부재 → 서명 검증 실패 401")


# ── 4. 바디 변조 차단 ───────────────────────────────────────────
def test_body_tamper_rejected():
    idp = _fresh_idp(); reg = DeviceRegistry()
    priv, jwk = _new_device()
    reg.bind("dev-1", jwk, owner_sub=CLIENT_ID)
    c, _ = _rs_app(idp, reg)
    tok = _bound_token(idp, jwk)
    hdr = _signed_headers(priv, tok, body=b'{"amount":100}')      # 100 으로 서명
    r = c.post("/macro/run", content=b'{"amount":999999}', headers=hdr)  # 999999 로 전송
    assert r.status_code == 401, r.text
    assert "서명 검증 실패" in r.json()["error_description"]
    print("✓ 서명 후 바디 변조(bh 불일치) 401 차단")


# ── 5. 리플레이(동일 서명 재전송) 차단 ──────────────────────────
def test_replay_rejected():
    idp = _fresh_idp(); reg = DeviceRegistry()
    priv, jwk = _new_device()
    reg.bind("dev-1", jwk, owner_sub=CLIENT_ID)
    c, _ = _rs_app(idp, reg)
    tok = _bound_token(idp, jwk)
    hdr = _signed_headers(priv, tok, body=b"x", jti="fixed-nonce-1")
    first = c.post("/macro/run", content=b"x", headers=hdr)
    assert first.status_code == 200, first.text
    second = c.post("/macro/run", content=b"x", headers=hdr)      # 동일 proof 재전송
    assert second.status_code == 401 and "replayed" in second.json()["error_description"], second.text
    print("✓ 동일 요청 증명 재전송(jti 재사용) 401 리플레이 차단")


# ── 6. 만료 타임스탬프 차단 ─────────────────────────────────────
def test_stale_timestamp_rejected():
    idp = _fresh_idp(); reg = DeviceRegistry()
    priv, jwk = _new_device()
    reg.bind("dev-1", jwk, owner_sub=CLIENT_ID)
    c, _ = _rs_app(idp, reg)
    tok = _bound_token(idp, jwk)
    hdr = _signed_headers(priv, tok, body=b"x", iat=int(time.time()) - 600)  # 10분 전
    r = c.post("/macro/run", content=b"x", headers=hdr)
    assert r.status_code == 401 and "iat_out_of_window" in r.json()["error_description"], r.text
    print("✓ 시계 창 초과(오래된 iat) 401 차단")


# ── 7. 미등록 / 폐기 기기 차단 ──────────────────────────────────
def test_unknown_and_revoked_device_rejected():
    idp = _fresh_idp(); reg = DeviceRegistry()
    priv, jwk = _new_device()
    c, _ = _rs_app(idp, reg)
    tok = _bound_token(idp, jwk)
    # 미등록
    r1 = c.post("/macro/run", content=b"", headers=_signed_headers(priv, tok))
    assert r1.status_code == 401 and "미등록 기기" in r1.json()["error_description"], r1.text
    # 등록 후 폐기(분실 시나리오)
    reg.bind("dev-1", jwk, owner_sub=CLIENT_ID)
    reg.revoke("dev-1")
    r2 = c.post("/macro/run", content=b"", headers=_signed_headers(priv, tok))
    assert r2.status_code == 401 and "폐기된 기기" in r2.json()["error_description"], r2.text
    print("✓ 미등록 기기 + 분실 폐기 기기 401 원격 차단")


# ── 8. 비바인딩 토큰 / 서명헤더 누락 차단 ───────────────────────
def test_unbound_token_and_missing_proof_rejected():
    idp = _fresh_idp(); reg = DeviceRegistry()
    priv, jwk = _new_device()
    reg.bind("dev-1", jwk, owner_sub=CLIENT_ID)
    c, _ = _rs_app(idp, reg)
    # cnf 없는 일반 토큰으로 기기 라우트 진입 시도
    plain_tok = idp.grant_client_credentials(CLIENT_ID, CLIENT_SECRET)["access_token"]
    r1 = c.post("/macro/run", content=b"", headers=_signed_headers(priv, plain_tok))
    assert r1.status_code == 401 and "cnf 없음" in r1.json()["error_description"], r1.text
    # 바인딩 토큰이지만 서명 헤더 없이
    tok = _bound_token(idp, jwk)
    r2 = c.post("/macro/run", content=b"", headers={"Authorization": f"Bearer {tok}"})
    assert r2.status_code == 401 and "서명 헤더" in r2.json()["error_description"], r2.text
    print("✓ 비바인딩 토큰 + 서명헤더 누락 401 차단")


# ── 9. 하위호환: 비바인딩 토큰은 기존 RS 라우트에서 정상 동작 ───
def test_backward_compat_plain_route():
    idp = _fresh_idp(); reg = DeviceRegistry()
    c, _ = _rs_app(idp, reg)
    plain_tok = idp.grant_client_credentials(CLIENT_ID, CLIENT_SECRET)["access_token"]
    r = c.get("/plain", headers={"Authorization": f"Bearer {plain_tok}"})
    assert r.status_code == 200, r.text
    print("✓ 비바인딩 토큰 → 기존 ResourceGuard 라우트 정상(하위호환 보존)")


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"\n✅ ALL {len(tests)} TESTS PASSED")


if __name__ == "__main__":
    _run()
