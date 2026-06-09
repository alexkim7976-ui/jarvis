"""
J.A.R.V.I.S — 자율 리스크 시뮬레이션 엔진 (Autonomous Risk Engine)
══════════════════════════════════════════════════════════════════════
stock_quant.py 의 3레이어 매트릭스 엔진을 정적 스냅샷에서 시나리오 기반
'자율 시뮬레이션 에이전트'로 진화시키는 핵심 모듈.

구성
  1) 몬테카를로 스트레스 테스트 (10,000 경로) — 금리 급등 / 환율 피크 / 공급망 차단
     가상 시나리오 하의 시나리오별 예상 최대 낙폭(Estimated MDD) 분포를 산출.
  2) 리스크 가중치 재조정 — MDD가 임계치를 초과하면 매트릭스 점수가 높아도
     포지션 비중을 강제 하향(고변동성 종목 -5~-10%p) + 현금 확보 권고.
  3) 고래/소셜 인과관계 엔진 — news_analyzer 를 트리거해 '왜' 매매했는지 컨텍스트화,
     호가/거래량 프로파일로 단순 FOMO·매집·숏스퀴즈를 판별.
  4) 서사형 forecast_briefing 생성.

설계 원칙 (Implementation Rules 준수)
  · 연산 비용이 큰 작업은 asyncio(run_in_executor)로 병렬화 + TTL 캐시로 중복 차단.
  · numpy 벡터화로 10k 시뮬레이션을 수 ms 에 처리(스칼라 결과만 보존 → 메모리 누수 없음).
  · 모든 외부 의존성은 지연 임포트 + try/except 폴백 → 하위 호환성·안정성 보장.
  · 캐시/인플라이트 셋은 단일 Lock 으로 동기화 → 데드락 없는 thread-safe 구조.
"""
from __future__ import annotations

import asyncio
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

# ── 몬테카를로 가상 시나리오 (일간 drift 충격 / 변동성 증폭) ──────────────
MC_SCENARIOS = [
    {"key": "RATE_SPIKE", "label": "미 국채 금리 급등", "en": "RATE SPIKE", "drift": -0.0020, "vol_mult": 1.6},
    {"key": "FX_PEAK",    "label": "환율 피크 고점",   "en": "FX PEAK",    "drift": -0.0018, "vol_mult": 1.5},
    {"key": "SUPPLY_CUT", "label": "글로벌 공급망 차단", "en": "SUPPLY CUT", "drift": -0.0025, "vol_mult": 1.8},
]

DEFAULT_N_SIMS = 10_000          # 시나리오당 시뮬레이션 경로 수
DEFAULT_HORIZON = 21             # 충격 지속 거래일(약 1개월)
MDD_THRESHOLD_PCT = -20.0        # 이보다 큰 손실(더 음수)이면 강제 비중 감축
HIGH_VOL_DAILY = 0.03            # 고변동성 종목 기준(일간 표준편차 3%)
_RISK_WEIGHT_FLOOR = 40.0        # |MDD| 40% = 리스크 가중치 1.0

_MACRO_VOL_MULT = {"안정": 1.0, "경계": 1.15, "위험": 1.35}

# TTL 캐시 (ticker|macro_level → 결과). 크기 상한으로 무한 증식 방지.
_STRESS_CACHE: dict = {}
_STRESS_TTL = 300
_CAUSATION_CACHE: dict = {}
_CAUSATION_TTL = 3600
_CACHE_MAX = 256

_lock = threading.Lock()
_inflight: set = set()
# 시나리오 병렬 연산용 공유 풀 (max_workers 제한 → 자원 폭주 방지)
_EXECUTOR = ThreadPoolExecutor(max_workers=3, thread_name_prefix="risk-mc")


# ════════════════════════════════════════════════════════════════════
#  유틸
# ════════════════════════════════════════════════════════════════════
def _series_to_array(series) -> np.ndarray:
    try:
        if series is None:
            return np.array([])
        if hasattr(series, "to_numpy"):
            return series.to_numpy(dtype=float)
        return np.asarray(series, dtype=float)
    except Exception:
        return np.array([])


