"""
J.A.R.V.I.S — Hybrid Routing & Local Intelligence (Ollama ↔ Gemini)

Cost-Zero Guard:
  LOCAL_TASK (LIGHT/MEDIUM) → Ollama + local_intelligence (Gemini 0)
  EXPERT_TASK             → Gemini + gemini_cache_manager (제한적)

Ollama 불가 → Gemini 안전 폴백.
HYBRID_ROUTING_ENABLED=0 → 레거시 (호출부 분기).

환경변수:
  HYBRID_ROUTING_ENABLED=1
  HYBRID_DEFAULT_TIER=local     # local|expert — 모호한 입력 기본
  LOCAL_INTEL_OLLAMA_ENABLED=1
"""
from __future__ import annotations

from speech_style import USER_DISPLAY_NAME
import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

_log_fn: Callable[[str, str], None] | None = None

_GEMINI_CALLS = 0
_OLLAMA_CALLS = 0


class TaskTier(str, Enum):
    LOCAL_TASK = "local"      # LIGHT + MEDIUM — Ollama 전담
    EXPERT_TASK = "expert"    # 고차원 융합 — Gemini 전담


# 하위 호환 별칭 (기존 테스트·코드)
TaskTier.LIGHT_TASK = TaskTier.LOCAL_TASK
TaskTier.HEAVY_TASK = TaskTier.EXPERT_TASK


@dataclass
class ClassificationResult:
    tier: TaskTier
    reason: str
    method: str = "deterministic"


@dataclass
class RouteDecision:
    tier: TaskTier
    backend: str
    classification: ClassificationResult
    fallback: bool = False
    gemini_called: bool = False
    ollama_called: bool = False


# ── LOCAL (Ollama): 단순 + 주식 1차 분석 85%+ ──
LOCAL_TOOLS = frozenset({
    # 파일·시스템
    "search_files", "list_folder", "organize_folder", "find_recent_files",
    "move_files", "open_file", "open_app", "get_disk_usage", "find_large_files",
    "get_system_status", "get_cpu_usage", "get_memory_usage",
    "get_monitor_status", "check_github_now", "check_playstore_now",
    "get_current_weather", "get_weather_forecast",
    "get_events", "create_event", "delete_event", "get_upcoming",
    "record_stock_trade", "deposit_portfolio_cash", "report_portfolio_status",
    "get_price_alert_status", "list_price_alerts", "cancel_price_alert",
    "set_price_alert", "get_news_monitor_status", "get_whale_tracker_status",
    "get_social_detector_status", "get_stock_monitor_status",
    "get_media_pipeline_status", "cancel_media_job", "get_ir_interpreter_status",
    "check_upcoming_academic_schedule", "refresh_campus_agent_status",
    "register_face", "remove_face", "list_faces",
    # 주식 현재 정보·1차 분석 (로컬 인텔리전스)
    "get_stock_quote", "get_stock_trend", "verify_stock_indicators",
    "analyze_stock_investment", "analyze_stock_multi_source",
    "analyze_stock_news", "get_comprehensive_matrix_score",
    "check_watchlist_now", "compare_stocks_trend", "display_table",
    "fetch_whale_trades", "analyze_social_trends",
    "analyze_tomorrow_stock",
    "get_macro_market_status",
})

# 레거시 호환
LIGHT_TOOLS = LOCAL_TOOLS

# ── EXPERT (Gemini): 몬테카를로·구조적 충격·인과 추론 ──
EXPERT_TOOLS = frozenset({
    "run_strategy_backtest",
    "mirror_whale_portfolio",
    "start_ir_interpreter",
    "web_search", "news_search", "submit_media_url",
    "simulate_supply_chain_scenario", "simulate_structural_shock",
    "analyze_supply_chain_impact",
})

HEAVY_TOOLS = EXPERT_TOOLS

_EXPERT_TOOL_PREFIXES = ("simulate_", "run_shock_")


