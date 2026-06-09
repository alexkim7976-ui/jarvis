"""
Local Intelligence — data/sandbox/local_analysis_report.json 원자적 캐시
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from cv_cache_manager import write_json_atomic

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_override_path: Path | None = None
_DEFAULT_FILE = "local_analysis_report.json"


def set_report_path_for_tests(path: Path | None) -> None:
    global _override_path
    _override_path = path


def report_path() -> Path:
    if _override_path is not None:
        return _override_path
    rel = __import__("os").getenv("LOCAL_INTEL_REPORT_FILE", _DEFAULT_FILE).strip() or _DEFAULT_FILE
    try:
        from sandbox import resolve_in_sandbox, init_sandbox
        init_sandbox(create_standard_dirs=False)
        return resolve_in_sandbox(rel, create_parents=True)
    except Exception:
        p = PROJECT_ROOT / "data" / "sandbox" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


def _empty_store() -> dict[str, Any]:
    return {"version": 1, "reports": {}, "updated_at": 0}


def read_store() -> dict[str, Any]:
    path = report_path()
    if not path.is_file():
        return _empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_store()
        data.setdefault("version", 1)
        data.setdefault("reports", {})
        return data
    except Exception:
        return _empty_store()


def upsert_report(report: dict[str, Any]) -> dict[str, Any]:
    key = (report.get("ticker") or report.get("query") or "").strip().upper()
    if not key:
        raise ValueError("report.ticker required")
    report = dict(report)
    report["ticker"] = key
    report.setdefault("timestamp", int(time.time()))

    store = read_store()
    store.setdefault("reports", {})[key] = report
    store["updated_at"] = int(time.time())
    write_json_atomic(report_path(), store)
    return report


def get_report(ticker_or_query: str) -> dict[str, Any] | None:
    key = (ticker_or_query or "").strip().upper()
    store = read_store()
    rep = (store.get("reports") or {}).get(key)
    if rep:
        return rep
    for r in (store.get("reports") or {}).values():
        if (r.get("query") or "").upper() == key:
            return r
    return None


def get_latest_report() -> dict[str, Any] | None:
    reps = list((read_store().get("reports") or {}).values())
    if not reps:
        return None
    return max(reps, key=lambda r: int(r.get("timestamp") or 0))


def clear_reports() -> None:
    path = report_path()
    if path.is_file():
        path.unlink(missing_ok=True)
