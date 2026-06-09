"""
Cross-Validation 샌드박스 캐시 격리 테스트

실행:
  .venv/bin/python test_cv_cache.py
  .venv/bin/python -m pytest test_cv_cache.py -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("CV_CACHE_ENABLED", "1")
os.environ.setdefault("CV_CACHE_ON_DEMAND_GEMINI", "1")
os.environ.setdefault("CV_CACHE_BACKGROUND_NO_GEMINI", "1")


def _sample_report():
    from cross_validation import (
        StockData, ValidationReport, FieldConsensus,
        build_market_snapshot, build_compact_digest,
        compress_news_local, detect_anomaly_flag,
        refresh_market_snapshot_cache, reset_cv_gemini_call_count,
        get_cv_gemini_call_count, analyze_stock_on_demand,
        collect_consensus_local,
    )
    return locals()


def test_write_json_atomic_roundtrip():
    from cv_cache_manager import write_json_atomic
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "snap.json"
        payload = {"version": 1, "snapshots": {"NVDA": {"ticker": "NVDA"}}}
        write_json_atomic(path, payload)
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["snapshots"]["NVDA"]["ticker"] == "NVDA"
        print("✓ write_json_atomic roundtrip")


def test_upsert_and_read_snapshot():
    from cv_cache_manager import (
        set_snapshot_path_for_tests, upsert_snapshot, get_snapshot, clear_snapshots,
    )
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "market_snapshot.json"
        set_snapshot_path_for_tests(p)
        try:
            snap = {
                "ticker": "NVDA",
                "query": "NVDA",
                "consensus_price": 120.5,
                "anomaly_flag": False,
                "compact_digest": "Yahoo/FMP 합의 가격 120.5달러.",
                "timestamp": int(time.time()),
            }
            upsert_snapshot(snap)
            got = get_snapshot("NVDA")
            assert got is not None
            assert got["consensus_price"] == 120.5
            assert "120.5" in got["compact_digest"]
        finally:
            clear_snapshots()
            set_snapshot_path_for_tests(None)
    print("✓ upsert_and_read_snapshot")


def test_compress_news_local_no_gemini():
    from cross_validation import compress_news_local, reset_cv_gemini_call_count, get_cv_gemini_call_count
    reset_cv_gemini_call_count()
    news = [
        {"title": "NVIDIA earnings beat expectations, stock surge"},
        {"title": "Analyst upgrade on AI growth"},
    ]
    body, score = compress_news_local(news)
    assert score > 0
    assert "earnings" in body.lower() or "NVIDIA" in body
    assert get_cv_gemini_call_count() == 0
    print("✓ compress_news_local_no_gemini")


def test_build_compact_digest_excludes_raw():
    from cross_validation import (
        ValidationReport, FieldConsensus, build_compact_digest, compress_news_local,
    )
    report = ValidationReport(
        query="NVDA", name="NVIDIA", currency="USD",
        sources_attempted=["Yahoo Finance", "FMP"],
        sources_succeeded=["Yahoo Finance", "FMP"],
        sources_failed=[],
        price=FieldConsensus(120.5, [("Yahoo Finance", 120.4), ("FMP", 120.6)], [], "2/2"),
        volume=FieldConsensus(None, [], [], "없음"),
        per=FieldConsensus(None, [], [], "없음"),
        pbr=FieldConsensus(None, [], [], "없음"),
        market_cap=FieldConsensus(None, [], [], "없음"),
        eps=FieldConsensus(None, [], [], "없음"),
        change_pct=FieldConsensus(-2.1, [("Yahoo Finance", -2.1)], [], "1/1"),
        extras={}, raw=[],
    )
    body, sent = compress_news_local([{"title": "NVDA AI demand surge"}])
    digest = build_compact_digest(report, body, sent)
    assert "120.5" in digest or "120" in digest
    assert "Open" not in digest  # raw OHLCV 필드명 미포함
    assert len(digest) < 400
    print("✓ build_compact_digest_excludes_raw")


def test_refresh_cache_updates_file_gemini_zero():
    from cv_cache_manager import set_snapshot_path_for_tests, read_store, clear_snapshots
    from cross_validation import (
        StockData, refresh_market_snapshot_cache,
        reset_cv_gemini_call_count, get_cv_gemini_call_count,
    )

    reset_cv_gemini_call_count()
    mock_results = [
        StockData(
            source="Yahoo Finance", ticker="NVDA", success=True, name="NVIDIA",
            price=120.5, change_pct=-2.0, volume=1_000_000, currency="USD",
        ),
        StockData(
            source="Financial Modeling Prep", ticker="NVDA", success=True, name="NVIDIA",
            price=120.6, change_pct=-2.0, volume=1_000_100, currency="USD",
        ),
    ]

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "market_snapshot.json"
        set_snapshot_path_for_tests(p)
        try:
            with patch("cross_validation.fetch_all_parallel", return_value=mock_results), \
                 patch("cross_validation._fetch_news_local", return_value=[
                     {"title": "NVDA beats earnings, stock rally"}
                 ]):
                snap = refresh_market_snapshot_cache("NVDA", include_news=True)

            store = read_store()
            assert "NVDA" in store.get("snapshots", {})
            assert store["snapshots"]["NVDA"]["consensus_price"] is not None
            assert snap.compact_digest
            assert get_cv_gemini_call_count() == 0
        finally:
            clear_snapshots()
            set_snapshot_path_for_tests(None)
    print("✓ refresh_cache_updates_file_gemini_zero")


def test_on_demand_uses_digest_only_not_raw():
    from cv_cache_manager import set_snapshot_path_for_tests, upsert_snapshot, clear_snapshots
    from cross_validation import analyze_stock_on_demand, reset_cv_gemini_call_count, get_cv_gemini_call_count

    reset_cv_gemini_call_count()
    raw_blob = "RAW_OHLCV_DATA_" * 500
    digest = "Yahoo/FMP 합의 가격 $120.50, AI 감성 +7."

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "market_snapshot.json"
        set_snapshot_path_for_tests(p)
        try:
            upsert_snapshot({
                "ticker": "NVDA",
                "query": "NVDA",
                "name": "NVIDIA",
                "consensus_price": 120.5,
                "consensus_change_pct": -2.0,
                "anomaly_flag": False,
                "timeline_text": "합의가 테스트.",
                "news_compact_body": "headline only",
                "compact_digest": digest,
                "sources_ok": ["Yahoo Finance"],
                "timestamp": int(time.time()),
            })

            captured_prompt = []

            def fake_gemini(prompt, *a, **k):
                captured_prompt.append(prompt)
                return "선생님, 엔비디아 합의가 120.5달러입니다."

            with patch("cross_validation._enhance_briefing_with_gemini") as mock_enh:
                mock_enh.side_effect = lambda d, q: (
                    fake_gemini(d, q) if d == digest else ""
                )
                out = analyze_stock_on_demand("NVDA")

            assert out.get("cached") is True
            assert out.get("compact_digest") == digest
            assert raw_blob not in str(out)
            assert raw_blob not in (captured_prompt[0] if captured_prompt else "")
            if captured_prompt:
                assert digest in captured_prompt[0]
        finally:
            clear_snapshots()
            set_snapshot_path_for_tests(None)
    print("✓ on_demand_uses_digest_only_not_raw")


def test_analyze_stock_legacy_path_when_cache_disabled():
    from cross_validation import analyze_stock, StockData

    os.environ["CV_CACHE_ENABLED"] = "0"
    try:
        mock_results = [
            StockData(
                source="Yahoo Finance", ticker="AAPL", success=True, name="Apple",
                price=200.0, change_pct=1.0, volume=100, currency="USD",
                per=30.0, pbr=5.0, market_cap=3e12, eps=6.0,
            ),
        ]
        with patch("cross_validation.fetch_all_parallel", return_value=mock_results):
            out = analyze_stock("AAPL", on_demand=False)
        assert out["success"] is True
        assert "briefing" in out
        assert "report" in out
    finally:
        os.environ["CV_CACHE_ENABLED"] = "1"
    print("✓ analyze_stock_legacy_path_when_cache_disabled")


def test_gemini_cache_manager_digest_bridge():
    from gemini_cache_manager import market_snapshot_digest_for_prompt, inject_market_snapshot_into_user_message
    from cv_cache_manager import set_snapshot_path_for_tests, upsert_snapshot, clear_snapshots

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "market_snapshot.json"
        set_snapshot_path_for_tests(p)
        try:
            upsert_snapshot({
                "ticker": "TSLA",
                "query": "TSLA",
                "compact_digest": "Tesla 합의가 $250.",
                "timestamp": int(time.time()),
            })
            d = market_snapshot_digest_for_prompt("TSLA")
            assert d and "250" in d
            msg = inject_market_snapshot_into_user_message("브리핑해 줘", "TSLA")
            assert "[시장스냅샷]" in msg
            assert "250" in msg
        finally:
            clear_snapshots()
            set_snapshot_path_for_tests(None)
    print("✓ gemini_cache_manager_digest_bridge")


def run_all():
    tests = [
        test_write_json_atomic_roundtrip,
        test_upsert_and_read_snapshot,
        test_compress_news_local_no_gemini,
        test_build_compact_digest_excludes_raw,
        test_refresh_cache_updates_file_gemini_zero,
        test_on_demand_uses_digest_only_not_raw,
        test_analyze_stock_legacy_path_when_cache_disabled,
        test_gemini_cache_manager_digest_bridge,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
        except Exception as e:
            failed += 1
            print(f"✗ {fn.__name__}: {e}")
            import traceback
            traceback.print_exc()
    print(f"\n{'='*50}")
    print(f"결과: {len(tests)-failed}/{len(tests)} passed")
    return failed == 0


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
