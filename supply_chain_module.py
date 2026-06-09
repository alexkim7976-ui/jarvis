"""
J.A.R.V.I.S — Supply Chain Agent LLM 도구
──────────────────────────────────────────
Java 백엔드가 떠 있을 때 JARVIS가 빅테크→국내 밸류체인 분석을 호출한다.
"""
from __future__ import annotations

import json

from supply_chain_client import analyze_supply_chain, simulate_scenario, health_check
from scenario_simulator import Shock, build_demo_graph, simulate as run_structural_simulation

SUPPLY_CHAIN_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "analyze_supply_chain_impact",
            "description": (
                "빅테크/에너지 기업(Google, LS에코에너지 등) 뉴스·공시·실적 발표 원문을 "
                "분석해 국내 반도체/에너지 밸류체인(한미반도체, 어보브반도체 등) 연쇄 영향을 추론한다. "
                "인과관계 체인과 impactScore(-5~+5)를 반환한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "body_text": {
                        "type": "string",
                        "description": "분석할 뉴스/공시/실적 발표 원문 전체",
                    },
                    "title": {"type": "string", "description": "기사/문서 제목"},
                    "source": {"type": "string", "description": "출처 (Reuters, SEC 등)"},
                    "source_type": {
                        "type": "string",
                        "enum": ["NEWS", "FILING", "EARNINGS_TRANSCRIPT", "PRESS_RELEASE"],
                        "default": "NEWS",
                    },
                    "url": {"type": "string", "description": "원문 URL"},
                },
                "required": ["body_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simulate_supply_chain_scenario",
            "description": (
                "사용자가 제시한 가상(What-If) 시나리오의 충격 유형(PRICE_SPIKE 가격 전가 / "
                "REGULATORY 규제·지정학 / PHYSICAL_DISRUPTION 물리적 공급 차질)을 분류하고, "
                "국내 반도체/에너지 밸류체인 기업별 수혜·타격(BENEFICIARY/CASUALTY/NEUTRAL)과 "
                "expectedScore(-5~+5)를 시뮬레이션한다. 실제 뉴스가 아닌 '만약 ~한다면' 가정 분석용."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scenario_text": {
                        "type": "string",
                        "description": (
                            "분석할 가상 시나리오 한 건 "
                            "(예: '미국이 중국으로의 HBM 장비 수출을 전면 금지한다면?')"
                        ),
                    },
                },
                "required": ["scenario_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "simulate_structural_shock",
            "description": (
                "결정론적(deterministic) 공급망 그래프 위에서 충격을 직접 전파시켜 "
                "기업별 영향(-5~+5)과 인과 경로를 산출한다. simulate_supply_chain_scenario(LLM 추론)와 달리 "
                "진앙 노드·shock 종류를 명시적으로 지정하므로 결과가 그래프 상에서 방어 가능하다. "
                "Java 백엔드 없이 로컬에서 동작. "
                "구체적 진앙·종류가 명확한 What-If에 적합 (예: 'TSMC가 2주 가동중단', '심텍이 기판값 50% 인상')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "shock_kind": {
                        "type": "string",
                        "enum": ["SUPPLY_OUTAGE", "PRICE_SPIKE", "DEMAND_COLLAPSE", "CAPEX_BOOM", "REGULATORY"],
                        "description": (
                            "SUPPLY_OUTAGE=공급 차질(지진·화재) / PRICE_SPIKE=원가 급등 / "
                            "DEMAND_COLLAPSE=수요 붕괴 / CAPEX_BOOM=설비투자 호황(호재성) / "
                            "REGULATORY=규제(target_country 필수)"
                        ),
                    },
                    "origin_ticker": {
                        "type": "string",
                        "description": "진앙 노드 ticker (예: 'TSMC', '042700'). REGULATORY는 생략 가능.",
                    },
                    "intensity": {
                        "type": "number",
                        "description": "충격 강도 0.0~1.0 (1.0=완전 중단, 0.5=부분). 기본 1.0",
                    },
                    "duration_weeks": {
                        "type": "number",
                        "description": "충격 지속 기간(주). 재고 흡수 계산에 사용. 기본 2.0",
                    },
                    "target_country": {
                        "type": "string",
                        "description": "REGULATORY 전용 — 규제 대상 국가 코드 (예: 'TW', 'CN').",
                    },
                },
                "required": ["shock_kind"],
            },
        },
    },
]