# ── Semantic ──
_EXPERT_TEXT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"몬테\s*카를로|monte\s*carlo|10[\s,]*000\s*경로", re.I), "monte_carlo"),
    (re.compile(r"스트레스\s*테스트|stress\s*test", re.I), "stress_test"),
    (re.compile(r"구조적\s*충격|structural\s*shock", re.I), "structural_shock"),
    (re.compile(r"인과\s*관계\s*(추론|분석|시뮬)|causal\s*infer", re.I), "causal_inference"),
    (re.compile(r"매크로.*공급망|공급망.*매크로|VIX.*금리.*환율.*충격", re.I), "macro_supply_fusion"),
    (re.compile(r"밸류\s*체인.*what[\-\s]?if|what[\-\s]?if.*시나리오", re.I), "supply_whatif"),
    (re.compile(r"멀티\s*쇼크|multi[\-\s]?shock", re.I), "multi_shock"),
    (re.compile(r"백테스트\s*해석|몬테카를로\s*결과", re.I), "backtest_interpret"),
]

_LOCAL_TEXT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"매매\s*기록|거래\s*기록|record\s*trade", re.I), "trade_record"),
    (re.compile(r"파일\s*(이동|정리)|list\s*folder|organize", re.I), "file_ops"),
    (re.compile(r"시스템\s*상태|cpu|memory", re.I), "system"),
    (re.compile(r"날씨|weather|일정", re.I), "weather_cal"),
    (re.compile(r"알림\s*(목록|취소)|list\s*alert", re.I), "alerts"),
    (re.compile(r"포트폴리오\s*(현황|리포트)", re.I), "portfolio"),
    # 주식 1차 분석 — LOCAL
    (re.compile(r"주가|현재가|시세|얼마|price", re.I), "stock_price"),
    (re.compile(r"RSI|볼린저|bb|스퀴즈|14\s*개\s*지표|기술\s*적", re.I), "technicals"),
    (re.compile(r"교차\s*검증|다중\s*소스|합의\s*가", re.I), "cross_val"),
    (re.compile(r"퀀트|매트릭스\s*점수|종합\s*점수", re.I), "quant_matrix"),
    (re.compile(r"뉴스\s*감성|호재|악재|sentiment", re.I), "news_sentiment"),
    (re.compile(r"브리핑|종목\s*분석|주식\s*분석", re.I), "stock_brief"),
    (re.compile(r"고래|소셜|whale|reddit", re.I), "flow_social"),
    (re.compile(r"내일\s*(주가|전망|시세)|CNN.*트위터|트위터.*CNN|내일\s*오를", re.I), "next_day_forecast"),
]

_LOCAL_INTEL_TOOLS = frozenset({
    # 전용 핸들러로 분기 — generic local_intelligence 로 덮어쓰지 않음
})

_STOCK_QUANT_LOCAL = frozenset({
    "verify_stock_indicators", "get_stock_quote", "get_stock_trend",
    "get_comprehensive_matrix_score", "analyze_stock_investment",
    "get_macro_market_status",
})
_NEWS_LOCAL = frozenset({"analyze_stock_news"})
_FORECAST_LOCAL = frozenset({"analyze_tomorrow_stock"})
_PRICE_ALERT_LOCAL = frozenset({"check_watchlist_now"})


