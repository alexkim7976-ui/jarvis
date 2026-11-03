"""
Local Intelligence — Ollama Multi-Persona Chain-of-Thought Orchestrator

3-Stage Pipeline (Gemini 0):
  Agent A (Technical) → Agent B (Value, cross-validates A) → Orchestrator (synthesis)
"""
from __future__ import annotations

import os
from typing import Any, Callable

from speech_style import USER_DISPLAY_NAME

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
    st = ctx.get("stress_test_profile") or {}
    worst = st.get("worst") or {}
    if worst.get("expected_mdd_pct") is not None:
        lines.append(
            f"WhatIf[{worst.get('label')}]: MDD={worst.get('expected_mdd_pct')}% "
            f"VaR95={worst.get('var_95_pct')}%"
        )
    return "\n".join(lines)


_AGENT_A_PROMPT = """[Agent A: Technical Analyst JARVIS]

Role: Quantitatively parse volume profile, moving average ribbon, and sentiment golden cross/divergence.
Follow Chain-of-Thought:
  1) Observation: List key numbers from context as bullets
  2) Interpretation: Assess trend, support/resistance, and sentiment momentum
  3) Conclusion: Summarize the technical outlook in 2-3 English sentences

Rules:
- Never invent numbers not present in the context
- Never mention Gemini or cloud APIs
- Respond in English

[Technical & Sentiment Data]
{technical_context}
"""


_AGENT_B_PROMPT = """[Agent B: Value Investor JARVIS]

Role: Evaluate fundamentals using DCF, Graham intrinsic value, and Margin of Safety. Cross-validate Agent A's report.
Chain-of-Thought:
  1) Value Observation: List DCF/Graham/MoS figures
  2) Cross-Validation: Note contradictions or complements to Agent A's technical view
  3) Value Conclusion: Summarize the valuation outlook in 2-3 English sentences

Rules:
- Never invent numbers not present in the context
- Never mention Gemini or cloud APIs
- Respond in English

[Agent A Preliminary Report]
{agent_a_report}

[Value & Valuation Data]
{value_context}
"""


# 병렬 모드용 — A 리포트에 의존하지 않는 '독립' 가치 분석 (교차검증은 오케스트레이터가 수행)
_AGENT_B_PROMPT_SOLO = """[Agent B: Value Investor JARVIS]

Role: Independently evaluate fundamentals using DCF, Graham intrinsic value, and Margin of Safety.
Chain-of-Thought:
  1) Value Observation: List DCF/Graham/MoS figures
  2) Valuation Judgment: Assess undervalued/fair/overvalued basis
  3) Value Conclusion: Summarize the valuation outlook in 2-3 English sentences

Rules:
- Never invent numbers not present in the context
- Never mention Gemini or cloud APIs
- Respond in English

[Value & Valuation Data]
{value_context}
"""


_ORCHESTRATOR_PROMPT = """[Orchestrator: Comprehensive Investment Matrix JARVIS]

Role: Synthesize Agent A (Technical) and Agent B (Value) perspectives into a cohesive final spoken briefing.
Chain-of-Thought (internal only, output only the final spoken narrative):
  1) Reconcile key agreements and divergences between A and B
  2) Derive a balanced view from the local comprehensive matrix
  3) Final narrative briefing in 4-5 sentences

Output Rules:
- Natural, articulate spoken **English** in the style of J.A.R.V.I.S. (no bullet points, no markdown).
- 4 to 5 sentences. Address '__USER_NAME__' at most once. Never use 'Tony' or Korean honorifics.
- Incorporate technicals (volume profile/MA), value (DCF/MoS), sentiment MA, and What-If stress risk (MDD/VaR).
- Never invent numbers or external events not present in the context.
- Never mention Gemini or cloud APIs.
- Respond strictly in English.

[Full Metrics Snapshot]
{compact_context}

[Agent A — Technical Analysis]
{agent_a_report}

[Agent B — Value Analysis]
{agent_b_report}
"""


def build_agent_a_prompt(ctx: dict[str, Any]) -> str:
    return _AGENT_A_PROMPT.format(technical_context=build_technical_context(ctx))


def build_agent_b_prompt(ctx: dict[str, Any], agent_a_report: str) -> str:
    return _AGENT_B_PROMPT.format(
        agent_a_report=agent_a_report.strip(),
        value_context=build_value_context(ctx),
    )


def build_agent_b_prompt_solo(ctx: dict[str, Any]) -> str:
    """A 리포트에 의존하지 않는 독립 가치분석 (병렬 실행용)."""
    return _AGENT_B_PROMPT_SOLO.format(value_context=build_value_context(ctx))


