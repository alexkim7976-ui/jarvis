"""jarvis_v2 CV 캐시 브릿지 단위 테스트."""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("CV_CACHE_ENABLED", "1")


def test_apply_cv_cache_injects_digest():
    from cv_cache_manager import set_snapshot_path_for_tests, upsert_snapshot, clear_snapshots
    import jarvis_v2

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "market_snapshot.json"
        set_snapshot_path_for_tests(p)
        try:
            upsert_snapshot({
                "ticker": "NVDA",
                "query": "NVDA",
                "compact_digest": "Yahoo/FMP 합의 가격 $120.50.",
                "timestamp": int(time.time()),
            })
            out = jarvis_v2._apply_cv_cache_to_user_text(
                "자비스, 엔비디아 브리핑해 줘", history=[],
            )
            assert "[시장스냅샷]" in out
            assert "120.50" in out
            assert "RAW_OHLCV" not in out
        finally:
            clear_snapshots()
            set_snapshot_path_for_tests(None)
    print("✓ apply_cv_cache_injects_digest")


def test_deictic_uses_latest_snapshot():
    from cv_cache_manager import set_snapshot_path_for_tests, upsert_snapshot, clear_snapshots
    import jarvis_v2

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "market_snapshot.json"
        set_snapshot_path_for_tests(p)
        try:
            upsert_snapshot({
                "ticker": "TSLA",
                "query": "TSLA",
                "compact_digest": "Tesla 합의가 $250.",
                "timestamp": int(time.time()) - 10,
            })
            upsert_snapshot({
                "ticker": "NVDA",
                "query": "NVDA",
                "compact_digest": "NVDA 최신 digest.",
                "timestamp": int(time.time()),
            })
            out = jarvis_v2._apply_cv_cache_to_user_text("아까 그 종목 브리핑해 줘", history=[])
            assert "NVDA" in out or "최신 digest" in out
        finally:
            clear_snapshots()
            set_snapshot_path_for_tests(None)
    print("✓ deictic_uses_latest_snapshot")


if __name__ == "__main__":
    test_apply_cv_cache_injects_digest()
    test_deictic_uses_latest_snapshot()
    print("OK")
