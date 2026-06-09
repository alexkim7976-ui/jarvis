"""
J.A.R.V.I.S — 포지션 매크로 스트레스 테스트 엔진 (Estimated MDD / Risk Weight)
────────────────────────────────────────────────────────────────────────
analyze_stock_investment 의 포지션 사이징과 결합되어, 백그라운드에서 보유/후보
종목에 대한 매크로 스트레스 시나리오(금리 급등 · 환율 고점 · VIX 급등 · 공급망 충격)를
실행하고 "최악 시나리오 발생 시 예상 최대 손실률(Estimated MDD)"과 "리스크 가중치"를
산출한다.

설계:
  · 통계적 낙폭 = 일간 변동성 × √(지속 거래일) × 시나리오 z(표준편차 배수)
    → 종목 고유 변동성 기반의 정량적 추정(방어 가능).
  · 구조적 충격 = scenario_simulator(공급망 그래프)에 종목이 노드로 존재하면 해당
    매크로성 충격(PRICE_SPIKE/SUPPLY_OUTAGE 등)을 전파시켜 victim impactScore를
    추가 낙폭으로 가중. → "scenario_simulator 결합" 요구사항 충족.
  · 매크로 등급(안정/경계/위험)에 따라 낙폭 배수를 곱해 전역 위험도와 연동.
  · 5분 TTL 캐시로 시뮬레이션 오버헤드를 억제(기존 매트릭스 캐시 정책과 동일 철학).
"""
from __future__ import annotations

import math
from datetime import datetime

# 시나리오: z=충격 표준편차 배수, horizon_days=충격 지속(거래일), sim_kind=공급망 엔진 매핑
STRESS_SCENARIOS = [
    {"key": "RATE_SPIKE",   "label": "금리 급등", "en": "RATE SPIKE",   "z": 2.6, "horizon_days": 10, "sim_kind": "PRICE_SPIKE"},
    {"key": "FX_SPIKE",     "label": "환율 고점", "en": "FX SPIKE",     "z": 2.4, "horizon_days": 10, "sim_kind": "PRICE_SPIKE"},
    {"key": "VOL_SPIKE",    "label": "VIX 급등", "en": "VOL SPIKE",    "z": 3.0, "horizon_days": 7,  "sim_kind": "DEMAND_COLLAPSE"},
    {"key": "SUPPLY_SHOCK", "label": "공급망 충격", "en": "SUPPLY SHOCK", "z": 2.2, "horizon_days": 14, "sim_kind": "SUPPLY_OUTAGE"},
]

# 매크로 등급별 낙폭 가중 (전역 위험도 연동)
_MACRO_DD_MULT = {"안정": 1.0, "경계": 1.2, "위험": 1.5}

# 리스크 가중치 1.0 에 해당하는 손실률(%) — |MDD| 40% = 최대 위험
_RISK_WEIGHT_FLOOR = 40.0

_STRESS_CACHE: dict = {}
_STRESS_TTL_SECONDS = 300
_DEMO_GRAPH = None  # scenario_simulator 그래프 1회 빌드 후 재사용(경량)


def _daily_vol(df) -> float:
    """일간 수익률 표준편차. 데이터 부족 시 보수적 기본값(2%)."""
    try:
        import numpy as np
        closes = df["Close"].dropna().to_numpy(dtype=float)
        if closes.size < 5:
            return 0.02
        rets = np.diff(closes) / closes[:-1]
        v = float(np.std(rets))
        return v if v > 0 else 0.02
    except Exception:
        return 0.02


def _structural_impact(ticker: str, sim_kind: str):
    """공급망 그래프에 종목이 노드로 존재하면 구조적 충격(victim 비중 0~1)과 impactScore 반환."""
    global _DEMO_GRAPH
    try:
        from scenario_simulator import build_demo_graph, Shock, propagate
        if _DEMO_GRAPH is None:
            _DEMO_GRAPH = build_demo_graph()
        g = _DEMO_GRAPH
        code = str(ticker).split(".")[0]
        if code not in g.nodes:
            return 0.0, None
        # 그래프 진앙(TSMC)에서 매크로성 충격을 발생시켜 해당 노드 영향 조회
        shock = Shock(origin="TSMC", kind=sim_kind, intensity=1.0, duration_weeks=2.0)
        impact, _paths = propagate(g, shock)
        raw = impact.get(code, 0.0)
        score = max(-5, min(5, round(raw * 5)))   # to_value_chain_impacts 와 동일 스케일
        if score >= 0:
            return 0.0, score                       # 수혜/무영향은 리스크 미반영
        return min(1.0, abs(score) / 5.0), score
    except Exception:
        return 0.0, None


def estimate_position_stress(ticker: str, df=None, macro_status=None,
                             score=None, force_refresh: bool = False) -> dict:
    """매크로 스트레스 시나리오별 예상 MDD와 리스크 가중치를 산출.

    Returns dict: scenarios[], worst, estimated_mdd_pct, risk_weight,
                  macro_level, alert_text, scenario_rows, summary_kr
    """
    level = (macro_status or {}).get("risk_level", "안정")
    key = f"{ticker}|{level}"
    now = datetime.now().timestamp()
    if not force_refresh:
        cached = _STRESS_CACHE.get(key)
        if cached and (now - cached["ts"]) < _STRESS_TTL_SECONDS:
            return cached["data"]

    vol = _daily_vol(df) if df is not None else 0.02
    macro_mult = _MACRO_DD_MULT.get(level, 1.0)
    # 투자점수가 낮을수록(부진) 방어력 약함 → 60점 미만이면 낙폭 가산
    s = 60 if score is None else float(score)
    quality_mult = 1.0 + max(0.0, (60.0 - s)) / 100.0

    results = []
    for sc in STRESS_SCENARIOS:
        stat_dd = vol * math.sqrt(sc["horizon_days"]) * sc["z"]      # 통계적 낙폭(비율)
        struct, sim_score = _structural_impact(ticker, sc["sim_kind"])
        struct_dd = struct * 0.12                                     # 구조적 충격 최대 +12%p
        est = (stat_dd * macro_mult * quality_mult) + struct_dd
        est_pct = -round(est * 100, 1)
        results.append({
            "key": sc["key"], "label": sc["label"], "en": sc["en"],
            "est_mdd_pct": est_pct, "structural": round(struct, 2), "sim_score": sim_score,
        })

    results.sort(key=lambda r: r["est_mdd_pct"])   # 최대 손실(가장 음수) 먼저
    worst = results[0]
    worst_abs = abs(worst["est_mdd_pct"])
    risk_weight = round(min(1.0, worst_abs / _RISK_WEIGHT_FLOOR), 2)

    # HUD 표용 행 (라벨, 값)
    scenario_rows = [[f"{r['label']}", f"EST.MDD {r['est_mdd_pct']:.0f}%"] for r in results]
    scenario_rows.append(["리스크 가중치", f"{risk_weight:.2f}"])

    data = {
        "scenarios": results,
        "worst": worst,
        "estimated_mdd_pct": worst["est_mdd_pct"],
        "risk_weight": risk_weight,
        "macro_level": level,
        "alert_text": f"CRITICAL SCENARIO: {worst['en']} -> EST.MDD: {worst['est_mdd_pct']:.0f}%",
        "scenario_rows": scenario_rows,
        "summary_kr": (
            f"최악 시나리오 '{worst['label']}' 발생 시 예상 최대 손실률은 "
            f"{worst['est_mdd_pct']:.0f}%이며, 리스크 가중치는 {risk_weight:.2f}입니다."
        ),
    }
    _STRESS_CACHE[key] = {"ts": now, "data": data}
    return data