def execute_supply_chain_tool(tool_name: str, arguments: dict) -> dict:
    if tool_name not in (
        "analyze_supply_chain_impact",
        "simulate_supply_chain_scenario",
        "simulate_structural_shock",
    ):
        return {"success": False, "message": f"Unknown tool: {tool_name}"}

    try:
        if isinstance(arguments, str):
            arguments = json.loads(arguments)

        # 결정론 그래프 시뮬 — pure Python, Java 백엔드 불필요
        if tool_name == "simulate_structural_shock":
            return _run_structural(arguments)

        # analyze / LLM simulate — Java 백엔드 필요
        if not health_check():
            return {
                "success": False,
                "message": (
                    "Supply Chain Agent 서버가 실행 중이 아닙니다. "
                    "supply-chain-agent를 먼저 시작하세요 (mvn spring-boot:run)."
                ),
            }
        if tool_name == "simulate_supply_chain_scenario":
            return _format_simulation(
                simulate_scenario(arguments.get("scenario_text", ""))
            )
        return _format_analysis(analyze_supply_chain(
            body_text=arguments.get("body_text", ""),
            title=arguments.get("title", ""),
            source=arguments.get("source", "JARVIS"),
            source_type=arguments.get("source_type", "NEWS"),
            url=arguments.get("url", ""),
        ))
    except Exception as e:
        return {"success": False, "message": f"Supply Chain 분석 실패: {e}"}


def _signed(n: int) -> str:
    """HUD 표기용 — 양수/음수는 부호를 붙이고 0은 부호 없이."""
    return f"{n:+d}" if n else "0"


def _norm_ticker(t: str) -> str:
    """042700.KQ → 042700 (scenario_simulator/LLM 어휘 정규화)."""
    return (t or "").split(".")[0].strip()


def _format_analysis(data: dict) -> dict:
    """뉴스 원문 분석(/analyze) 결과 → HUD overlay_table + 음성 메시지."""
    result = data.get("result", {})
    briefing = result.get("reasoningBriefing", "")
    impacts = result.get("valueChainImpacts") or []
    beneficiaries = [
        f"{v.get('companyName')} ({v.get('ticker')}, {v.get('impactScore'):+d})"
        for v in impacts
        if v.get("impactType") == "BENEFICIARY"
    ]
    return {
        "success": True,
        "message": briefing or result.get("headlineSummary", "분석 완료"),
        "analysis_id": result.get("id"),
        "target_big_tech": result.get("targetBigTech"),
        "impact_score": result.get("impactScore"),
        "beneficiaries": beneficiaries,
        "overlay_table": {
            "type": "table",
            "title": f"Supply Chain · {result.get('targetBigTech', '')}",
            "headers": ["기업", "티커", "영향", "점수", "섹터"],
            "rows": [
                [
                    v.get("companyName", ""),
                    v.get("ticker", ""),
                    v.get("impactType", ""),
                    str(v.get("impactScore", 0)),
                    v.get("sector", ""),
                ]
                for v in impacts
            ],
        },
        "raw": result,
    }


def _format_simulation(data: dict) -> dict:
    """Multi-Shock What-If 시뮬레이션(/simulate) 결과 → HUD overlay_table + J.A.R.V.I.S 브리핑."""
    shock = data.get("shockType", "")
    results = data.get("simulationResults") or []
    beneficiaries = [
        f"{r.get('companyName')} ({r.get('ticker')}, {r.get('expectedScore'):+d})"
        for r in results
        if r.get("status") == "BENEFICIARY"
    ]
    casualties = [
        f"{r.get('companyName')} ({r.get('ticker')}, {r.get('expectedScore'):+d})"
        for r in results
        if r.get("status") == "CASUALTY"
    ]
    return {
        "success": True,
        "message": data.get("jarvisBriefingMessage") or data.get("macroImpactBriefing", "시뮬레이션 완료"),
        "shock_type": shock,
        "scenario_summary": data.get("hypotheticalEventSummary"),
        "beneficiaries": beneficiaries,
        "casualties": casualties,
        "overlay_table": {
            "type": "table",
            "title": f"Multi-Shock · {shock}",
            "headers": ["기업", "티커", "판정", "점수", "팩터"],
            "rows": [
                [
                    r.get("companyName", ""),
                    r.get("ticker", ""),
                    r.get("status", ""),
                    _signed(r.get("expectedScore", 0)),
                    r.get("factorAnalysis", ""),
                ]
                for r in results
            ],
        },
        "raw": data,
    }


