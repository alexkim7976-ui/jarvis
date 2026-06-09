"""
로컬 What-If 스트레스 백테스트 단위 테스트 — Gemini 0

실행:
  .venv/bin/python test_local_stress_backtest.py
  .venv/bin/python -m pytest test_local_stress_backtest.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["LOCAL_INTEL_OLLAMA_ENABLED"] = "0"
os.environ["HYBRID_ROUTING_ENABLED"] = "1"


def _synth_close(seed=0, vol=0.02, n=120):
    rng = np.random.default_rng(seed)
    rets = rng.normal(0, vol, n)
    return 100 * np.cumprod(1 + rets)


def test_lehman_vs_covid_mdd_ordering():
    """코로나(더 높은 vol_mult)가 리먼보다 더 큰 MDD — 수학적 일관성."""
    from portfolio_stress import run_local_whatif_stress_test

    close = _synth_close(vol=0.025)
    weights = {"TEST": 1.0}
    close_map = {"TEST": close}

    res = run_local_whatif_stress_test(
        "TEST",
        portfolio_weights=weights,
        close_map=close_map,
        n_sims=5000,
        seed=42,
    )
    assert res["n_sims"] == 5000
    assert len(res["scenarios"]) == 2
    assert res["gemini_called"] is False

    by_key = {s["key"]: s for s in res["scenarios"]}
    lehman = by_key["LEHMAN_2008"]
    covid = by_key["COVID_2020"]

    assert lehman["expected_mdd_pct"] < 0
    assert covid["expected_mdd_pct"] < 0
    assert covid["var_95_pct"] < 0
    assert lehman["var_95_pct"] < 0
    # 코로나 시나리오의 일간 충격 강도(σ 배율)가 리먼보다 높음
    covid_intensity = covid["shock"]["vol_mult"] * covid["shock"]["beta_shock"]
    lehman_intensity = lehman["shock"]["vol_mult"] * lehman["shock"]["beta_shock"]
    assert covid_intensity > lehman_intensity
    print(
        f"✓ Lehman MDD={lehman['expected_mdd_pct']}% "
        f"Covid MDD={covid['expected_mdd_pct']}% "
        f"(covid σ×β={covid_intensity:.2f} > lehman {lehman_intensity:.2f})"
    )


def test_portfolio_weights_applied():
    from portfolio_stress import run_local_whatif_stress_test

    close_a = _synth_close(seed=1, vol=0.015)
    close_b = _synth_close(seed=2, vol=0.04)
    weights = {"LOW": 0.7, "HIGH": 0.3}
    close_map = {"LOW": close_a, "HIGH": close_b}

    res = run_local_whatif_stress_test(
        None,
        portfolio_weights=weights,
        close_map=close_map,
        n_sims=3000,
        seed=7,
    )
    assert res["portfolio_weights"]["LOW"] == 0.7
    assert res["portfolio_weights"]["HIGH"] == 0.3
    assert res["stress_test_score"] > 0
    print(f"✓ portfolio weights → stress_score={res['stress_test_score']}")


def test_deterministic_seed_reproducibility():
    from portfolio_stress import run_local_whatif_stress_test

    close = _synth_close()
    kw = dict(
        portfolio_weights={"X": 1.0},
        close_map={"X": close},
        n_sims=2000,
        seed=99,
    )
    a = run_local_whatif_stress_test("X", **kw)
    b = run_local_whatif_stress_test("X", **kw)
    assert a["worst"]["expected_mdd_pct"] == b["worst"]["expected_mdd_pct"]
    assert a["worst"]["var_95_pct"] == b["worst"]["var_95_pct"]
    print("✓ deterministic seed reproducibility")


def test_cache_stress_profile_gemini_zero():
    from hybrid_router import try_local_tool_execution
    from local_intelligence.report_cache import set_report_path_for_tests, read_store, clear_reports

    mock_stress = {
        "engine": "whatif_monte_carlo",
        "n_sims": 10000,
        "stress_test_score": 72.5,
        "gemini_called": False,
        "scenarios": [
            {
                "key": "LEHMAN_2008",
                "label": "2008 리먼 브라더스 쇼크",
                "expected_mdd_pct": -38.5,
                "var_95_pct": -22.1,
                "stress_score": 72.5,
            },
            {
                "key": "COVID_2020",
                "label": "2020 코로나 팬데믹 블랙스완",
                "expected_mdd_pct": -45.2,
                "var_95_pct": -28.4,
                "stress_score": 85.0,
            },
        ],
        "worst": {
            "key": "COVID_2020",
            "label": "2020 코로나 팬데믹 블랙스완",
            "expected_mdd_pct": -45.2,
            "var_95_pct": -28.4,
        },
        "summary_kr": "최악 What-If",
    }

    mock_ctx = {
        "ticker": "AAPL",
        "query": "AAPL",
        "name": "Apple",
        "consensus_price": 180.0,
        "change_pct": 0.0,
        "rsi": 50.0,
        "hybrid_score": 60,
        "bb_squeeze": False,
        "sentiment_score": 0,
        "compact_digest": "합의 180",
        "sources_ok": [],
        "volume_profile": {},
        "ma_ribbon": {},
        "valuation": {},
        "local_matrix": {},
        "dcf_valuation": {},
        "graham_valuation": {},
        "stress_test_profile": mock_stress,
        "tech_signals": {},
        "timeline_text": "",
        "anomaly_flag": False,
    }

    with tempfile.TemporaryDirectory() as td:
        rp = Path(td) / "local_analysis_report.json"
        set_report_path_for_tests(rp)
        try:
            with patch("local_intelligence.engine.gather_fusion_context", return_value=mock_ctx):
                result = try_local_tool_execution(
                    "analyze_stock_multi_source", {"query": "AAPL"},
                )
            assert result.get("gemini_called") is False
            report = result.get("report") or {}
            prof = report.get("stress_test_profile") or {}
            assert prof.get("stress_test_score") == 72.5
            assert len(prof.get("scenarios", [])) == 2
            store = read_store()
            snap = store["reports"]["AAPL"]
            assert snap["metrics_snapshot"]["stress_test_profile"]["worst"]["key"] == "COVID_2020"
            briefing = result.get("briefing", "")
            assert "MDD" in briefing or "낙폭" in briefing or "위기" in briefing
        finally:
            clear_reports()
            set_report_path_for_tests(None)
    print("✓ cache stress_test_profile + gemini 0")


def test_no_regression_risk_engine():
    """기존 risk_engine 몬테카를로 API 불변."""
    import risk_engine as re

    close = _synth_close()
    res = re.monte_carlo_stress("AAPL", close, {"risk_level": "안정"}, n_sims=1000, seed=1)
    assert res["n_sims"] == 1000
    assert len(res["scenarios"]) == len(re.MC_SCENARIOS)
    print("✓ risk_engine regression OK")


def test_scenario_hud_regression():
    """portfolio_stress estimate_position_stress 불변."""
    from portfolio_stress import estimate_position_stress, STRESS_SCENARIOS
    import pandas as pd

    close = pd.Series(_synth_close())
    df = pd.DataFrame({"Close": close})
    res = estimate_position_stress("AAPL", df=df, score=70)
    assert len(res["scenarios"]) == len(STRESS_SCENARIOS)
    assert res["estimated_mdd_pct"] < 0
    print("✓ estimate_position_stress regression OK")


def run_all():
    tests = [
        test_lehman_vs_covid_mdd_ordering,
        test_portfolio_weights_applied,
        test_deterministic_seed_reproducibility,
        test_cache_stress_profile_gemini_zero,
        test_no_regression_risk_engine,
        test_scenario_hud_regression,
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
    print(f"\n{'='*50}\n결과: {len(tests)-failed}/{len(tests)} passed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run_all() else 1)
