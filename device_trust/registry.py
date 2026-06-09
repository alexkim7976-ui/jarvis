"""
J.A.R.V.I.S — 기기 바인딩: 등록 저장소(Device Registry)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
기기 UUID → (공개키 JWK, 소유자 sub, 지문 jkt, 상태) 매핑.
최초 인증(jarvis_link 승인코드) 시 bind() 으로 등록 → 이후 요청 서명 검증의 근거.
분실/도난 기기는 revoke() 로 즉시 원격 차단(제로 트러스트: 신뢰는 폐기 가능).
스레드 세이프(RefreshTokenStore 와 동일 RLock 패턴).
"""
from __future__ import annotations
import time, threading

from .proof import public_jwk, jkt_from_jwk


class DeviceRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._devices: dict[str, dict] = {}
        # attestation 검증 훅(2차 스프린트): code(attestation, jwk, platform)->bool.
        # 1차에서는 None → 무검증 통과(인터페이스만 선개통).
        self.attestation_verifier = None

    def bind(self, device_id: str, public_jwk_: dict, owner_sub: str,
             platform: str = "unknown", attestation=None) -> str:
        """기기 공개키 등록. Returns jkt(cnf 에 박을 지문). 재등록 시 갱신."""
        if not device_id or not public_jwk_ or not owner_sub:
            raise ValueError("device_id/public_jwk/owner_sub 필수")
        if self.attestation_verifier is not None:           # 2차: 앱/기기 무결성
            if not self.attestation_verifier(attestation, public_jwk_, platform):
                raise ValueError("attestation 검증 실패")
        jkt = jkt_from_jwk(public_jwk_)
        with self._lock:
            self._devices[device_id] = {
                "device_id": device_id, "owner_sub": owner_sub,
                "jwk": public_jwk_, "jkt": jkt, "platform": platform,
                "status": "active", "created": time.time(), "last_seen": None,
            }
        return jkt

    def get(self, device_id: str) -> dict | None:
        with self._lock:
            d = self._devices.get(device_id or "")
            return dict(d) if d else None

    def revoke(self, device_id: str) -> bool:
        """분실/도난 기기 폐기 — 이후 모든 요청 거부."""
        with self._lock:
            d = self._devices.get(device_id or "")
            if d:
                d["status"] = "revoked"
            return bool(d)

    def touch(self, device_id: str):
        with self._lock:
            d = self._devices.get(device_id or "")
            if d:
                d["last_seen"] = time.time()

    def devices_of(self, owner_sub: str) -> list[dict]:
        with self._lock:
            return [dict(d) for d in self._devices.values() if d["owner_sub"] == owner_sub]

    # 편의: cryptography 비공개키로 바로 등록(테스트/내장 클라이언트용)
    def bind_keypair(self, device_id, private_key, owner_sub, platform="unknown") -> str:
        return self.bind(device_id, public_jwk(private_key), owner_sub, platform)
