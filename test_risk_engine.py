"""
risk_engine 자율 시뮬레이션 엔진 검증
실행: .venv/bin/python test_risk_engine.py

검증 항목
  · 몬테카를로 10,000 경로 안정 산출(시나리오별 MDD, 음수, 순서)
  · 비동기(asyncio) 병렬 실행 무데드락
  · 동시성(멀티스레드) 캐시/인플라이트 thread-safe
  · 대량 반복 시 메모리 누수 없음(RSS 증가폭 상한)
  · 리스크 가중치 재조정(임계치 초과 → 강제 비중 하향 + 현금 확보)
  · 고래 인과관계(news_fn 주입) / 소셜 미시구조(매집·숏스퀴즈·FOMO) 판별
  · forecast_briefing 서사 생성
"""
from __future__ import annotations

from speech_style import USER_DISPLAY_NAME

import asyncio
import gc
import threading

import numpy as np

import risk_engine as re


def _synth_close(seed=0, vol=0.02, n=140, drift=0.0):
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    return 100 * np.cumprod(1 + rets)


def test_monte_carlo_shape_and_sign():
    close = _synth_close()
    res = re.monte_carlo_stress("AAPL", close, {"risk_level": "위험"}, score=70,
                                n_sims=10_000, seed=1)
    assert res["n_sims"] == 10_000
    assert len(res["scenarios"]) == len(re.MC_SCENARIOS)
    assert all(s["est_mdd_pct"] < 0 for s in res["scenarios"]), res["scenarios"]
    # 정렬: 최악(가장 음수) 먼저
    mdds = [s["est_mdd_pct"] for s in res["scenarios"]]
    assert mdds == sorted(mdds)
    assert 0.0 <= res["risk_weight"] <= 1.0
    print(f"✓ 몬테카를로 10k 안정: worst={res['estimated_mdd_pct']}% rw={res['risk_weight']}")


def test_async_parallel_no_deadlock():
    close = _synth_close(seed=2, vol=0.035)

    async def _run():
        tasks = [re.monte_carlo_stress_async("NVDA", close, {"risk_level": "경계"},
                                             score=80, n_sims=8000, seed=i) for i in range(4)]
        return await asyncio.wait_for(asyncio.gather(*tasks), timeout=30)

    out = asyncio.run(_run())
    assert len(out) == 4 and all(o["scenarios"] for o in out)
    print("✓ asyncio 병렬 4작업 무데드락 완료")


def test_threadsafe_cache_concurrency():
    close = _synth_close(seed=3)
    errors = []

    def _worker(i):
        try:
            re.assess("MSFT", close, {"risk_level": "위험"}, score=75,
                      base_ratio=0.30, n_sims=4000, seed=i)
        except Exception as e:  # noqa
            errors.append(e)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(12)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=30)
    assert not any(t.is_alive() for t in threads), "스레드 미종료(데드락 의심)"
    assert not errors, f"동시성 오류: {errors[:2]}"
    print("✓ 12스레드 동시 assess 캐시 thread-safe (오류 0, 데드락 없음)")


def test_no_memory_leak_under_load():
    """대량 반복 시뮬레이션 후 RSS 증가폭이 상한 이내인지 확인(메모리 누수 가드)."""
    try:
        import resource
        def _rss_mb():
            r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return r / (1024 * 1024) if r > 1e6 else r / 1024  # mac=bytes, linux=KB
    except Exception:
        print("· resource 모듈 없음 — 메모리 검사 생략")
        return
    close = _synth_close(seed=5, vol=0.03)
    re.monte_carlo_stress("X", close, {"risk_level": "위험"}, n_sims=10_000, seed=0)  # 워밍업
    gc.collect()
    before = _rss_mb()
    for i in range(40):
        re.monte_carlo_stress("X", close, {"risk_level": "위험"}, n_sims=10_000,
                              seed=i, parallel=False)
    gc.collect()
    after = _rss_mb()
    grew = after - before
    assert grew < 80, f"메모리 누수 의심: +{grew:.1f}MB (40×10k 시뮬레이션)"
    print(f"✓ 40×10k 반복 후 RSS 증가 {grew:+.1f}MB (<80MB) — 누수 없음")


