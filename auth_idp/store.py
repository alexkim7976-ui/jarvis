"""
J.A.R.V.I.S — OIDC IDP: 리프레시 토큰 저장소 + RTR(회전)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
리프레시 토큰을 jti 단위로 추적한다(빅테크 RTR 관행).
  · 정상: 1회 소비(used) 후 같은 family 로 새 토큰 발급 → 회전.
  · 도난 탐지: 이미 used 인 jti 가 다시 오면 도난으로 간주 → family 전체 폐기.
  · 메모리 한정·스레드 세이프. 만료/폐기 항목은 주기적으로 정리(메모리 DoS 방지).
"""
from __future__ import annotations
import time, threading, secrets


class RefreshTokenStore:
    def __init__(self, max_records: int = 4096):
        self._lock = threading.RLock()
        self._recs: dict[str, dict] = {}     # jti -> rec
        self._max = max_records

    def new_family(self) -> str:
        return secrets.token_urlsafe(8)

    def register(self, jti: str, *, sub: str, family_id: str,
                 client_id: str, scope: str, exp: float):
        with self._lock:
            self._prune_locked()
            self._recs[jti] = {"sub": sub, "family_id": family_id,
                               "client_id": client_id, "scope": scope,
                               "exp": exp, "used": False, "revoked": False}

    def consume(self, jti: str) -> tuple[bool, str, dict | None]:
        """RTR 소비 검증. Returns (ok, reason, rec_snapshot)."""
        now = time.time()
        with self._lock:
            rec = self._recs.get(jti)
            if not rec:
                return False, "unknown_token", None
            if rec["revoked"]:
                return False, "revoked", None
            if now > rec["exp"]:
                self._recs.pop(jti, None)
                return False, "expired", None
            if rec["used"]:
                # 재사용 = 도난 신호 → family 전체 무력화
                self._revoke_family_locked(rec["family_id"])
                return False, "reuse_detected", None
            rec["used"] = True
            return True, "ok", dict(rec)

    def revoke_family(self, family_id: str):
        with self._lock:
            self._revoke_family_locked(family_id)

    def _revoke_family_locked(self, family_id: str):
        for r in self._recs.values():
            if r["family_id"] == family_id:
                r["revoked"] = True

    def _prune_locked(self):
        now = time.time()
        for j in [j for j, r in self._recs.items() if now > r["exp"] or r["revoked"]]:
            self._recs.pop(j, None)
        if len(self._recs) >= self._max:   # 상한 초과 시 임박 만료부터 축출
            doomed = sorted(self._recs.items(), key=lambda kv: kv[1]["exp"])
            for j, _ in doomed[: len(self._recs) - self._max + 1]:
                self._recs.pop(j, None)

    def stats(self) -> dict:
        with self._lock:
            return {"records": len(self._recs),
                    "active": sum(1 for r in self._recs.values()
                                  if not r["used"] and not r["revoked"])}