_STRUCTURAL_SHOCKS = {"SUPPLY_OUTAGE", "PRICE_SPIKE", "DEMAND_COLLAPSE", "CAPEX_BOOM", "REGULATORY"}

_SCENARIO_KR = {
    "SUPPLY_OUTAGE": "공급 차질(지진·화재 등 물리적 가동중단)",
    "PRICE_SPIKE": "원가 급등",
    "DEMAND_COLLAPSE": "수요 붕괴",
    "CAPEX_BOOM": "설비투자 호황(호재성)",
    "REGULATORY": "규제·제재",
}


def _build_scenario_text(shock_kind, origin, intensity, duration, target_country):
    """structural 입력을 LLM simulate용 자연어 시나리오로 변환."""
    desc = _SCENARIO_KR[shock_kind]
    pct = int(round(float(intensity) * 100))
    if shock_kind == "REGULATORY":
        return (f"국가 코드 '{target_country}'에 해당하는 모든 공급망 노드를 대상으로 한 "
                f"{desc} (강도 {pct}%, 약 {duration}주 지속). 한국 반도체·에너지 밸류체인 영향은?")
    return (f"{origin} 진앙에서 발생한 {desc} 충격, 강도 {pct}%, 약 {duration}주 지속. "
            f"한국 반도체·에너지 밸류체인 기업별 영향은?")


def _cross_validate(shock_kind, origin, intensity, duration, target_country, structural_impacts):
    """B′ — ACTIVE 결과를 LLM simulate 결과와 비교. 백엔드 다운/예외 시 None 반환 (graceful)."""
    if not health_check():
        return None
    try:
        llm_data = simulate_scenario(
            _build_scenario_text(shock_kind, origin, intensity, duration, target_country)
        )
    except Exception:
        return None

    llm_by_key = {}
    for r in (llm_data.get("simulationResults") or []):
        key = _norm_ticker(r.get("ticker", ""))
        if key:
            llm_by_key[key] = {
                "score": int(r.get("expectedScore", 0)),
                "status": r.get("status", "NEUTRAL"),
            }

    divergence = {}
    for ticker, struct_score in structural_impacts.items():
        llm = llm_by_key.get(_norm_ticker(ticker))
        if llm is None:
            continue
        if struct_score * llm["score"] < 0:                              # 양쪽 강한 부호 반대만
            divergence[ticker] = {"llm_score": llm["score"], "llm_status": llm["status"]}
    return {"divergence": divergence, "llm_briefing": llm_data.get("jarvisBriefingMessage", "")}


def _run_structural(args: dict) -> dict:
    """scenario_simulator 호출 wrapper — Shock 구성 + 그래프 검증 + 결과 포맷."""
    shock_kind = (args.get("shock_kind") or "").strip().upper()
    if shock_kind not in _STRUCTURAL_SHOCKS:
        return {"success": False, "message": (
            f"지원하지 않는 shock_kind: '{shock_kind}'. "
            f"지원: {'/'.join(sorted(_STRUCTURAL_SHOCKS))}"
        )}
    origin = (args.get("origin_ticker") or "").strip()
    intensity = float(args.get("intensity", 1.0))
    duration = float(args.get("duration_weeks", 2.0))
    target_country = (args.get("target_country") or "").strip().upper()

    predicate = None
    if shock_kind == "REGULATORY":
        if not target_country:
            return {"success": False, "message": "REGULATORY shock은 target_country (예: 'TW', 'CN') 필요"}
        predicate = lambda n: n.country == target_country  # noqa: E731
    elif not origin:
        return {"success": False, "message": f"{shock_kind} shock은 origin_ticker (예: 'TSMC', '042700') 필요"}

    g = build_demo_graph()
    if shock_kind != "REGULATORY" and origin not in g.nodes:
        return {"success": False, "message": (
            f"origin_ticker '{origin}'이 그래프에 없음. "
            f"현재 노드: {', '.join(sorted(g.nodes.keys()))}"
        )}
    shock = Shock(
        origin=origin,
        kind=shock_kind,
        intensity=intensity,
        duration_weeks=duration,
        predicate=predicate,
    )
    structural_data = run_structural_simulation(g, shock)

    # B′ 교차검증 — graceful (백엔드 다운/예외 시 divergence={})
    structural_impacts = {r["ticker"]: r["impactScore"]
                          for r in (structural_data.get("valueChainImpacts") or [])}
    cross = _cross_validate(shock_kind, origin, intensity, duration, target_country, structural_impacts)
    divergence = (cross or {}).get("divergence", {})

    return _format_structural(structural_data, shock_kind, divergence)


