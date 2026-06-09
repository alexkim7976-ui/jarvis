"""
local_rule_engine — Local Consensus Matrix Score

기술(14+) + 재무 + 고래 + 소셜 + 확장 지표 → 0~100 로컬 종합 점수. Gemini 0.
"""
from __future__ import annotations

from typing import Any


def compute_local_consensus_matrix(
    ticker: str,
    *,
    tech: dict,
    fundamentals: dict | None,
    volume_profile: dict | None = None,
    ma_ribbon: dict | None = None,
    valuation: dict | None = None,
    hybrid_score: int = 50,
) -> dict[str, Any]:
    """
    stock_quant 레이어 함수 재사용 + 확장 지표 가중.
    """
    vp = volume_profile or {}
    ribbon = ma_ribbon or {}
    val = valuation or {}

    # ── 기술 레이어 (기존 14지표 시그널) ──
    tech_layer = 25
    tech_summary = "중립"
    try:
        from stock_quant import _compute_tech_layer_score
        tech_layer, tech_summary = _compute_tech_layer_score(tech.get("signals") or {})
    except Exception:
        tech_layer = max(0, min(25, int(hybrid_score * 0.25)))

    # ── 확장 기술 보너스 (Volume Profile + MA Ribbon) ──
    ext_bonus = 0
    ext_notes: list[str] = []
    if vp.get("poc"):
        if vp.get("near_poc"):
            ext_bonus += 3
            ext_notes.append("POC 밀집")
        sig = str(vp.get("signal", ""))
        if "지지" in sig:
            ext_bonus += 4
            ext_notes.append("매물대 지지")
        elif "돌파" in sig:
            ext_bonus += 2
            ext_notes.append("매물대 돌파")

    if ribbon.get("breakout"):
        ext_bonus += 5
        ext_notes.append("이평선 폭발")
    elif ribbon.get("squeeze"):
        ext_bonus += 2
        ext_notes.append("이평선 수렴")
    elif ribbon.get("bull_stack"):
        ext_bonus += 3
        ext_notes.append("정배열")
    elif ribbon.get("bear_stack"):
        ext_bonus -= 3
        ext_notes.append("역배열")

    tech_total = max(0, min(35, tech_layer + ext_bonus))

    # ── 재무 레이어 ──
    val_score = int(val.get("score", 50))
    fin_layer = max(0, min(25, int(val_score * 0.25)))
    fin_grade = val.get("grade", "중립")

    # ── 고래 레이어 ──
    whale_layer = 12
    whale_status = "중립"
    whale_notes: list[str] = []
    try:
        from stock_quant import _compute_whale_layer_score
        whale_layer, whale_status, whale_notes = _compute_whale_layer_score(ticker)
    except Exception:
        whale_notes = ["whale 데이터 없음"]

    # ── 소셜 레이어 ──
    social_layer = 12
    social_status = "중립"
    social_notes: list[str] = []
    try:
        from stock_quant import _compute_social_layer_score
        social_layer, social_status, social_notes = _compute_social_layer_score(ticker)
    except Exception:
        social_notes = ["social 데이터 없음"]

    total = max(0, min(100, tech_total + fin_layer + whale_layer + social_layer))

    if total >= 80:
        grade = "STRONG LOCAL BUY"
    elif total >= 65:
        grade = "LOCAL BUY"
    elif total >= 50:
        grade = "HOLD"
    elif total >= 35:
        grade = "CAUTION"
    else:
        grade = "AVOID"

    return {
        "local_matrix_score": total,
        "grade": grade,
        "layers": {
            "technical": {"score": tech_total, "summary": tech_summary, "ext_notes": ext_notes},
            "fundamental": {"score": fin_layer, "grade": fin_grade, "valuation_score": val_score},
            "whale": {"score": whale_layer, "status": whale_status, "notes": whale_notes[:2]},
            "social": {"score": social_layer, "status": social_status, "notes": social_notes[:2]},
        },
        "hybrid_score_ref": hybrid_score,
    }
