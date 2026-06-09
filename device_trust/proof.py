"""
J.A.R.V.I.S — 기기 바인딩: 요청 서명(proof) 생성·검증 + 리플레이 방어
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DPoP(RFC 9449) 계열의 요청 증명. 기기 비공개키(ES256/P-256)로 매 요청의
메서드·URL·바디·타임스탬프·토큰지문을 서명한다 → 탈취 토큰 단독 사용 차단.

proof payload(JWS, alg=ES256, typ=jarvis-proof+jws):
  htm : HTTP 메서드          htu : 정규화 URL(scheme://host/path)
  ath : SHA256(access_token) bh  : SHA256(request_body)
  iat : 발급시각(±window)    jti : 1회용 nonce(리플레이 차단)
"""
from __future__ import annotations
import base64, hashlib, json, time, threading, secrets

import jwt
from jwt.algorithms import ECAlgorithm

PROOF_TYP   = "jarvis-proof+jws"
IAT_WINDOW  = 60          # 서버 시계와 허용 오차(초)


# ── 헬퍼 ────────────────────────────────────────────────────────
def _b64u_sha256(data: bytes) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()


def _as_bytes(x) -> bytes:
    return x if isinstance(x, (bytes, bytearray)) else str(x or "").encode()


def public_jwk(private_key) -> dict:
    """cryptography EC 비공개키 → 공개 JWK(dict). (등록·테스트 공용)"""
    return json.loads(ECAlgorithm.to_jwk(private_key.public_key()))


def jkt_from_jwk(jwk: dict) -> str:
    """RFC 7638 JWK thumbprint(EC) — access_token cnf.jkt 와 결속되는 기기 지문."""
    canon = json.dumps({"crv": jwk["crv"], "kty": jwk["kty"], "x": jwk["x"], "y": jwk["y"]},
                       separators=(",", ":"), sort_keys=True)
    return _b64u_sha256(canon.encode())


def build_proof(private_key, *, method, url, access_token, body=b"",
                iat=None, jti=None) -> str:
    """기기 비공개키로 요청 증명 서명(클라이언트/테스트 참조 구현)."""
    payload = {
        "htm": method.upper(),
        "htu": url,
        "ath": _b64u_sha256(_as_bytes(access_token)),
        "bh":  _b64u_sha256(_as_bytes(body)),
        "iat": int(iat if iat is not None else time.time()),
        "jti": jti or secrets.token_urlsafe(12),
    }
    return jwt.encode(payload, private_key, algorithm="ES256", headers={"typ": PROOF_TYP})


# ── 리플레이 방어 캐시 ──────────────────────────────────────────
class ProofReplayCache:
    """소비된 proof jti 를 TTL 동안 추적 → 동일 서명 재전송(리플레이) 차단."""
    def __init__(self, ttl: int = 120, max_records: int = 8192):
        self._lock = threading.RLock()
        self._seen: dict[str, float] = {}
        self._ttl = ttl
        self._max = max_records

    def register(self, jti: str, now: float | None = None) -> bool:
        """처음 본 jti 면 True(등록), 이미 본 jti 면 False(리플레이)."""
        now = now or time.time()
        with self._lock:
            self._prune_locked(now)
            if jti in self._seen:
                return False
            self._seen[jti] = now + self._ttl
            return True

    def _prune_locked(self, now: float):
        for j in [j for j, e in self._seen.items() if now > e]:
            self._seen.pop(j, None)
        if len(self._seen) >= self._max:        # 상한 초과 → 임박 만료부터 축출
            doomed = sorted(self._seen.items(), key=lambda kv: kv[1])
            for j, _ in doomed[: len(self._seen) - self._max + 1]:
                self._seen.pop(j, None)


# ── 검증 (자원 서버가 사용) ─────────────────────────────────────
def verify_proof(proof_jws: str, public_jwk_: dict, *, method, url, access_token,
                 body=b"", replay: ProofReplayCache | None = None,
                 now: float | None = None, iat_window: int = IAT_WINDOW
                 ) -> tuple[bool, str, dict | None]:
    """기기 공개키로 요청 증명 검증. Returns (ok, reason, claims).

    순서: 서명검증 → 필드결속(htm/htu/ath/bh) → iat창 → jti 리플레이.
    (실패한 서명으로는 nonce 를 소비하지 않도록 리플레이 등록은 맨 마지막)
    """
    now = now if now is not None else time.time()
    # 1) typ 헤더 + ES256 서명 검증(alg 고정 — 다운그레이드 차단)
    try:
        if jwt.get_unverified_header(proof_jws).get("typ") != PROOF_TYP:
            return False, "bad_proof_typ", None
        key = ECAlgorithm.from_jwk(json.dumps(public_jwk_))
        claims = jwt.decode(proof_jws, key, algorithms=["ES256"],
                            options={"verify_aud": False, "verify_exp": False,
                                     "verify_nbf": False, "verify_iss": False,
                                     "require": ["htm", "htu", "ath", "bh", "iat", "jti"]})
    except jwt.MissingRequiredClaimError:
        return False, "proof_missing_claim", None
    except Exception:
        return False, "proof_bad_signature", None
    # 2) 요청 결속 — 무엇을·어디로·무슨 토큰·무슨 바디로 보냈는지 전부 일치
    if claims["htm"] != method.upper():
        return False, "method_mismatch", None
    if claims["htu"] != url:
        return False, "url_mismatch", None
    if claims["ath"] != _b64u_sha256(_as_bytes(access_token)):
        return False, "token_binding_mismatch", None
    if claims["bh"] != _b64u_sha256(_as_bytes(body)):
        return False, "body_tampered", None
    # 3) 시계 창(미래/과거 skew 모두 차단)
    if abs(now - int(claims["iat"])) > iat_window:
        return False, "iat_out_of_window", None
    # 4) 리플레이 — 마지막에 소비
    if replay is not None and not replay.register(claims["jti"], now):
        return False, "replayed", None
    return True, "ok", claims