def _format_structural(data: dict, shock_kind: str, divergence=None) -> dict:
    """결정론 시뮬 결과 → JARVIS HUD + 음성 브리핑.
    · VICTIM → CASUALTY 라벨 정규화 (REACTIVE simulate와 어휘 통일).
    · divergence (B′): 부호 반대 종목에 ⚠ DIVERGENT 마킹 + 브리핑 부연.
    """
    divergence = divergence or {}
    impacts = data.get("valueChainImpacts") or []
    rows, beneficiaries, casualties = [], [], []
    for r in impacts:
        score = r.get("impactScore", 0)
        raw_type = r.get("impactType", "")
        status = (
            "BENEFICIARY" if raw_type == "BENEFICIARY"
            else "CASUALTY" if raw_type == "VICTIM"
            else "NEUTRAL"
        )
        line = f"{r.get('companyName')} ({r.get('ticker')}, {_signed(score)})"
        if status == "BENEFICIARY":
            beneficiaries.append(line)
        elif status == "CASUALTY":
            casualties.append(line)
        path_str = " ← ".join(f"{src}:{k}" for src, k, _ in r.get("path", [])[:2])
        div = divergence.get(r.get("ticker"))
        div_cell = f"⚠ DIVERGENT (LLM {_signed(div['llm_score'])})" if div else ""
        rows.append([
            r.get("companyName", ""),
            r.get("ticker", ""),
            status,
            _signed(score),
            path_str or r.get("sector", ""),
            div_cell,
        ])

    worst = min(impacts, key=lambda r: r["impactScore"]) if impacts else None
    best = max(impacts, key=lambda r: r["impactScore"]) if impacts else None
    parts = []
    if worst and worst["impactScore"] < 0:
        parts.append(f"최대 타격 {worst['companyName']}({worst['ticker']}) {_signed(worst['impactScore'])}")
    if best and best["impactScore"] > 0:
        parts.append(f"최대 수혜 {best['companyName']}({best['ticker']}) {_signed(best['impactScore'])}")

    divergence_note = ""
    if len(divergence) == 1:
        t, info = next(iter(divergence.items()))
        cname = next((r["companyName"] for r in impacts if r.get("ticker") == t), t)
        divergence_note = (f" 단, {cname}({t})은 LLM이 {_signed(info['llm_score'])}로 반대 평가 — "
                           f"단기 매출 직격 vs 중기 우회 수요 시각 차이 가능.")
    elif len(divergence) >= 2:
        names = ", ".join(next((r["companyName"] for r in impacts if r.get("ticker") == t), t)
                          for t in divergence)
        divergence_note = (f" 단, {names} {len(divergence)}개 종목에서 LLM과 부호 반대 — "
                           f"시계열 시각 차이 검토 필요.")

    briefing = (
        f"{shock_kind} 구조적 시뮬레이션 결과, " + ", ".join(parts) + "입니다." + divergence_note
        if parts else f"{shock_kind} 시뮬: 관심종목 범위 내 유의미한 영향 없음."
    )

    return {
        "success": True,
        "message": briefing,
        "shock_type": shock_kind,
        "engine": "structural_graph+cross_validated" if divergence else "structural_graph",
        "scenario_summary": data.get("scenario"),
        "beneficiaries": beneficiaries,
        "casualties": casualties,
        "divergence": divergence,
        "overlay_table": {
            "type": "table",
            "title": f"Structural · {shock_kind}",
            "headers": ["기업", "티커", "판정", "점수", "경로", "교차검증"],
            "rows": rows,
        },
        "raw": data,
    }
