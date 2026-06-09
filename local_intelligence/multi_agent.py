"""
Local Intelligence — Ollama Multi-Persona Chain-of-Thought Orchestrator

3-Stage Pipeline (Gemini 0):
  Agent A (Technical) → Agent B (Value, cross-validates A) → Orchestrator (synthesis)
"""
from __future__ import annotations

import os
from typing import Any, Callable

_StageFn = Callable[[str, str], str]  # (stage_name, prompt) -> response
_stage_fn: _StageFn | None = None


def set_ollama_stage_fn(fn: _StageFn | None) -> None:
    """테스트용 — stage별 Ollama mock (stage: agent_a | agent_b | orchestrator)."""
    global _stage_fn
    _stage_fn = fn


# ── Context slices per persona ──

def build_technical_context(ctx: dict[str, Any]) -> str:
    """에이전트 A 입력 — 기술·심리 지표만."""
    vp = ctx.get("volume_profile") or {}
    rb = ctx.get("ma_ribbon") or {}
    lines = [
        f"종목: {ctx.get('name')}({ctx.get('ticker')})",
        f"합의가: {ctx.get('consensus_price')} | 변동: {ctx.get('change_pct')}%",
        f"RSI: {ctx.get('rsi', 0):.0f} | BB스퀴즈: {'Y' if ctx.get('bb_squeeze') else 'N'}",
        f"뉴스감성: {ctx.get('sentiment_score', 0):+d}",
    ]
    if vp.get("poc"):
        lines.append(
            f"매물대 POC={vp.get('poc')} 지지={vp.get('support')} "
            f"저항={vp.get('resistance')} | {vp.get('signal', '')}"
        )
    if rb.get("spread_pct") is not None:
        lines.append(
            f"이평선 spread={rb.get('spread_pct')}% state={rb.get('state')} | {rb.get('signal', '')}"
        )
    sm5, sm20 = ctx.get("sentiment_ma5"), ctx.get("sentiment_ma20")
    if sm5 is not None and sm20 is not None:
        lines.append(
            f"감성MA5={sm5:+.1f} MA20={sm20:+.1f} "
            f"골든크로스={'Y' if ctx.get('sentiment_golden_cross') else 'N'}"
        )
    div = (ctx.get("sentiment_divergence_signal") or "").strip()
    if div:
        lines.append(f"감성다이버전스: {div}")
    mx = ctx.get("local_matrix") or {}
    tech = (mx.get("layers") or {}).get("technical", {})
    if tech.get("score") is not None:
        lines.append(f"기술레이어={tech.get('score')}/25")
    return "\n".join(lines)


def build_value_context(ctx: dict[str, Any]) -> str:
    """에이전트 B 입력 — 가치·밸류에이션 지표만."""
    val = ctx.get("valuation") or {}
    dcf = ctx.get("dcf_valuation") or {}
    gr = ctx.get("graham_valuation") or {}
    mx = ctx.get("local_matrix") or {}
    lines = [
        f"종목: {ctx.get('name')}({ctx.get('ticker')})",
        f"합의가: {ctx.get('consensus_price')}",
    ]
    if val.get("score") is not None:
        lines.append(
            f"밸류에이션={val.get('score')}/100 ({val.get('grade')}) "
            f"PER={val.get('per')} ROE={val.get('roe')}%"
        )
    if dcf.get("fair_price") is not None:
        lines.append(
            f"DCF적정=${dcf.get('fair_price')} MoS={dcf.get('margin_of_safety_pct')}% "
            f"WACC={dcf.get('wacc_pct')}%"
        )
    if gr.get("intrinsic_value") is not None:
        lines.append(
            f"그레이엄=${gr.get('intrinsic_value')} 저평가율={gr.get('undervaluation_pct')}%"
        )
    if mx.get("local_matrix_score") is not None:
        layers = mx.get("layers") or {}
        lines.append(
            f"로컬매트릭스={mx.get('local_matrix_score')}/100 ({mx.get('grade')}) "
            f"재무={layers.get('fundamental', {}).get('score', '?')}"
        )
    return "\n".join(lines)


_AGENT_A_PROMPT = """[에이전트 A: 기술적 분석가 자비스]

역할: 매물대·이평선 밀집도·감성 골든크로스/다이버전스만 정량 파싱.
Chain-of-Thought를 반드시 따르세요:
  1) 관측(Observation): 컨텍스트 수치를 bullet로 나열
  2) 해석(Interpretation): 추세·지지·저항·심리 모멘텀 판단
  3) 1차 결론(Conclusion): 2~3문장 기술적 시각 요약

규칙:
- 컨텍스트에 없는 숫자 invent 금지
- Gemini/클라우드 API 언급 금지
- 한국어

[기술·심리 데이터]
{technical_context}
"""


