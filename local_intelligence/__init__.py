"""Local Intelligence — Ollama Multi-Agent CoT + sandbox 리포트 캐시."""
from .engine import (
    LocalAnalysisReport,
    build_rule_based_narrative,
    gather_fusion_context,
    refresh_local_analysis_report,
    format_tool_response,
    get_local_report,
    run_ollama_qualitative_analysis,
)
from .multi_agent import (
    build_agent_a_prompt,
    build_agent_b_prompt,
    build_orchestrator_prompt,
    build_technical_context,
    build_value_context,
    run_multi_agent_cot,
    set_ollama_stage_fn,
    multi_agent_enabled,
)
from .report_cache import (
    get_report,
    get_latest_report,
    upsert_report,
    clear_reports,
    set_report_path_for_tests,
    report_path,
)

__all__ = [
    "LocalAnalysisReport",
    "gather_fusion_context",
    "build_rule_based_narrative",
    "run_ollama_qualitative_analysis",
    "build_agent_a_prompt",
    "build_agent_b_prompt",
    "build_orchestrator_prompt",
    "build_technical_context",
    "build_value_context",
    "run_multi_agent_cot",
    "set_ollama_stage_fn",
    "multi_agent_enabled",
    "refresh_local_analysis_report",
    "get_local_report",
    "get_report",
    "get_latest_report",
    "upsert_report",
    "clear_reports",
    "set_report_path_for_tests",
    "report_path",
]