def test_reweight_forces_haircut():
    """높은 변동성 + 위험 등급 → 임계치 초과 시 강제 비중 하향."""
    high_vol_close = _synth_close(seed=7, vol=0.05)   # 고변동성(엔비디아류)
    res = re.assess("NVDA", high_vol_close, {"risk_level": "위험"}, score=92,
                    base_ratio=0.30, n_sims=10_000, seed=3, force_refresh=True)
    rw = res["reweight"]
    assert rw["triggered"], (res["stress"]["estimated_mdd_pct"], rw)
    assert rw["adjusted_ratio"] < rw["base_ratio"], rw
    assert rw["haircut_pct"] >= 10.0, f"고변동성+위험 감축폭 부족: {rw}"
    print(f"✓ 리스크 재조정: MDD {res['stress']['estimated_mdd_pct']}% → 비중 "
          f"{rw['base_ratio']*100:.0f}%→{rw['adjusted_ratio']*100:.0f}% (현금 {rw['cash_secure_pct']:.0f}% 확보)")


def test_low_vol_stable_no_haircut():
    """저변동성 종목은 임계치 미달 → 감축 없음(점수 보존)."""
    low_vol_close = _synth_close(seed=9, vol=0.008)
    res = re.assess("KO", low_vol_close, {"risk_level": "경계"}, score=80,
                    base_ratio=0.175, n_sims=10_000, seed=4, force_refresh=True)
    assert not res["reweight"]["triggered"], res["stress"]["estimated_mdd_pct"]
    print(f"✓ 저변동성 안정 종목 감축 없음 (MDD {res['stress']['estimated_mdd_pct']}%)")


def test_whale_causation_injected():
    """news_fn 주입으로 네트워크 없이 인과관계 컨텍스트화 검증."""
    def fake_news(ticker, *a, **k):
        return {"success": True, "critical": [
            {"title": "Berkshire raises stake", "reason": "밸류에이션 매력·자사주 매입", "score": 6},
            {"title": "Strong 10-Q", "reason": "잉여현금흐름 급증", "score": 4},
        ], "analyzed": []}
    ctx = re.whale_causation("AAPL", "엘리트 매수 (+13pt)", ["버크셔 3개월 이내 매수 포착"],
                             news_fn=fake_news, background=False)
    assert ctx["available"] and ctx["sentiment"] == "긍정", ctx
    assert "밸류에이션" in ctx["why"]
    # 시그널 없으면 분석 스킵
    none_ctx = re.whale_causation("AAPL", "중립", ["수급 중립"], news_fn=fake_news, background=False)
    assert none_ctx["available"] is False and none_ctx["status"] == "no_signal"
    print("✓ 고래 인과관계 엔진: 매수 사유 컨텍스트화 +", ctx["why"][:30])


def test_social_microstructure_patterns():
    # 숏스퀴즈: 선행 하락 후 급반등 + 거래량 폭증
    n = 60
    base = np.concatenate([np.linspace(100, 80, n - 6), np.linspace(80, 96, 6)])
    vol = np.concatenate([np.full(n - 5, 1e6), np.full(5, 4e6)])
    sq = re.social_microstructure("GME", base, vol)
    assert sq["pattern"] == "SHORT_SQUEEZE", sq
    # FOMO: 과열 급등 + 거래량 급증
    up = np.concatenate([np.full(54, 100.0), 100 * np.cumprod(1 + np.full(6, 0.03))])
    volf = np.concatenate([np.full(55, 1e6), np.full(5, 3e6)])
    fomo = re.social_microstructure("MEME", up, volf)
    assert fomo["pattern"] in ("FOMO", "SHORT_SQUEEZE"), fomo
    print(f"✓ 소셜 미시구조 판별: 숏스퀴즈={sq['subgrade']} / 과열={fomo['subgrade']}")


def test_forecast_briefing_narrative():
    close = _synth_close(seed=11, vol=0.045)
    res = re.assess("NVDA", close, {"risk_level": "위험"}, score=90,
                    base_ratio=0.30, n_sims=10_000, seed=6, force_refresh=True)
    text = re.build_forecast_briefing("엔비디아", res["stress"], res["reweight"],
                                      base_pct=30, final_pct=20, macro_level="위험",
                                      high_vol_ticker="엔비디아")
    assert USER_DISPLAY_NAME in text and "MDD" in text and "%" in text
    assert "몬테카를로" in text
    print("✓ forecast_briefing:", text[:90], "...")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"\n✅ ALL {len(tests)} TESTS PASSED")
