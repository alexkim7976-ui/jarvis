"""
J.A.R.V.I.S — 경량 OIDC IDP: 비대칭 키 관리(RS256) + JWKS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RSA-2048 개인키로 JWT(RS256)를 서명하고, 공개키를 표준 JWKS로 노출한다.
  · 개인키는 .certs/(gitignore됨)에 0600 PKCS8 PEM으로 영속화 — 없으면 최초 1회 생성.
  · kid 는 RFC 7638 JWK 썸프린트(공개키 고정 식별자) → 키 회전 시 검증 측이 자동 선택.
  · rotate(): 새 키로 서명 전환하되 직전 공개키는 JWKS에 남겨 검증 무중단(grace).
"""
from __future__ import annotations
import os, json, base64, hashlib, threading
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _int_b64u(i: int) -> str:
    return _b64u(i.to_bytes((i.bit_length() + 7) // 8 or 1, "big"))


class _Key:
    """단일 RSA 키쌍 + 파생 JWK 메타."""
    def __init__(self, private_key: rsa.RSAPrivateKey):
        self.private = private_key
        self.public = private_key.public_key()
        nums = self.public.public_numbers()
        self.n_b64 = _int_b64u(nums.n)
        self.e_b64 = _int_b64u(nums.e)
        self.kid = self._thumbprint()

    def _thumbprint(self) -> str:
        # RFC 7638: 정규 JWK {e,kty,n} 의 SHA-256 → 안정적 kid
        canon = json.dumps({"e": self.e_b64, "kty": "RSA", "n": self.n_b64},
                           separators=(",", ":"), sort_keys=True).encode()
        return _b64u(hashlib.sha256(canon).digest())

    def jwk(self) -> dict:
        return {"kty": "RSA", "use": "sig", "alg": "RS256",
                "kid": self.kid, "n": self.n_b64, "e": self.e_b64}


class KeyManager:
    """활성 서명키 1개 + 폐기 예정 공개키(검증 한정) 목록을 관리."""
    def __init__(self, key_path: str | None = None):
        self._lock = threading.RLock()
        self._path = Path(os.path.expanduser(
            key_path or os.getenv("JARVIS_OIDC_KEY_FILE", ".certs/auth_idp_active.pem"))).resolve()
        self._active: _Key | None = None
        self._retired: list[_Key] = []     # 회전 후에도 일정 기간 검증 허용
        self._load_or_create()

    def _load_or_create(self):
        with self._lock:
            if self._path.exists():
                priv = serialization.load_pem_private_key(self._path.read_bytes(), password=None)
                self._active = _Key(priv)
            else:
                self._active = _Key(rsa.generate_private_key(public_exponent=65537, key_size=2048))
                self._persist(self._active)

    def _persist(self, key: _Key):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        pem = key.private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption())
        # 0600 보장 — 비공개키 누출 방지
        fd = os.open(str(self._path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)

    @property
    def active(self) -> _Key:
        with self._lock:
            return self._active

    def public_key_for(self, kid: str):
        """kid 로 검증용 공개키 선택(활성 + 폐기예정). 없으면 None."""
        with self._lock:
            for k in (self._active, *self._retired):
                if k and k.kid == kid:
                    return k.public
        return None

    def jwks(self) -> dict:
        with self._lock:
            keys = [self._active.jwk()] + [k.jwk() for k in self._retired]
        return {"keys": keys}

    def rotate(self) -> str:
        """새 활성키로 전환하고 직전 키(최대 2개)를 검증용으로 보존."""
        with self._lock:
            old = self._active
            self._active = _Key(rsa.generate_private_key(public_exponent=65537, key_size=2048))
            self._retired = ([old] + self._retired)[:2]
            self._persist(self._active)
            return self._active.kid