def build_orchestrator_prompt(
    ctx: dict[str, Any],
    compact_context: str,
    agent_a_report: str,
    agent_b_report: str,
) -> str:
    return _ORCHESTRATOR_PROMPT.replace("__USER_NAME__", USER_DISPLAY_NAME).format(
        compact_context=compact_context.strip(),
        agent_a_report=agent_a_report.strip(),
        agent_b_report=agent_b_report.strip(),
    )


def _ma_parallel() -> bool:
    """토론 에이전트 A·B를 병렬 실행할지 (기본 ON). 정밀 분석 속도의 핵심."""
    return os.getenv("JARVIS_MA_PARALLEL", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _agent_model() -> str:
    """A·B(토론) 스테이지용 '빠른' 모델. 미설치면 stock 모델로 안전 폴백.
    숫자는 교차검증이 확정하므로 서술·판단은 경량 모델로 충분(정확도 손실 최소)."""
    want = (os.getenv("OLLAMA_STOCK_AGENT_MODEL") or "qwen2.5:7b-instruct").strip()
    try:
        from ollama_agent import _model_installed, get_stock_model
        if want and _model_installed(want):
            return want
        return get_stock_model()
    except Exception:
        return want


def _orch_model() -> str:
    """오케스트레이터(최종 종합) 스테이지용 정밀 모델(기본 stock 모델=Nemotron 30B)."""
    try:
        from ollama_agent import get_stock_model
        return get_stock_model()
    except Exception:
        return os.getenv("OLLAMA_STOCK_MODEL", os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct"))


def _stage_model(stage: str) -> str:
    return _orch_model() if stage == "orchestrator" else _agent_model()


def _stage_options(stage: str) -> dict | None:
    """스테이지별 생성 길이 상한 — 전역 설정은 건드리지 않고 여기서만 짧게(=빠르게)."""
    if stage == "orchestrator":
        np = os.getenv("OLLAMA_ORCH_NUM_PREDICT", "768")
    else:
        np = os.getenv("OLLAMA_AGENT_NUM_PREDICT", "560")
    np = np.strip()
    return {"num_predict": int(np)} if np.isdigit() else None


def _default_http_ollama(prompt: str, *, model: str | None = None,
                         options: dict | None = None) -> str:
    timeout = float(os.getenv("LOCAL_INTEL_OLLAMA_STAGE_TIMEOUT",
                               os.getenv("LOCAL_INTEL_OLLAMA_TIMEOUT", "45")))
    try:
        from ollama_agent import get_stock_model
        from ollama_llm import ollama_chat
        m = model or get_stock_model()
        return ollama_chat(
            m,
            [{"role": "user", "content": prompt}],
            timeout=timeout,
            options=options,
        )
    except Exception:
        pass
    url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    m = model
    if not m:
        try:
            from ollama_agent import get_stock_model
            m = get_stock_model()
        except ImportError:
            m = os.getenv("OLLAMA_STOCK_MODEL", os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct"))
    try:
        import requests
        from ai_accuracy import ollama_accuracy_options
        r = requests.post(
            f"{url}/api/chat",
            json={
                "model": m,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": ollama_accuracy_options(options),
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
    return _default_http_ollama(prompt, model=_stage_model(stage),
                                options=_stage_options(stage))


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

    # 실제(기본) 경로에서만 병렬 — 테스트 stage_fn / legacy_fn 경로는 순차 유지(결정성).
    if _ma_parallel() and _stage_fn is None and legacy_ollama_fn is None:
        prompt_a = build_agent_a_prompt(ctx)
        prompt_b = build_agent_b_prompt_solo(ctx)   # A에 의존하지 않는 독립 가치분석
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as ex:
            fa = ex.submit(_invoke_stage, "agent_a", prompt_a, legacy_ollama_fn)
            fb = ex.submit(_invoke_stage, "agent_b", prompt_b, legacy_ollama_fn)
            agent_a = fa.result()
            agent_b = fb.result()
        trace["parallel"] = True
        trace["stages"]["agent_a"] = {"prompt_len": len(prompt_a), "output": agent_a[:500]}
        trace["stages"]["agent_b"] = {"prompt_len": len(prompt_b), "output": agent_b[:500]}
        if not agent_a and not agent_b:
            return "", False, trace
    else:
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
        try:
            from ai_accuracy import merge_partial_agent_reports
            merged = merge_partial_agent_reports(
                agent_a, agent_b, name=str(ctx.get("name") or ctx.get("ticker") or ""),
            )
            if merged:
                trace["partial_merge"] = True
                return merged[:1500], True, trace
        except ImportError:
            pass
        return "", False, trace

    trace["agent_a_report"] = agent_a[:600]
    trace["agent_b_report"] = agent_b[:600]
    return final[:1500], True, trace
