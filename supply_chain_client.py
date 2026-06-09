"""
J.A.R.V.I.S — Supply Chain Agent 클라이언트
──────────────────────────────────────────
Java Spring Boot 백엔드(supply-chain-agent) REST API 호출.
느린 WiFi 대응: net 모듈의 공용 세션(연결 재사용·재시도) 사용.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

try:
    import net
except Exception:
    import requests as net  # type: ignore

# 음성(STT)·타이핑 공통 입력 게이트. security 모듈이 없으면 최소 검증으로 폴백.
try:
    from security import validate_command_input, sanitize_external_content
except Exception:  # pragma: no cover
    def validate_command_input(text, source="user"):
        t = (text or "").strip()
        return (bool(t), "OK" if t else "빈 입력", t)

    def sanitize_external_content(text, source="web"):
        return text

SUPPLY_CHAIN_URL = os.getenv("SUPPLY_CHAIN_URL", "https://127.0.0.1:8443").rstrip("/")
TIMEOUT = (5, 60)

# CA 경로 해석기 (로컬 자체 서명 인증서 신뢰). tls_certs가 없으면 환경변수만 사용.
try:
    from tls_certs import ca_cert_path as _ca_cert_path
except Exception:  # pragma: no cover
    def _ca_cert_path():
        p = os.getenv("SUPPLY_CHAIN_CA_CERT", "").strip()
        return p or None


def _verify() -> bool | str:
    """requests verify= 인자.
    · https + 로컬 CA 인증서가 있으면 그 경로를 신뢰(자체 서명 검증, MITM 방지).
    · https인데 CA를 못 찾으면 시스템 CA(True)로 — verify=False 임시방편은 절대 쓰지 않음.
    · http면 무의미하므로 True."""
    if SUPPLY_CHAIN_URL.startswith("https://"):
        ca = _ca_cert_path()
        if ca and os.path.exists(ca):
            return ca
    return True


def _auth_headers() -> dict:
    """백엔드 공유 시크릿 인증 헤더. 호출 시점에 환경변수를 읽어 자동 기동이 주입한 키도 반영."""
    key = os.getenv("SUPPLY_CHAIN_API_KEY", "").strip()
    return {"X-API-Key": key} if key else {}


def analyze_supply_chain(
    body_text: str,
    *,
    title: str = "",
    source: str = "JARVIS",
    source_type: str = "NEWS",
    url: str = "",
    language: str = "ko",
) -> dict:
    """
    뉴스/공시 원문을 Supply Chain Agent에 전달해 밸류체인 분석을 수행한다.
    Returns: API JSON (success, result, message)
    """
    # 원문(장문)은 차단하지 않고 간접 프롬프트 인젝션만 중화 — 정상 기사 오탐 방지.
    body_text = sanitize_external_content(body_text or "", source="supply_chain")
    title = sanitize_external_content(title or "", source="supply_chain")
    payload = {
        "title": title,
        "bodyText": body_text,
        "source": source,
        "sourceType": source_type,
        "url": url,
        "language": language,
        "collectedAt": datetime.now(timezone.utc).isoformat(),
    }
    resp = net.post(
        f"{SUPPLY_CHAIN_URL}/api/v1/supply-chain/analyze",
        json=payload,
        headers=_auth_headers(),
        timeout=TIMEOUT,
        verify=_verify(),
    )
    resp.raise_for_status()
    return resp.json()


def simulate_scenario(scenario_text: str) -> dict:
    """
    What-If 가상 시나리오를 Multi-Shock 시뮬레이션 엔진에 전달한다.
    PRICE_SPIKE / REGULATORY / PHYSICAL_DISRUPTION 분류 + 국내 밸류체인 영향 시뮬레이션.
    Returns: MultiShockSimulationPayload JSON (shockType, simulationResults[], jarvisBriefingMessage 등) — 봉투 없이 직접 반환.
    """
    # 시나리오 텍스트는 명령형 입력 → 명령어 주입/경로 조작 하드 차단 + 인젝션 중화.
    ok, reason, scenario_text = validate_command_input(scenario_text, source="supply_chain")
    if not ok:
        raise ValueError(f"입력 보안 검증 실패: {reason}")
    resp = net.post(
        f"{SUPPLY_CHAIN_URL}/api/v1/supply-chain/simulate",
        json={"scenarioText": scenario_text},
        headers=_auth_headers(),
        timeout=TIMEOUT,
        verify=_verify(),
    )
    resp.raise_for_status()
    return resp.json()


def submit_typed_command(text: str, *, mode: str = "simulate", **analyze_kwargs) -> dict:
    """사용자가 직접 '타이핑'한 텍스트 명령을 음성과 동일한 인증/검증 경로로 백엔드에 전송.

    음성(STT)이든 타이핑이든 결국 같은 REST 엔드포인트(_auth_headers의 X-API-Key 포함)와
    동일한 입력 게이트(validate_command_input)를 거치도록 단일화한 진입점이다.

    mode:
      · "simulate" — What-If 시나리오 시뮬레이션(/simulate, Java 백엔드 필요)
      · "analyze"  — 뉴스/공시 원문 밸류체인 분석(/analyze, Java 백엔드 필요)
    Returns: 백엔드 API JSON
    """
    ok, reason, cleaned = validate_command_input(text, source="typed")
    if not ok:
        raise ValueError(f"입력 보안 검증 실패: {reason}")
    if mode == "analyze":
        return analyze_supply_chain(cleaned, **analyze_kwargs)
    return simulate_scenario(cleaned)


def get_analysis(analysis_id: str) -> dict:
    resp = net.get(
        f"{SUPPLY_CHAIN_URL}/api/v1/supply-chain/analyses/{analysis_id}",
        headers=_auth_headers(),
        timeout=TIMEOUT,
        verify=_verify(),
    )
    resp.raise_for_status()
    return resp.json()


def health_check() -> bool:
    try:
        resp = net.get(
            f"{SUPPLY_CHAIN_URL}/api/v1/supply-chain/health",
            timeout=(3, 5),
            verify=_verify(),
        )
        return resp.status_code == 200
    except Exception:
        return False
