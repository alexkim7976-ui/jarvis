"""
J.A.R.V.I.S — AI 분석 정확도 유틸 (Ollama 옵션·숫자 검증·내러티브 보정)
"""
from __future__ import annotations

import os
import re
from typing import Any


def ollama_accuracy_options(extra: dict | None = None) -> dict:
    """일관된 저-환각 Ollama 생성 옵션."""
    opts: dict[str, Any] = {
        "num_ctx": int(os.getenv("OLLAMA_NUM_CTX", "8192")),
        "temperature": float(os.getenv("OLLAMA_TEMPERATURE", "0.1")),
        "top_p": float(os.getenv("OLLAMA_TOP_P", "0.9")),
        "repeat_penalty": float(os.getenv("OLLAMA_REPEAT_PENALTY", "1.1")),
    }
    num_predict = os.getenv("OLLAMA_NUM_PREDICT", "1536")
    if num_predict.isdigit():
        opts["num_predict"] = int(num_predict)
    num_gpu = int(os.getenv("OLLAMA_NUM_GPU", "-1"))
    if num_gpu != 0:
        opts["num_gpu"] = num_gpu
    if extra:
        opts.update(extra)
    return opts


def numeric_validation_enabled() -> bool:
    return os.getenv("LOCAL_INTEL_VALIDATE_NUMBERS", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _round_variants(v: float) -> set[float]:
    out = set()
    for d in (0, 1, 2):
        out.add(round(v, d))
    return out


def collect_context_numbers(ctx: dict[str, Any] | None) -> set[float]:
    """컨텍스트에 존재하는 수치 집합 (허용 목록)."""
    allowed: set[float] = set()

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            for val in o.values():
                walk(val)
        elif isinstance(o, (list, tuple)):
            for val in o:
                walk(val)
        elif isinstance(o, bool):
            return
        elif isinstance(o, (int, float)):
            if abs(o) < 0.0001:
                return
            allowed.update(_round_variants(float(o)))

    if ctx:
        walk(ctx)
    return allowed


def _parse_number_token(token: str) -> float | None:
    t = token.replace(",", "").replace("%", "").strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def validate_narrative_numbers(
    narrative: str,
    ctx: dict[str, Any] | None,
    *,
    tolerance: float | None = None,
) -> tuple[bool, list[str]]:
    """
    내러티브 속 숫자가 컨텍스트에 없는 값인지 검사.
    Returns: (ok, suspicious_tokens)
    """
    if not narrative or not numeric_validation_enabled():
        return True, []

    tol = tolerance if tolerance is not None else float(
        os.getenv("LOCAL_INTEL_NUMBER_TOLERANCE", "0.08")
    )
    allowed = collect_context_numbers(ctx)
    if not allowed:
        return True, []

    suspicious: list[str] = []
    for m in re.finditer(
        r"(?<!\d)(-?\d[\d,]*\.?\d*)\s*(?:원|%|달러|USD|KRW|주|배|pt)?",
        narrative,
        re.IGNORECASE,
    ):
        raw = m.group(1)
        v = _parse_number_token(raw)
        if v is None:
            continue
        if 1900 <= abs(v) <= 2100 and float(int(v)) == v:
            continue  # 연도
        if abs(v) <= 10 and float(int(v)) == v and abs(v) <= 10:
            # -10~10 감성/점수는 컨텍스트에 없어도 허용 (뉴스 스코어 등)
            if any(abs(v - a) <= 0.5 for a in allowed):
                continue
        matched = any(
            abs(v - a) <= max(0.01, abs(a) * tol) for a in allowed
        )
        if not matched:
            suspicious.append(raw)
    return len(suspicious) == 0, suspicious


def repair_narrative(
    narrative: str,
    ctx: dict[str, Any] | None,
    rule_fallback: str = "",
) -> tuple[str, bool]:
    """
    숫자 검증 실패 시 rule_fallback 또는 rule+짧은 LLM 요약 대체.
    Returns: (text, repaired)
    """
    ok, bad = validate_narrative_numbers(narrative, ctx)
    if ok:
        return narrative, False
    if rule_fallback:
        return rule_fallback, True
    cleaned = re.sub(
        r"(?<!\w)(-?\d[\d,]*\.?\d*)(?!\w)",
        lambda m: m.group(1) if m.group(1).replace(",", "") in bad else m.group(1),
        narrative,
    )
    return cleaned, True


def merge_partial_agent_reports(agent_a: str, agent_b: str, name: str = "") -> str:
    """멀티에이전트 오케스트레이터 실패 시 부분 결과 병합."""
    nm = name or "해당 종목"
    parts = []
    if agent_a.strip():
        parts.append(f"기술 관점: {agent_a.strip()[:350]}")
    if agent_b.strip():
        parts.append(f"가치 관점: {agent_b.strip()[:350]}")
    if not parts:
        return ""
    return f"{nm} 분석이에요. " + " ".join(parts)
