"""
J.A.R.V.I.S — 실행 경로

메인(음성·화면·제스처)은 맥북 CPU.
그 외(시세·뉴스·공급망·검색·비전)는 백엔드를 먼저 시도하고,
실패하면 맥 CPU로 되돌린 다음, 가능한 작업은 오프라인으로 끝낸다.

환경변수:
  JARVIS_MAIN_DEVICE=cpu     기본. gpu|metal 이면 예전 Metal 경로
  JARVIS_BACKEND_URL         그 외 작업용 백엔드. 비어 있으면 로컬 Ollama·공급망 서버
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

# 네트워크 없이도 맥에서 처리되는 도구
OFFLINE_TOOLS = frozenset({
    "search_files", "list_folder", "organize_folder", "find_recent_files",
    "move_files", "open_file", "open_app", "get_disk_usage", "find_large_files",
    "get_system_status", "get_cpu_usage", "get_memory_usage",
    "get_events", "create_event", "delete_event", "get_upcoming",
    "register_face", "remove_face", "list_faces", "display_table",
    "set_volume", "media_play_pause", "set_timer",
    "get_monitor_status", "get_price_alert_status", "list_price_alerts",
    "report_portfolio_status",
})

# 맥 메인 루프 밖에서 백엔드로 보내는 도구
_OTHER_EXACT = frozenset({
    "web_search", "news_search", "submit_media_url", "start_ir_interpreter",
    "run_strategy_backtest", "mirror_whale_portfolio",
    "simulate_supply_chain_scenario", "simulate_structural_shock",
    "analyze_supply_chain_impact", "analyze_chart_image",
})
_OTHER_PREFIXES = (
    "analyze_", "simulate_", "fetch_", "check_watchlist", "compare_stocks",
    "get_stock_", "verify_stock", "get_comprehensive", "run_shock_",
)


class BacktrackSkip(Exception):
    """이 경로는 설정되지 않았거나 조건이 안 맞음. 오류로 치지 않는다."""


@dataclass
class Attempt:
    route: str
    ok: bool
    detail: str


@dataclass
class BacktrackResult:
    ok: bool
    route: str
    value: Any = None
    attempts: list[Attempt] = field(default_factory=list)


def main_uses_cpu() -> bool:
    raw = os.getenv("JARVIS_MAIN_DEVICE", "cpu").strip().lower()
    return raw not in ("gpu", "metal", "mlx")


def apply_runtime_policy() -> None:
    """맥 메인을 CPU로 고정해 Metal 프로세스 STT가 죽으며 나던 오류를 막는다."""
    os.environ.setdefault("GLOG_minloglevel", "2")
    os.environ.setdefault("ABSL_MIN_LOG_LEVEL", "2")
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    _silence_known_noise()
    if not main_uses_cpu():
        return
    os.environ["JARVIS_MAIN_DEVICE"] = "cpu"
    os.environ["JARVIS_WHISPER_BACKEND"] = "faster"
    os.environ["JARVIS_WHISPER_DEVICE"] = "cpu"
    os.environ["JARVIS_STT_PROCESS"] = "0"
    os.environ["OLLAMA_NUM_GPU"] = "0"


def _silence_known_noise() -> None:
    import logging

    class _Drop(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            msg = record.getMessage()
            return "tflite runtime" not in msg and "NORM_RECT" not in msg

    logging.getLogger().addFilter(_Drop())


def lane_for_tool(name: str) -> str:
    if name in _OTHER_EXACT or name.startswith(_OTHER_PREFIXES):
        return "other"
    return "main"


def offline_capable(name: str) -> bool:
    return name in OFFLINE_TOOLS


def describe_error(exc: BaseException) -> str:
    text = str(exc).strip()
    if text:
        return f"{type(exc).__name__}: {text}"
    return f"{type(exc).__name__} (원인 메시지가 비어 있어 해당 경로를 건너뜀)"


def run_backtrack(
    task: str,
    steps: list[tuple[str, Callable[[], Any]]],
) -> BacktrackResult:
    """앞 단계가 실패하면 그 결과를 버리고 다음 단계로 돌아간다."""
    attempts: list[Attempt] = []
    for route, fn in steps:
        try:
            value = fn()
        except BacktrackSkip as exc:
            attempts.append(Attempt(route, False, str(exc).strip() or "skip"))
            continue
        except Exception as exc:
            attempts.append(Attempt(route, False, describe_error(exc)))
            continue
        attempts.append(Attempt(route, True, "ok"))
        return BacktrackResult(True, route, value, attempts)
    return BacktrackResult(False, "", None, attempts)


def backend_url() -> str:
    return os.getenv("JARVIS_BACKEND_URL", "").strip().rstrip("/")


def call_tool_backend(name: str, args: dict | None) -> dict:
    """JARVIS_BACKEND_URL 이 있을 때만 호출. 없으면 건너뛴다."""
    url = backend_url()
    if not url:
        raise BacktrackSkip("백엔드 URL 없음")
    import requests
    response = requests.post(
        f"{url}/jarvis/task",
        json={"tool": name, "args": args or {}},
        timeout=8,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("백엔드 응답 형식 오류")
    if data.get("success") is False:
        raise RuntimeError(str(data.get("message") or "백엔드 실패"))
    return data


def offline_tool_result(name: str) -> dict:
    """로컬 실행까지 실패했을 때의 오프라인 안내. 성공한 척하지 않는다."""
    if name in OFFLINE_TOOLS:
        return {
            "success": False,
            "offline": True,
            "message": "이 작업은 오프라인으로 이 맥에서 처리하는데, 지금은 실패했습니다. 다시 말씀해 주세요.",
        }
    return {
        "success": False,
        "offline": True,
        "message": "지금은 오프라인입니다. 파일, 시스템 상태, 일정, 소리 조절은 이 맥에서 계속 됩니다.",
    }


def offline_reply(text: str) -> str | None:
    """LLM·네트워크가 없을 때 맥에서 바로 답할 수 있는 몇 가지."""
    raw = (text or "").strip().lower()
    if not raw:
        return None
    if any(k in raw for k in ("몇 시", "지금 시간", "현재 시간", "오늘 날짜")):
        now = datetime.now()
        return now.strftime("지금은 %Y년 %m월 %d일 %H시 %M분입니다. 오프라인 상태입니다.")
    if any(k in raw for k in ("cpu", "메모리", "시스템 상태", "용량", "디스크")):
        try:
            if "용량" in raw or "디스크" in raw:
                from file_manager import execute_tool
                res = execute_tool("get_disk_usage", {})
            else:
                from system_monitor import execute_system_tool
                if "메모리" in raw:
                    res = execute_system_tool("get_memory_usage", {})
                elif "cpu" in raw:
                    res = execute_system_tool("get_cpu_usage", {})
                else:
                    res = execute_system_tool("get_system_status", {})
            if isinstance(res, dict) and res.get("success") is not False:
                msg = res.get("message") or res.get("summary")
                if msg:
                    return str(msg)
        except Exception:
            return None
    return None


def guard_tool(
    name: str,
    args: dict | None,
    local: Callable[[], Any],
    log_fn: Callable[[str, str], None] | None = None,
    backend: Callable[[], Any] | None = None,
) -> Any:
    """도구 하나. 그 외 작업은 백엔드 → 맥 CPU → 오프라인 순으로 되돌린다."""
    steps: list[tuple[str, Callable[[], Any]]] = []
    if lane_for_tool(name) == "other":
        def _backend() -> Any:
            if backend is not None:
                return backend()
            return call_tool_backend(name, args)
        steps.append(("backend", _backend))
    steps.append(("mac_cpu", local))
    if offline_capable(name):
        steps.append(("offline", lambda: offline_tool_result(name)))

    result = run_backtrack(name, steps)
    recovered = [a for a in result.attempts[:-1] if not a.ok and a.detail != "백엔드 URL 없음"]
    if log_fn and result.ok and recovered:
        path = " → ".join(a.route for a in result.attempts)
        log_fn("INFO", f"백트래킹 {name}: {path}")
    if result.ok:
        return result.value
    detail = result.attempts[-1].detail if result.attempts else "실패"
    if log_fn:
        log_fn("WARN", f"{name} 모든 경로 실패 — {detail}")
    return {
        "success": False,
        "message": "지금은 처리하지 못했습니다. 파일·시스템·일정·소리 조절은 이 맥에서 계속 됩니다.",
    }


def ollama_failure_reply(user_text: str, exc: BaseException | None = None) -> str:
    local = offline_reply(user_text)
    if local:
        return local
    return (
        "로컬 AI에 연결하지 못했습니다. "
        "시간, 시스템 상태, 파일, 일정, 소리 조절은 오프라인으로 계속 됩니다."
    )
