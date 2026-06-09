"""
J.A.R.V.I.S — 기기 바인딩 / 제로 트러스트 패키지.

access_token 을 '기기 종속적 출입증'으로 만든다(RFC 7800 cnf + RFC 9449 DPoP 계열).
토큰이 탈취돼도 기기 비공개키 없이는 요청 서명을 만들 수 없어 RS 가 원격 거부한다.

구성:
  DeviceRegistry     — 기기 UUID ↔ 공개키/소유자/지문/상태 등록 저장소
  ProofReplayCache   — 요청 증명 jti 추적(리플레이 차단)
  build_proof        — 기기 비공개키 요청 서명(클라이언트 참조 구현)
  verify_proof       — 요청 증명 검증(RS)
  DeviceGuard        — Bearer + 기기 결속 + 서명 검증 FastAPI 의존성
  public_jwk/jkt_from_jwk — EC 공개키 ↔ JWK ↔ RFC7638 지문

idp.py 접점: grant_*(..., cnf={"jkt": <지문>}) 으로 토큰에 기기 결속(옵션, 비파괴).
"""
from .registry import DeviceRegistry
from .proof import (ProofReplayCache, build_proof, verify_proof,
                    public_jwk, jkt_from_jwk, PROOF_TYP, IAT_WINDOW)
from .guard import DeviceGuard

__all__ = [
    "DeviceRegistry", "ProofReplayCache", "build_proof", "verify_proof",
    "public_jwk", "jkt_from_jwk", "DeviceGuard", "PROOF_TYP", "IAT_WINDOW",
]