def daily_volatility(close) -> float:
    """일간 수익률 표준편차. 데이터 부족 시 보수적 기본값(2%)."""
    arr = _series_to_array(close)
    arr = arr[np.isfinite(arr)]
    if arr.size < 5:
        return 0.02
    rets = np.diff(arr) / arr[:-1]
    rets = rets[np.isfinite(rets)]
    v = float(np.std(rets)) if rets.size else 0.02
    return v if v > 0 else 0.02


def _prune_cache(cache: dict):
    if len(cache) <= _CACHE_MAX:
        return
    # 가장 오래된 절반 제거 (간단한 LRU 근사)
    for k in sorted(cache, key=lambda k: cache[k]["ts"])[: len(cache) // 2]:
        cache.pop(k, None)


# ════════════════════════════════════════════════════════════════════
#  1. 몬테카를로 스트레스 테스트
# ════════════════════════════════════════════════════════════════════
def _simulate_scenario_mdd(base_vol: float, macro_mult: float, structural: float,
                           scenario: dict, n_sims: int, horizon: int,
                           seed: int | None = None) -> dict:
    """한 시나리오에 대해 n_sims 경로의 MDD 분포를 벡터화 계산.
    스칼라 통계(p95 tail / median)만 반환하므로 대용량 배열은 즉시 해제된다."""
    rng = np.random.default_rng(seed)
    sigma = base_vol * scenario["vol_mult"] * macro_mult
    # 구조적 공급망 충격(0~1)을 추가 하방 drift 로 반영 (최대 -0.0015/일)
    mu = scenario["drift"] - structural * 0.0015

    rets = rng.normal(mu, sigma, size=(n_sims, horizon))
    # 누적 가격 경로 (시작 1.0)
    price = np.empty((n_sims, horizon + 1), dtype=np.float64)
    price[:, 0] = 1.0
    price[:, 1:] = np.cumprod(1.0 + rets, axis=1)
    running_max = np.maximum.accumulate(price, axis=1)
    dd = (price - running_max) / running_max          # ≤ 0
    mdd = dd.min(axis=1)                               # 경로별 최대 낙폭

    p95 = float(np.percentile(mdd, 5))                 # 하위 5% 꼬리(최악권)
    p50 = float(np.percentile(mdd, 50))
    worst = float(mdd.min())
    # 대형 임시배열 명시적 해제(반복 실행 시 메모리 안정)
    del rets, price, running_max, dd, mdd
    return {
        "key": scenario["key"], "label": scenario["label"], "en": scenario["en"],
        "est_mdd_pct": round(p95 * 100, 1),            # "예상 최대 낙폭" = 꼬리 위험
        "median_mdd_pct": round(p50 * 100, 1),
        "worst_mdd_pct": round(worst * 100, 1),
        "sigma": round(sigma, 4),
    }


def monte_carlo_stress(ticker: str, close, macro_status: dict | None = None,
                       score: float | None = None, structural: float = 0.0,
                       n_sims: int = DEFAULT_N_SIMS, horizon: int = DEFAULT_HORIZON,
                       seed: int | None = None, parallel: bool = True) -> dict:
    """모든 가상 시나리오에 대해 몬테카를로 스트레스 테스트 수행 → 시나리오별 MDD."""
    level = (macro_status or {}).get("risk_level", "안정")
    base_vol = daily_volatility(close)
    macro_mult = _MACRO_VOL_MULT.get(level, 1.0)
    # 투자점수 낮을수록 방어력 약함 → 변동성 소폭 가산
    s = 60.0 if score is None else float(score)
    quality_mult = 1.0 + max(0.0, (60.0 - s)) / 120.0
    eff_vol = base_vol * quality_mult

    def _run(sc, i):
        sd = None if seed is None else seed + i
        return _simulate_scenario_mdd(eff_vol, macro_mult, structural, sc, n_sims, horizon, sd)

    if parallel and n_sims >= 2000:
        futures = [_EXECUTOR.submit(_run, sc, i) for i, sc in enumerate(MC_SCENARIOS)]
        scenarios = [f.result() for f in futures]
    else:
        scenarios = [_run(sc, i) for i, sc in enumerate(MC_SCENARIOS)]

    scenarios.sort(key=lambda r: r["est_mdd_pct"])     # 최악(가장 음수) 먼저
    worst = scenarios[0]
    risk_weight = round(min(1.0, abs(worst["est_mdd_pct"]) / _RISK_WEIGHT_FLOOR), 2)
    return {
        "engine": "monte_carlo",
        "n_sims": n_sims,
        "horizon_days": horizon,
        "base_daily_vol": round(base_vol, 4),
        "macro_level": level,
        "scenarios": scenarios,
        "worst": worst,
        "estimated_mdd_pct": worst["est_mdd_pct"],
        "risk_weight": risk_weight,
        "high_volatility": base_vol >= HIGH_VOL_DAILY,
    }


async def monte_carlo_stress_async(ticker: str, close, macro_status=None, score=None,
                                   structural: float = 0.0, n_sims: int = DEFAULT_N_SIMS,
                                   horizon: int = DEFAULT_HORIZON, seed: int | None = None) -> dict:
    """비동기 래퍼 — 이벤트 루프를 막지 않고 백그라운드 스레드풀에서 시뮬레이션."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: monte_carlo_stress(ticker, close, macro_status, score, structural,
                                   n_sims, horizon, seed),
    )


# ════════════════════════════════════════════════════════════════════
#  2. 리스크 가중치 재조정 (강제 비중 하향)
# ════════════════════════════════════════════════════════════════════
def reweight_allocation(base_ratio: float, stress: dict, macro_level: str = "안정") -> dict:
    """스트레스 결과를 받아 포지션 비중을 강제 재조정.
    임계치(MDD_THRESHOLD_PCT) 초과 시 고변동성 종목은 -10%p, 그 외 -5%p,
    매크로 '위험'이면 +3%p 추가 감축하고 현금 확보를 권고한다."""
    worst_mdd = stress.get("estimated_mdd_pct", 0.0)
    high_vol = stress.get("high_volatility", False)
    haircut = 0.0
    triggered = False
    if worst_mdd <= MDD_THRESHOLD_PCT:
        triggered = True
        haircut = 0.10 if high_vol else 0.05
        if macro_level == "위험":
            haircut += 0.03
    new_ratio = max(0.0, round(base_ratio - haircut, 4))
    return {
        "triggered": triggered,
        "haircut_pct": round(haircut * 100, 1),
        "base_ratio": round(base_ratio, 4),
        "adjusted_ratio": new_ratio,
        "cash_secure_pct": round(haircut * 100, 1),
        "threshold_pct": MDD_THRESHOLD_PCT,
    }


# ════════════════════════════════════════════════════════════════════
#  3. 통합 평가 (캐시 + 재조정) — 매트릭스/분석 엔진 공통 진입점
# ════════════════════════════════════════════════════════════════════
def assess(ticker: str, close, macro_status: dict | None = None, score: float | None = None,
           base_ratio: float = 0.0, n_sims: int = DEFAULT_N_SIMS,
           force_refresh: bool = False, seed: int | None = None) -> dict:
    """스트레스 테스트 + 리스크 재조정을 캐시와 함께 수행.
    매크로가 '경계'/'위험'일 때만 호출하도록 설계(안정 시 연산 절약)."""
    level = (macro_status or {}).get("risk_level", "안정")
    key = f"{ticker}|{level}|{int(base_ratio*1000)}"
    now = time.time()
    if not force_refresh:
        with _lock:
            c = _STRESS_CACHE.get(key)
            if c and (now - c["ts"]) < _STRESS_TTL:
                return c["data"]

    # 공급망 구조적 충격(scenario_simulator 연동) — 그래프 노드면 추가 하방압력
    structural = 0.0
    try:
        from portfolio_stress import _structural_impact
        structural, _ = _structural_impact(ticker, "SUPPLY_OUTAGE")
    except Exception:
        structural = 0.0

    stress = monte_carlo_stress(ticker, close, macro_status, score, structural, n_sims=n_sims, seed=seed)
    reweight = reweight_allocation(base_ratio, stress, level)
    data = {"stress": stress, "reweight": reweight, "structural": round(structural, 2)}

    with _lock:
        _STRESS_CACHE[key] = {"ts": now, "data": data}
        _prune_cache(_STRESS_CACHE)
    return data


def build_forecast_briefing(name: str, stress: dict, reweight: dict,
                            base_pct: float, final_pct: float, macro_level: str,
                            high_vol_ticker: str = "") -> str:
    """LLM 서사형 예측 가이드(forecast_briefing) 생성."""
    worst = stress.get("worst", {})
    mdd = abs(stress.get("estimated_mdd_pct", 0.0))
    label = worst.get("label", "매크로 충격")
    n = stress.get("n_sims", DEFAULT_N_SIMS)
    parts = [
        f"토니, 몬테카를로 {n:,}회 시뮬레이션 결과 현재 포트폴리오는 "
        f"'{label}' 시나리오 발생 시 예상 최대 낙폭(MDD)이 {mdd:.0f}%까지 확대됩니다."
    ]
    if reweight.get("triggered"):
        tail = f" 특히 고변동성 종목" + (f"({high_vol_ticker})" if high_vol_ticker else "") + \
               f" 비중을 {reweight['haircut_pct']:.0f}% 줄이고 현금을 확보하는 것을 권고합니다."
        parts.append(
            f"손실 임계치({abs(reweight['threshold_pct']):.0f}%)를 초과하여, 매트릭스 점수가 높더라도 "
            f"권장 진입 비중을 {base_pct:.0f}%에서 {final_pct:.1f}%로 강제 하향했습니다." + tail
        )
    else:
        parts.append(
            f"낙폭이 관리 임계치 이내이므로 권장 진입 비중 {final_pct:.1f}%를 유지합니다. "
            f"다만 매크로 등급이 '{macro_level}'인 만큼 분할 진입을 권장합니다."
        )
    return " ".join(parts)


# ════════════════════════════════════════════════════════════════════
#  4. 고래 인과관계 엔진 (news_analyzer 연동, 백그라운드+캐시)
# ════════════════════════════════════════════════════════════════════
def _causation_material(whale_notes: list) -> bool:
    """고래 시그널이 인과 분석할 가치가 있는지(실제 매수/매도 포착) 판별."""
    kw = ("매수", "매도", "매집", "분산", "청산", "BUY", "SELL")
    return any(any(k in str(n) for k in kw) for n in (whale_notes or []))


def whale_causation(ticker: str, whale_status: str = "", whale_notes: list | None = None,
                    news_fn=None, background: bool = True) -> dict:
    """고래 포지션 변동의 '이유'를 news_analyzer(SEC/IR/뉴스)로 컨텍스트화.
    무겁고 네트워크 의존적이므로 1시간 캐시 + 백그라운드 트리거(비블로킹)."""
    if not _causation_material(whale_notes):
        return {"available": False, "status": "no_signal", "why": "", "sources": []}

    now = time.time()
    with _lock:
        c = _CAUSATION_CACHE.get(ticker)
        if c and (now - c["ts"]) < _CAUSATION_TTL:
            return c["data"]
        if background and ticker in _inflight:
            return {"available": False, "status": "analyzing", "why": "공시·뉴스 인과 분석 진행 중", "sources": []}

    def _compute() -> dict:
        try:
            fn = news_fn
            if fn is None:
                from news_analyzer import score_ticker_news as fn  # 지연 임포트(무거움)
            res = fn(ticker)
            items = (res.get("critical") or res.get("analyzed") or [])[:3]
            reasons = []
            score_sum = 0
            for it in items:
                r = it.get("reason") or it.get("title") or ""
                if r:
                    reasons.append(str(r)[:80])
                try:
                    score_sum += int(it.get("score", 0))
                except Exception:
                    pass
            sentiment = "긍정" if score_sum > 0 else ("부정" if score_sum < 0 else "중립")
            why = "; ".join(reasons) if reasons else "특이 공시/뉴스 없음"
            return {"available": True, "status": "ok", "why": why,
                    "sources": reasons, "sentiment": sentiment, "score_sum": score_sum}
        except Exception as e:
            return {"available": False, "status": "error", "why": f"인과 분석 실패: {str(e)[:50]}", "sources": []}

    if not background:
        data = _compute()
        with _lock:
            _CAUSATION_CACHE[ticker] = {"ts": now, "data": data}
            _prune_cache(_CAUSATION_CACHE)
        return data

    # 백그라운드 스레드로 계산 → 다음 호출 사이클에 캐시 반영(현재 호출은 즉시 반환)
    def _worker():
        data = _compute()
        with _lock:
            _CAUSATION_CACHE[ticker] = {"ts": time.time(), "data": data}
            _prune_cache(_CAUSATION_CACHE)
            _inflight.discard(ticker)

    with _lock:
        _inflight.add(ticker)
    threading.Thread(target=_worker, daemon=True).start()
    return {"available": False, "status": "analyzing", "why": "공시·뉴스 인과 분석 진행 중", "sources": []}


# ════════════════════════════════════════════════════════════════════
#  5. 소셜 미시구조 — 호가/거래량 프로파일 (FOMO vs 매집 vs 숏스퀴즈)
# ════════════════════════════════════════════════════════════════════
def social_microstructure(ticker: str, close, volume, high=None, low=None) -> dict:
    """OHLCV 거래량 프로파일로 소셜 스파이크의 성격을 정밀 분류.
    - 매집(ACCUMULATION): 가격 횡보 속 거래량 증가 + 매수우위 자금흐름
    - 숏스퀴즈(SHORT_SQUEEZE): 선행 하락 후 급반등 + 거래량 폭증
    - 단순FOMO(FOMO): 이미 과열된 상태에서 가격·거래량 동반 급등
    반환 score_delta 는 소셜 레이어에 합산할 보정치(±, 호출부에서 클램핑)."""
    c = _series_to_array(close)
    v = _series_to_array(volume)
    n = min(c.size, v.size)
    if n < 20:
        return {"pattern": "UNKNOWN", "subgrade": "데이터 부족", "score_delta": 0, "notes": ["가격/거래량 데이터 부족"]}
    c = c[-n:]; v = v[-n:]
    rets = np.diff(c) / c[:-1]

    # 최근 5거래일(반등 구간) vs 선행 추세(반등 시작 직전까지의 고점 대비 낙폭)
    recent_ret = float(c[-1] / c[-6] - 1.0) if n >= 6 else 0.0
    prior_window = c[:-5] if n > 6 else c
    prior_peak = float(np.max(prior_window)) if prior_window.size else float(c[0])
    prior_ret = float(c[-6] / prior_peak - 1.0) if (prior_peak > 0 and n >= 6) else 0.0
    avg_vol = float(np.mean(v[:-5])) if n > 10 else float(np.mean(v))
    recent_vol = float(np.mean(v[-5:]))
    vol_spike = recent_vol / avg_vol if avg_vol > 0 else 1.0
    recent_sigma = float(np.std(rets[-5:])) if rets.size >= 5 else 0.0
    range_tight = recent_sigma < (float(np.std(rets)) * 0.8 if rets.size else 1.0)

    # 매수우위 자금흐름(부호있는 거래량 누적 = OBV 근사)의 기울기
    signed = np.sign(np.append(rets[0], rets)) * v
    obv = np.cumsum(signed)
    obv_slope = float(obv[-1] - obv[-6]) if n >= 6 else 0.0

    notes = [f"거래량 스파이크 {vol_spike:.1f}x · 최근5일 {recent_ret*100:+.1f}% · 선행 {prior_ret*100:+.1f}%"]

    if prior_ret <= -0.10 and recent_ret >= 0.12 and vol_spike >= 2.0:
        pattern = "SHORT_SQUEEZE"; subgrade = "숏스퀴즈 가능성"; delta = +4
        notes.append("선행 하락 후 거래량 폭증 급반등 → 숏커버링 의심")
    elif range_tight and vol_spike >= 1.5 and obv_slope > 0 and abs(recent_ret) < 0.06:
        pattern = "ACCUMULATION"; subgrade = "세력 매집 의심"; delta = +5
        notes.append("가격 횡보 속 매수우위 거래량 증가 → 조용한 매집")
    elif recent_ret >= 0.10 and vol_spike >= 2.0:
        pattern = "FOMO"; subgrade = "단순 FOMO 과열"; delta = -6
        notes.append("과열 구간 가격·거래량 동반 급등 → 추격매수 위험")
    else:
        pattern = "NEUTRAL"; subgrade = "특이 미시구조 없음"; delta = 0

    return {
        "pattern": pattern, "subgrade": subgrade, "score_delta": int(delta),
        "vol_spike": round(vol_spike, 2), "recent_ret_pct": round(recent_ret * 100, 1),
        "prior_ret_pct": round(prior_ret * 100, 1), "obv_slope": round(obv_slope, 1),
        "notes": notes,
    }
