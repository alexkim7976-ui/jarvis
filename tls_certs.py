"""
J.A.R.V.I.S — 로컬 TLS 인증서 자동 생성/관리
──────────────────────────────────────────
Python 메인 앱 ↔ Java(Spring Boot) 백엔드 간 평문(http) 통신을 로컬 HTTPS로
암호화하기 위한 자체 서명 인증서를 생성·관리한다.

생성 산출물 (.certs/):
  · supplychain.p12      — PKCS12 키스토어 (Java server.ssl 에 주입)
  · supplychain-ca.crt   — PEM 공개 인증서 (Python requests 의 verify= 에 사용)
  · keystore.pass        — 키스토어 비밀번호 (소유자 전용 권한)

자체 서명이지만 verify=False 같은 임시방편을 쓰지 않고, 클라이언트가 이 CA
인증서를 명시적으로 신뢰하도록 하여 MITM 없이 안전하게 검증한다.
"""
from __future__ import annotations

import datetime
import ipaddress
import os
import secrets
import stat
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CERT_DIR = PROJECT_ROOT / ".certs"
P12_PATH = CERT_DIR / "supplychain.p12"
CA_PEM_PATH = CERT_DIR / "supplychain-ca.crt"
PASS_PATH = CERT_DIR / "keystore.pass"
KEY_ALIAS = "supplychain"

# 인증서 유효기간 (자체 서명 leaf 권장 상한 ~825일)
_VALID_DAYS = 825
_RENEW_BEFORE_DAYS = 7  # 만료 7일 전이면 재발급


def _restrict(path: Path, owner_only: bool = True) -> None:
    """소유자 전용 권한으로 제한 (크로스플랫폼)."""
    try:
        if os.name == "nt":
            # Windows: 상속 제거 후 현재 사용자에게만 권한 부여
            _windows_lockdown(path)
        else:
            mode = stat.S_IRUSR | stat.S_IWUSR if owner_only else stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
            os.chmod(path, mode)
    except OSError:
        pass


def _windows_lockdown(path: Path) -> None:
    import subprocess
    user = os.getenv("USERNAME") or os.getenv("USER") or ""
    try:
        subprocess.run(["icacls", str(path), "/inheritance:r"],
                       check=False, capture_output=True)
        if user:
            subprocess.run(["icacls", str(path), "/grant:r", f"{user}:R"],
                           check=False, capture_output=True)
    except (OSError, FileNotFoundError):
        pass


def _cert_is_valid() -> bool:
    """기존 인증서가 존재하고 만료/사용처(SAN 127.0.0.1·localhost)가 유효한지 검사."""
    if not (P12_PATH.exists() and CA_PEM_PATH.exists() and PASS_PATH.exists()):
        return False
    try:
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(CA_PEM_PATH.read_bytes())
        not_after = cert.not_valid_after_utc
        if not_after <= datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=_RENEW_BEFORE_DAYS):
            return False
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        ips = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
        dns = set(san.get_values_for_type(x509.DNSName))
        return "127.0.0.1" in ips and "localhost" in dns
    except Exception:
        return False


