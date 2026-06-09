"""
J.A.R.V.I.S — Cross-Validation 샌드박스 스냅샷 캐시

정제·합의된 시장 데이터를 data/sandbox/market_snapshot.json 에 원자적으로 저장한다.
백그라운드 워커는 캐시만 갱신(Gemini 0회), On-Demand 요청 시 compact_digest 만 주입.

환경변수:
  CV_CACHE_ENABLED=1
  CV_CACHE_SNAPSHOT_FILE=market_snapshot.json   # SANDBOX_ROOT 기준 상대 경로
  CV_CACHE_MAX_AGE_SEC=3600                     # 스냅샷 TTL (On-Demand 재사용)
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent

_lock = threading.Lock()
_override_path: Path | None = None


def cv_cache_enabled() -> bool:
    return os.getenv("CV_CACHE_ENABLED", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def cv_cache_max_age_sec() -> int:
    return max(60, int(os.getenv("CV_CACHE_MAX_AGE_SEC", "3600")))


def snapshot_file_name() -> str:
    return os.getenv("CV_CACHE_SNAPSHOT_FILE", "market_snapshot.json").strip() or "market_snapshot.json"


def set_snapshot_path_for_tests(path: Path | None) -> None:
    """테스트 격리용 — None 이면 기본 샌드박스 경로."""
    global _override_path
    _override_path = path


def snapshot_path() -> Path:
    if _override_path is not None:
        return _override_path
    rel = snapshot_file_name()
    try:
        from sandbox import resolve_in_sandbox, init_sandbox
        init_sandbox(create_standard_dirs=False)
        return resolve_in_sandbox(rel, create_parents=True)
    except Exception:
        p = PROJECT_ROOT / "data" / "sandbox" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """temp + os.replace 패턴으로 원자적 JSON 기록."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        suffix=".tmp",
        prefix=f"{path.stem}_",
        dir=str(path.parent),
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _empty_store() -> dict[str, Any]:
    return {"version": 1, "snapshots": {}, "updated_at": 0}


def read_store() -> dict[str, Any]:
    path = snapshot_path()
    if not path.is_file():
        return _empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_store()
        data.setdefault("version", 1)
        data.setdefault("snapshots", {})
        return data
    except Exception:
        return _empty_store()


def write_store(store: dict[str, Any]) -> None:
    store["updated_at"] = int(time.time())
    with _lock:
        write_json_atomic(snapshot_path(), store)


def normalize_ticker_key(ticker: str) -> str:
    return (ticker or "").strip().upper()


def upsert_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """단일 티커 스냅샷을 파일에 병합 저장."""
    key = normalize_ticker_key(snapshot.get("ticker") or snapshot.get("query") or "")
    if not key:
        raise ValueError("snapshot.ticker required")
    snapshot = dict(snapshot)
    snapshot["ticker"] = key
    snapshot.setdefault("timestamp", int(time.time()))

    with _lock:
        store = read_store()
        snaps = store.setdefault("snapshots", {})
        snaps[key] = snapshot
        store["updated_at"] = int(time.time())
        write_json_atomic(snapshot_path(), store)
    return snapshot


def get_snapshot(ticker_or_query: str) -> dict[str, Any] | None:
    key = normalize_ticker_key(ticker_or_query)
    store = read_store()
    snap = (store.get("snapshots") or {}).get(key)
    if snap:
        return snap
    # Yahoo 형식 005930.KS 등 그대로, 또는 query 매칭
    for s in (store.get("snapshots") or {}).values():
        if normalize_ticker_key(s.get("query", "")) == key:
            return s
        if (s.get("name") or "").strip().upper() == key:
            return s
    return None


def get_compact_digest(ticker_or_query: str) -> str | None:
    snap = get_snapshot(ticker_or_query)
    if not snap:
        return None
    digest = (snap.get("compact_digest") or "").strip()
    return digest or None


def is_snapshot_fresh(snapshot: dict[str, Any], max_age_sec: int | None = None) -> bool:
    if not snapshot:
        return False
    age_limit = cv_cache_max_age_sec() if max_age_sec is None else max_age_sec
    ts = int(snapshot.get("timestamp") or 0)
    return ts > 0 and (time.time() - ts) <= age_limit


def get_latest_snapshot() -> dict[str, Any] | None:
    """가장 최근 timestamp 의 스냅샷 (아까 그 종목 등 지시어용)."""
    store = read_store()
    snaps = list((store.get("snapshots") or {}).values())
    if not snaps:
        return None
    return max(snaps, key=lambda s: int(s.get("timestamp") or 0))


def clear_snapshots() -> None:
    """테스트 정리용."""
    with _lock:
        path = snapshot_path()
        if path.is_file():
            path.unlink(missing_ok=True)


__all__ = [
    "cv_cache_enabled",
    "cv_cache_max_age_sec",
    "snapshot_path",
    "write_json_atomic",
    "read_store",
    "write_store",
    "upsert_snapshot",
    "get_snapshot",
    "get_latest_snapshot",
    "get_compact_digest",
    "is_snapshot_fresh",
    "set_snapshot_path_for_tests",
    "clear_snapshots",
]