_AGENT_B_PROMPT = """[에이전트 B: 가치 투자자 자비스]

역할: DCF·그레이엌·안전마진으로 펀더멘털 평가. 에이전트 A 리포트를 교차 검증.
Chain-of-Thought:
  1) 가치 관측: DCF/그레이엄/MoS 수치 bullet
  2) 교차 검증: A의 기술 결론과 모순·보완 지점 명시
  3) 가치 결론: 2~3문장 밸류 시각 요약

규칙:
- 컨텍스트에 없는 숫자 invent 금지
- Gemini/클라우드 API 언급 금지
- 한국어

[에이전트 A 1차 리포트]
{agent_a_report}

[가치·밸류에이션 데이터]
{value_context}
"""


_ORCHESTRATOR_PROMPT = """[오케스트레이터: 종합 투자 매트릭스 자비스]

역할: 에이전트 A(기술)와 B(가치) 관점을 통합해 입체적 최종 브리핑 작성.
Chain-of-Thought (내부적으로만, 출력에는 CoT 생략 가능):
  1) A·B 핵심 합의점 / 충돌점 정리
  2) 로컬 종합 매트릭스 관점에서 균형 잡힌 투자 시각 도출
  3) 최종 서술형 브리핑 4~5문장

출력 규칙:
- 반드시 '선생님,'으로 시작하는 하나의 연속 브리핑만 출력
- 기술(매물대·이평)·가치(DCF·MoS)·심리(감성 MA)를 모두 반영
- 컨텍스트에 없는 숫자 invent 금지
- Gemini/클라우드 API 언급 금지
- 한국어

[전체 지표 스냅샷]
{compact_context}

[에이전트 A — 기술적 분석]
{agent_a_report}

[에이전트 B — 가치 투자 (A 교차검증)]
{agent_b_report}
"""


def build_agent_a_prompt(ctx: dict[str, Any]) -> str:
    return _AGENT_A_PROMPT.format(technical_context=build_technical_context(ctx))


def build_agent_b_prompt(ctx: dict[str, Any], agent_a_report: str) -> str:
    return _AGENT_B_PROMPT.format(
        agent_a_report=agent_a_report.strip(),
        value_context=build_value_context(ctx),
    )


def build_orchestrator_prompt(
    ctx: dict[str, Any],
    compact_context: str,
    agent_a_report: str,
    agent_b_report: str,
) -> str:
    return _ORCHESTRATOR_PROMPT.format(
        compact_context=compact_context.strip(),
        agent_a_report=agent_a_report.strip(),
        agent_b_report=agent_b_report.strip(),
    )


def _default_http_ollama(prompt: str) -> str:
    url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct-q4_K_M")
    timeout = float(os.getenv("LOCAL_INTEL_OLLAMA_STAGE_TIMEOUT",
                               os.getenv("LOCAL_INTEL_OLLAMA_TIMEOUT", "20")))
    try:
        import requests
        r = requests.post(
            f"{url}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"num_ctx": int(os.getenv("OLLAMA_NUM_CTX", "4096"))},
            },
            timeout=timeout,
        )
        r.raise_for_status()
        return (r.json().get("message", {}) or {}).get("content", "").strip()
    except Exception:
        return ""


def _invoke_stage(stage: str, prompt: str, legacy_fn: Callable[[str], str] | None) -> str:
    if _stage_fn:
        try:
            return (_stage_fn(stage, prompt) or "").strip()
        except Exception:
            return ""
    if legacy_fn:
        try:
            return (legacy_fn(prompt) or "").strip()
        except Exception:
            return ""
    return _default_http_ollama(prompt)


def multi_agent_enabled() -> bool:
    return os.getenv("LOCAL_INTEL_MULTI_AGENT", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def run_multi_agent_cot(
    ctx: dict[str, Any],
    compact_context: str,
    *,
    legacy_ollama_fn: Callable[[str], str] | None = None,
) -> tuple[str, bool, dict[str, Any]]:
    """
    3-Stage CoT — 실패 시 narrative="" (호출측 rule-based 폴백).
    Returns: (narrative, ollama_used, trace)
    """
    trace: dict[str, Any] = {"stages": {}, "gemini_called": False}

    if not compact_context and not ctx:
        return "", False, trace

    prompt_a = build_agent_a_prompt(ctx)
    agent_a = _invoke_stage("agent_a", prompt_a, legacy_ollama_fn)
    trace["stages"]["agent_a"] = {"prompt_len": len(prompt_a), "output": agent_a[:500]}
    if not agent_a:
        return "", False, trace

    prompt_b = build_agent_b_prompt(ctx, agent_a)
    agent_b = _invoke_stage("agent_b", prompt_b, legacy_ollama_fn)
    trace["stages"]["agent_b"] = {"prompt_len": len(prompt_b), "output": agent_b[:500]}
    if not agent_b:
        return "", False, trace

    prompt_o = build_orchestrator_prompt(ctx, compact_context, agent_a, agent_b)
    final = _invoke_stage("orchestrator", prompt_o, legacy_ollama_fn)
    trace["stages"]["orchestrator"] = {"prompt_len": len(prompt_o), "output": final[:800]}
    if not final:
        return "", False, trace

    trace["agent_a_report"] = agent_a[:600]
    trace["agent_b_report"] = agent_b[:600]
    return final[:1200], True, trace