def _generate(password: str) -> None:
    """RSA 2048 자체 서명 인증서를 생성해 PKCS12 + PEM 으로 기록."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "JARVIS Supply Chain (Local Dev)"),
    ])
    san = x509.SubjectAlternativeName([
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.IPAddress(ipaddress.ip_address("::1")),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=_VALID_DAYS))
        .add_extension(san, critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_encipherment=True, key_cert_sign=True,
                content_commitment=False, data_encipherment=False, key_agreement=False,
                crl_sign=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )

    CERT_DIR.mkdir(exist_ok=True)
    p12_bytes = pkcs12.serialize_key_and_certificates(
        name=KEY_ALIAS.encode(),
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode()),
    )
    P12_PATH.write_bytes(p12_bytes)
    CA_PEM_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    PASS_PATH.write_text(password, encoding="utf-8")

    # 비밀키가 든 키스토어/비밀번호는 소유자 전용, 공개 인증서는 읽기 허용
    _restrict(P12_PATH, owner_only=True)
    _restrict(PASS_PATH, owner_only=True)
    _restrict(CA_PEM_PATH, owner_only=False)


def ensure_certificates() -> dict | None:
    """TLS 인증서 자산을 보장(없거나 만료 시 생성)하고 경로/비밀번호를 반환.
    cryptography 미설치 등으로 실패하면 None (호출부에서 평문 폴백 처리)."""
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return None

    if not _cert_is_valid():
        password = secrets.token_urlsafe(24)
        try:
            _generate(password)
        except Exception:
            return None

    try:
        password = PASS_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None

    return {
        "keystore": str(P12_PATH),
        "ca_cert": str(CA_PEM_PATH),
        "password": password,
        "alias": KEY_ALIAS,
        "type": "PKCS12",
    }


def inject_env(info: dict) -> None:
    """생성된 TLS 자산 경로를 os.environ 에 주입.
    · Java 자식 프로세스: SUPPLY_CHAIN_KEYSTORE / *_PASSWORD / *_TYPE / KEY_ALIAS / SSL_ENABLED
    · Python 클라이언트: SUPPLY_CHAIN_CA_CERT (requests verify= 경로)"""
    os.environ["SUPPLY_CHAIN_SSL_ENABLED"] = "true"
    os.environ["SUPPLY_CHAIN_KEYSTORE"] = info["keystore"]
    os.environ["SUPPLY_CHAIN_KEYSTORE_PASSWORD"] = info["password"]
    os.environ["SUPPLY_CHAIN_KEYSTORE_TYPE"] = info["type"]
    os.environ["SUPPLY_CHAIN_KEY_ALIAS"] = info["alias"]
    os.environ["SUPPLY_CHAIN_CA_CERT"] = info["ca_cert"]


LINK_CERT_PATH = CERT_DIR / "jarvis-link.crt"
LINK_KEY_PATH  = CERT_DIR / "jarvis-link.key"


def _pem_cert_valid(cert_path: Path) -> bool:
    if not (cert_path.exists() and LINK_KEY_PATH.exists()):
        return False
    try:
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        not_after = cert.not_valid_after_utc
        return not_after > datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=_RENEW_BEFORE_DAYS)
    except Exception:
        return False


def ensure_link_pem() -> dict | None:
    """uvicorn/ASGI(JARVIS Link)용 PEM 인증서+키를 보장(없거나 만료 시 생성).
    Returns {"certfile","keyfile","ca_cert"} 또는 cryptography 미설치 시 None."""
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return None
    if not _pem_cert_valid(LINK_CERT_PATH):
        try:
            _generate_pem()
        except Exception:
            return None
    return {"certfile": str(LINK_CERT_PATH), "keyfile": str(LINK_KEY_PATH),
            "ca_cert": str(LINK_CERT_PATH)}


def _generate_pem() -> None:
    """RSA 2048 자체 서명 PEM 인증서/키 생성(SAN: localhost/127.0.0.1/::1)."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "JARVIS Link (Local Dev)"),
    ])
    san = x509.SubjectAlternativeName([
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.IPAddress(ipaddress.ip_address("::1")),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=_VALID_DAYS))
        .add_extension(san, critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    CERT_DIR.mkdir(exist_ok=True)
    LINK_KEY_PATH.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    LINK_CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    _restrict(LINK_KEY_PATH, owner_only=True)
    _restrict(LINK_CERT_PATH, owner_only=False)


def ca_cert_path() -> str | None:
    """클라이언트 검증용 CA 인증서 경로. 환경변수 우선, 없으면 기본 산출물 경로."""
    env = os.getenv("SUPPLY_CHAIN_CA_CERT", "").strip()
    if env and Path(env).exists():
        return env
    if CA_PEM_PATH.exists():
        return str(CA_PEM_PATH)
    return None


if __name__ == "__main__":
    info = ensure_certificates()
    if not info:
        print("✗ 인증서 생성 실패 (cryptography 설치 확인)")
        sys.exit(1)
    print("✓ TLS 인증서 준비 완료")
    for k, v in info.items():
        if k == "password":
            v = "***"
        print(f"  {k}: {v}")