def hybrid_routing_enabled() -> bool:
    return os.getenv("HYBRID_ROUTING_ENABLED", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def no_gemini_mode() -> bool:
    """Mac 로컬 AI 전용 — Gemini API 호출 금지."""
    try:
        from free_mode import free_only_enabled
        if free_only_enabled():
            return True
    except ImportError:
        pass
    if os.getenv("JARVIS_NO_GEMINI", "0").strip().lower() in ("1", "true", "yes", "on"):
        return True
    if os.getenv("JARVIS_PREFER_LOCAL_LLM", "0").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return os.getenv("LLM_PROVIDER", "").strip().lower() == "ollama"


def set_router_logger(fn: Callable[[str, str], None] | None) -> None:
    global _log_fn
    _log_fn = fn


def _log(level: str, msg: str) -> None:
    if _log_fn:
        _log_fn(level, msg)


def reset_call_counts() -> None:
    global _GEMINI_CALLS, _OLLAMA_CALLS
    _GEMINI_CALLS = 0
    _OLLAMA_CALLS = 0


def get_gemini_call_count() -> int:
    return _GEMINI_CALLS


def get_ollama_call_count() -> int:
    return _OLLAMA_CALLS


def _bump_gemini() -> None:
    global _GEMINI_CALLS
    _GEMINI_CALLS += 1


def _bump_ollama() -> None:
    global _OLLAMA_CALLS
    _OLLAMA_CALLS += 1


def classify_tool(tool_name: str) -> ClassificationResult:
    name = (tool_name or "").strip()
    if not name:
        return ClassificationResult(TaskTier.EXPERT_TASK, "empty tool", "default")
    if name in EXPERT_TOOLS:
        return ClassificationResult(TaskTier.EXPERT_TASK, f"expert tool:{name}", "deterministic")
    if name in LOCAL_TOOLS:
        return ClassificationResult(TaskTier.LOCAL_TASK, f"local tool:{name}", "deterministic")
    for prefix in _EXPERT_TOOL_PREFIXES:
        if name.startswith(prefix):
            return ClassificationResult(TaskTier.EXPERT_TASK, f"expert prefix:{prefix}", "deterministic")
    if name.startswith("analyze_supply_"):
        return ClassificationResult(TaskTier.EXPERT_TASK, "supply analyze", "deterministic")
    return ClassificationResult(TaskTier.LOCAL_TASK, f"default local:{name}", "default")


def classify_text(text: str) -> ClassificationResult | None:
    t = (text or "").strip()
    if not t:
        return None
    for pat, tag in _EXPERT_TEXT_PATTERNS:
        if pat.search(t):
            return ClassificationResult(TaskTier.EXPERT_TASK, f"expert semantic:{tag}", "deterministic")
    for pat, tag in _LOCAL_TEXT_PATTERNS:
        if pat.search(t):
            return ClassificationResult(TaskTier.LOCAL_TASK, f"local semantic:{tag}", "deterministic")
    return None


def classify_request(text: str = "", tool_name: str | None = None) -> ClassificationResult:
    if tool_name:
        tc = classify_tool(tool_name)
        if tool_name in (LOCAL_TOOLS | EXPERT_TOOLS):
            return tc
        if any(tool_name.startswith(p) for p in _EXPERT_TOOL_PREFIXES):
            return tc

    semantic = classify_text(text)
    if semantic:
        return semantic

    if tool_name:
        return classify_tool(tool_name)

    default = os.getenv("HYBRID_DEFAULT_TIER", "local").strip().lower()
    tier = TaskTier.EXPERT_TASK if default == "expert" else TaskTier.LOCAL_TASK
    return ClassificationResult(tier, "ambiguous default", "default")


def route_request(
    text: str = "",
    tool_name: str | None = None,
    *,
    ollama_available: bool = True,
) -> RouteDecision:
    cls = classify_request(text, tool_name)
    if cls.tier == TaskTier.EXPERT_TASK:
        if no_gemini_mode() and ollama_available:
            return RouteDecision(tier=cls.tier, backend="ollama", classification=cls)
        return RouteDecision(tier=cls.tier, backend="gemini", classification=cls)
    if ollama_available:
        return RouteDecision(tier=cls.tier, backend="ollama", classification=cls)
    return RouteDecision(
        tier=cls.tier, backend="gemini", classification=cls, fallback=True,
    )


def try_local_tool_execution(tool_name: str, arguments: Any) -> dict | None:
    """
    LOCAL 주식/뉴스 도구 — 전용 핸들러로 정확한 분석 경로 보장 (Gemini 0).
    analyze_stock_multi_source → None (cross_validation 전용).
    """
    if not hybrid_routing_enabled():
        return None

    if classify_tool(tool_name).tier == TaskTier.EXPERT_TASK:
        return None

    if tool_name not in (
        _STOCK_QUANT_LOCAL | _NEWS_LOCAL | _FORECAST_LOCAL | _PRICE_ALERT_LOCAL | _LOCAL_INTEL_TOOLS
    ):
        return None

    try:
        if isinstance(arguments, str):
            import json
            arguments = json.loads(arguments)
    except Exception:
        arguments = arguments or {}

    try:
        if tool_name in _STOCK_QUANT_LOCAL:
            from stock_quant import execute_stock_tool
            _log("OK", f"Local → stock_quant.{tool_name}")
            return execute_stock_tool(tool_name, arguments)

        if tool_name in _NEWS_LOCAL:
            from news_analyzer import execute_news_tool
            _log("OK", f"Local → news_analyzer.{tool_name}")
            return execute_news_tool(tool_name, arguments)

        if tool_name in _FORECAST_LOCAL:
            from next_day_forecast import execute_forecast_tool
            _log("OK", f"Local → next_day_forecast.{tool_name}")
            return execute_forecast_tool(tool_name, arguments)

        if tool_name in _PRICE_ALERT_LOCAL:
            from price_alert import execute_price_alert_tool
            _log("OK", f"Local → price_alert.{tool_name}")
            return execute_price_alert_tool(tool_name, arguments)

        query = (
            (arguments or {}).get("query")
            or (arguments or {}).get("ticker")
            or ""
        ).strip()
        if not query:
            return None

        from local_intelligence.engine import refresh_local_analysis_report, format_tool_response
        rep = refresh_local_analysis_report(query)
        _log("OK", f"Local Intelligence → {tool_name} ({query}) ollama={rep.ollama_used}")
        return format_tool_response(rep)
    except ImportError:
        return None
    except Exception as e:
        _log("WARN", f"Local tool 실패 ({tool_name}): {e}")
        return None


class HybridRoutingEngine:
    def classify(self, text: str = "", tool_name: str | None = None) -> ClassificationResult:
        return classify_request(text, tool_name)

    def route(
        self,
        text: str = "",
        tool_name: str | None = None,
        *,
        check_ollama: Callable[[], bool] | None = None,
    ) -> RouteDecision:
        avail = True if check_ollama is None else check_ollama()
        return route_request(text, tool_name, ollama_available=avail)

    def ask(
        self,
        text: str,
        history: list,
        pipeline,
        *,
        gemini_fn: Callable,
        ollama_fn: Callable,
        check_ollama: Callable[[], bool],
    ) -> str:
        decision = self.route(text, check_ollama=check_ollama)
        _log("INFO", f"Hybrid → {decision.backend} ({decision.classification.reason})")

        if decision.backend == "ollama":
            _bump_ollama()
            try:
                return ollama_fn(text, history, pipeline)
            except Exception as e:
                if no_gemini_mode():
                    _log("WARN", f"Ollama 실패 (Gemini 차단): {e}")
                    return f"죄송합니다, {USER_DISPLAY_NAME}. Mac 로컬 AI 처리에 실패했습니다."
                _log("WARN", f"Ollama 실패 → Gemini 폴백: {e}")
                _bump_gemini()
                return gemini_fn(text, history, pipeline)

        _bump_gemini()
        return gemini_fn(text, history, pipeline)

    def note_tool_invocation(self, tool_name: str) -> ClassificationResult:
        cls = classify_tool(tool_name)
        _log("INFO", f"Tool {tool_name} → {cls.tier.value} ({cls.reason})")
        return cls


_engine: HybridRoutingEngine | None = None


def get_engine() -> HybridRoutingEngine:
    global _engine
    if _engine is None:
        _engine = HybridRoutingEngine()
    return _engine


def note_tool_invocation(tool_name: str) -> ClassificationResult:
    return get_engine().note_tool_invocation(tool_name)


def ask_with_hybrid_routing(
    text: str,
    history: list,
    pipeline,
    *,
    gemini_fn: Callable,
    ollama_fn: Callable,
    check_ollama: Callable[[], bool],
) -> str:
    if not hybrid_routing_enabled():
        if no_gemini_mode():
            return ollama_fn(text, history, pipeline)
        return gemini_fn(text, history, pipeline)
    return get_engine().ask(
        text, history, pipeline,
        gemini_fn=gemini_fn,
        ollama_fn=ollama_fn,
        check_ollama=check_ollama,
    )


__all__ = [
    "TaskTier",
    "ClassificationResult",
    "RouteDecision",
    "HybridRoutingEngine",
    "hybrid_routing_enabled",
    "no_gemini_mode",
    "classify_request",
    "classify_tool",
    "classify_text",
    "route_request",
    "try_local_tool_execution",
    "get_engine",
    "ask_with_hybrid_routing",
    "note_tool_invocation",
    "reset_call_counts",
    "get_gemini_call_count",
    "get_ollama_call_count",
    "LOCAL_TOOLS",
    "EXPERT_TOOLS",
    "LIGHT_TOOLS",
    "HEAVY_TOOLS",
]
