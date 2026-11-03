from __future__ import annotations
import os, sys, time, wave, json, queue, struct, shutil, re
os.environ.setdefault("GLOG_minloglevel", "2")
os.environ.setdefault("ABSL_MIN_LOG_LEVEL", "2")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
import threading, subprocess
import warnings
from pathlib import Path
from datetime import datetime

# 실행에 영향 없는 무해한 경고 노이즈 억제
#  - faster-whisper가 무음/짧은 오디오 처리 시 발생시키는 matmul RuntimeWarning
#  - protobuf의 SymbolDatabase.GetPrototype() deprecated UserWarning
warnings.filterwarnings("ignore", message="invalid value encountered in matmul")
warnings.filterwarnings("ignore", message=".*SymbolDatabase.GetPrototype.*")
warnings.filterwarnings("ignore", message=".*pkg_resources is deprecated.*")

# 로그인으로 이름·메일·언어를 넣은 뒤에 나머지 모듈이 그 값을 읽게 한다.
from dotenv import load_dotenv
_early_data = (os.getenv("JARVIS_DATA_DIR") or "").strip()
if _early_data and Path(_early_data, ".env").is_file():
    load_dotenv(Path(_early_data) / ".env")
else:
    load_dotenv()

from i18n import (
    LANGUAGE_META,
    SUPPORTED_LANGS,
    apply_saved_or_default_lang,
    build_language_directive,
    cycle_lang,
    get_ack_phrases,
    get_lang,
    get_lang_meta,
    interpret_language_command,
    set_lang,
    tr,
)
apply_saved_or_default_lang()

if (
    __name__ == "__main__"
    and "--test-telegram" not in sys.argv
    and os.getenv("JARVIS_SKIP_LOGIN", "").strip().lower() not in ("1", "true", "yes", "on")
):
    from user_account import ensure_login
    print("▶ Opening sign-in window… (press ⌘+Tab → Python if hidden)", flush=True)
    ensure_login()
    print("▶ Sign-in complete — loading J.A.R.V.I.S…", flush=True)

import cv2, numpy as np, pyaudio, requests
from dotenv import load_dotenv


class _LazyModuleAttr:
    """Lazy module / attribute proxy to avoid blocking top-level imports (mediapipe, openai, etc.)."""
    def __init__(self, mod_name: str, attr_path: str = ""):
        self._mod_name = mod_name
        self._attr_path = attr_path
        self._target = None

    def _resolve(self):
        if self._target is None:
            import importlib
            obj = importlib.import_module(self._mod_name)
            for part in (self._attr_path.split(".") if self._attr_path else ()):
                if part:
                    obj = getattr(obj, part)
            self._target = obj
        return self._target

    def __getattr__(self, name: str):
        return getattr(self._resolve(), name)

    def __call__(self, *args, **kwargs):
        return self._resolve()(*args, **kwargs)


mp = _LazyModuleAttr("mediapipe")
OpenAI = _LazyModuleAttr("openai", "OpenAI")

import net  # 느린 WiFi 최적화: 연결 재사용·재시도·적응형 타임아웃 공용 세션

from file_manager import (FILE_TOOLS, execute_tool as execute_file_tool,
                          get_file_context_suffix)
from web_search import SEARCH_TOOLS, execute_search_tool
from screen_overlay import (ScreenOverlay, DISPLAY_TOOLS,
                            execute_display_tool, set_overlay_instance)
from face_module import (FaceRecognizer, FACE_TOOLS,
                         execute_face_tool, set_face_instance, set_current_frame)
from weather_module import WEATHER_TOOLS, execute_weather_tool
from system_monitor import SYSTEM_MONITOR_TOOLS, execute_system_tool
from calendar_module import CALENDAR_TOOLS, execute_calendar_tool
from hud_ironman import IronManHUD
from chat_log import ChatLog
from wake_word import WakeWordDetector
from streaming_tts import StreamingTTSPipeline
from speech_style import (VOICE_STYLE_INSTRUCTIONS, USER_NAME_RULES, USER_DISPLAY_NAME,
                          ACK_PHRASES, polish_for_voice, with_user_address)
from speech_gate import (begin_speech, end_speech, wait_audio_idle,
                         speech_active, reset_speech_gate,
                         shutdown_speech_gate, is_shutting_down)
from gesture_control import GestureController
from voice_gesture import VoiceGestureHandler, detect_from_landmarks, voice_gestures_enabled
from screen_keyboard import ScreenKeyboard
from screen_help import draw_help
from screen_performance import draw_performance_panel, handle_performance_key
from performance_presets import interpret_performance_command
from power_save_mode import (
    PowerSaveFrameReader,
    draw_power_save_button,
    interpret_power_save_command,
    is_active as power_save_active,
    set_power_save,
    status_message as power_save_status,
    wants_power_save_off,
    wants_power_save_on,
)
from background_control import (
    BACKGROUND_TOOLS, BACKGROUND_TOOL_NAMES, bind_task, draw_background_panel,
    execute_background_tool, interpret_control_command, is_enabled,
)
from interest_prefetch import prefetch_running, start_prefetch, stop_prefetch
from background_monitor import (BackgroundMonitor, MONITOR_TOOLS,
                                execute_monitor_tool, set_monitor_instance,
                                MONITOR_INTERVAL)
from stock_quant import STOCK_QUANT_TOOLS, execute_stock_tool, get_macro_market_status
from macro_guard import MacroGuardWatcher
from jarvis_link import init_link
from gaze_context import get_gaze_context
from news_analyzer import (NewsAnalyzer, NEWS_TOOLS, execute_news_tool,
                           set_news_analyzer_instance, NEWS_WATCHLIST)
from portfolio_manager import (PortfolioManager, PORTFOLIO_TOOLS,
                               execute_portfolio_tool, set_portfolio_instance)
from backtest_engine import BACKTEST_TOOLS, execute_backtest_tool
from price_alert import (PriceAlertWorker, PRICE_ALERT_TOOLS,
                         execute_price_alert_tool, set_price_alert_instance, WATCH_LIST,
                         take_pending_selection, add_target_choice, add_target_by_query)
from whale_tracker import (WhaleTracker, WHALE_TOOLS, execute_whale_tool,
                           set_whale_tracker_instance, WHALE_WATCHLIST)
from social_detector import (SocialSpikeDetector, SOCIAL_TOOLS, execute_social_tool,
                             set_social_detector_instance, SOCIAL_SUBREDDITS)
from next_day_forecast import FORECAST_TOOLS, execute_forecast_tool
from ir_interpreter import (IRInterpreter, IR_TOOLS, execute_ir_tool,
                            set_ir_interpreter_instance)
from stock_monitor import (StockMonitor, STOCK_MONITOR_TOOLS, execute_stock_monitor_tool,
                           set_stock_monitor_instance)
from campus_agent import (CampusAgent, CAMPUS_TOOLS, execute_campus_tool,
                          set_campus_agent_instance, campus_mail_account)
from supply_chain_module import SUPPLY_CHAIN_TOOLS, execute_supply_chain_tool
from cross_validation import (CROSSVAL_TOOLS, CROSSVAL_TOOL_NAMES,
                              execute_crossval_tool)
from supply_chain_server import ensure_running as start_supply_chain_backend, stop_backend as stop_supply_chain_backend
from security import (sanitize_tool_args, audit_tool_call, tool_rate_limiter,
                      check_env_security, is_boot_blocking_security_warning,
                      mask_key, detect_prompt_injection,
                      sanitize_external_content, guard_destructive_tool,
                      verify_audit_chain, harden_sensitive_files, chain_event,
                      validate_command_input, validate_llm_input,
                      validate_llm_output, register_tools,
                      verify_audit_log_integrity, reinitialize_audit_log,
                      write_audit_log, guard_fds_tool, set_fds_engine)
from secure_config import get_secret, filter_llm_output, get_security_status
from security_policy import (
    apply_environment_hardening, reconfigure_security, security_summary,
    get_security_level, SecurityLevel, voice_auth_required,
    boot_halt_on_security_warn, is_maximum_security,
)
from openclaw_guard import OPENCLAW_TOOLS, execute_openclaw_tool, boot_openclaw_guard
from resource_profile import (
    is_low_mem, vision_enabled, hands_enabled, camera_resolution, main_loop_fps,
    hand_complexity, face_worker_interval, stagger_workers_enabled,
    worker_stagger_seconds, performance_profile_summary,
)
from context_manager import get_context_window, append_exchange, context_turn_limit
from gemini_cache_manager import (
    get_cache_manager, log_gemini_usage, set_cache_logger, cache_enabled,
    inject_market_snapshot_into_user_message,
)
from hybrid_router import (
    ask_with_hybrid_routing, hybrid_routing_enabled, no_gemini_mode, note_tool_invocation,
    set_router_logger, try_local_tool_execution,
)
from ollama_agent import chat_with_tools as ollama_chat_with_tools, model_supports_tools
from alert_pipeline import create_jarvis_runtime, get_runtime, init_runtime
from media_tools import (
    MEDIA_TOOLS, execute_media_tool, set_media_runtime,
    build_media_overlay_table, register_media_artifact, get_media_context_suffix,
)
from media_pipeline import create_media_runtime, init_media_runtime
from stt_backend import resolve_stt_mode, shutdown_process_pool
from free_mode import apply_free_mode_defaults, free_only_enabled, free_mode_summary
from runtime_backtrack import apply_runtime_policy, offline_reply
from free_mode import openai_client_for_stt, use_paid_tts
from local_tts import synthesize_macos, stop_all_local_tts
from mac_control import MAC_CONTROL_TOOLS, execute_mac_control_tool
from power_manager import (
    write_pid, clear_pid, read_pid, consume_wake_request,
    IdleTracker, auto_quit_idle_seconds,
)
from voice_profile import voice_setup_required, is_known_device

# 출시 앱: ~/Library/Application Support/JARVIS/.env 우선
_data_dir = (os.getenv("JARVIS_DATA_DIR") or "").strip()
if _data_dir and Path(_data_dir, ".env").is_file():
    load_dotenv(Path(_data_dir) / ".env")
else:
    load_dotenv()
apply_free_mode_defaults()
apply_runtime_policy()

# faster-whisper 모델은 whisper_pool.get_whisper_model()이 공용 인스턴스로 관리 (웨이크워드와 공유)

OPENAI_API_KEY = get_secret("OPENAI_API_KEY")
FISH_API_KEY   = get_secret("FISH_AUDIO_API_KEY")
FISH_VOICE_ID  = get_secret("FISH_VOICE_ID")
LLM_PROVIDER   = os.getenv("LLM_PROVIDER","gemini").lower()
GEMINI_API_KEY = get_secret("GEMINI_API_KEY")
# 하이브리드 모델 이원화: 경량 대화/명령은 FLASH, 차트 비전·복합추론은 PRO
GEMINI_FLASH_MODEL   = os.getenv("GEMINI_FLASH_MODEL","gemini-3.5-flash")
GEMINI_PRO_MODEL     = os.getenv("GEMINI_PRO_MODEL","gemini-1.5-pro")
GEMINI_CONTEXT_TURNS = int(os.getenv("GEMINI_CONTEXT_TURNS","10"))  # 호출당 전달 최근 메시지 수
OLLAMA_MODEL        = os.getenv("OLLAMA_MODEL", "gemma4:latest")
OLLAMA_STOCK_MODEL  = os.getenv("OLLAMA_STOCK_MODEL", "qwen2.5:7b-instruct")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "moondream")
OLLAMA_URL     = os.getenv("OLLAMA_URL","http://localhost:11434")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX","4096"))
OLLAMA_NUM_GPU = int(os.getenv("OLLAMA_NUM_GPU","-1"))  # -1=Metal GPU 전 레이어 (Ollama 기본)
AUDIO_DIR      = Path(os.getenv("AUDIO_SAVE_DIR","audio_cache"))
AUDIO_DIR.mkdir(exist_ok=True)

SAMPLE_RATE=16_000; CHANNELS=1; CHUNK=1_024
RECORD_FORMAT=pyaudio.paInt16; MAX_RECORD_SECONDS=15
PROCESS_DELAY=float(os.getenv("JARVIS_PROCESS_DELAY", "0.05"))   # 녹음 자동 종료 후 PROCESSING 전환까지 대기(초, 기본 0.05)
VAD_THRESHOLD=int(os.getenv("VAD_THRESHOLD","800"))
VAD_CONSECUTIVE=int(os.getenv("VAD_CONSECUTIVE","4"))
FACE_CHECK_INTERVAL=int(os.getenv("FACE_CHECK_INTERVAL","15"))
PLAY_TIMEOUT=float(os.getenv("JARVIS_PLAY_TIMEOUT","180"))  # afplay 등 재생 프로세스 최대 대기(초)
# 메인 루프 FPS — resource_profile.main_loop_fps() (LOW_MEM 기본 15, 일반 30)

ALL_TOOLS = FILE_TOOLS + MAC_CONTROL_TOOLS + SEARCH_TOOLS + DISPLAY_TOOLS + FACE_TOOLS + WEATHER_TOOLS + SYSTEM_MONITOR_TOOLS + CALENDAR_TOOLS + MONITOR_TOOLS + STOCK_QUANT_TOOLS + NEWS_TOOLS + PORTFOLIO_TOOLS + BACKTEST_TOOLS + PRICE_ALERT_TOOLS + WHALE_TOOLS + SOCIAL_TOOLS + FORECAST_TOOLS + IR_TOOLS + MEDIA_TOOLS + STOCK_MONITOR_TOOLS + CAMPUS_TOOLS + SUPPLY_CHAIN_TOOLS + CROSSVAL_TOOLS + OPENCLAW_TOOLS + BACKGROUND_TOOLS
FILE_TOOL_NAMES    = {t["function"]["name"] for t in FILE_TOOLS}
MAC_CONTROL_TOOL_NAMES = {t["function"]["name"] for t in MAC_CONTROL_TOOLS}
SEARCH_TOOL_NAMES  = {t["function"]["name"] for t in SEARCH_TOOLS}
DISPLAY_TOOL_NAMES = {t["function"]["name"] for t in DISPLAY_TOOLS}
FACE_TOOL_NAMES    = {t["function"]["name"] for t in FACE_TOOLS}
WEATHER_TOOL_NAMES = {t["function"]["name"] for t in WEATHER_TOOLS}
SYSTEM_TOOL_NAMES  = {t["function"]["name"] for t in SYSTEM_MONITOR_TOOLS}
CALENDAR_TOOL_NAMES = {t["function"]["name"] for t in CALENDAR_TOOLS}
MONITOR_TOOL_NAMES = {t["function"]["name"] for t in MONITOR_TOOLS}
STOCK_TOOL_NAMES   = {t["function"]["name"] for t in STOCK_QUANT_TOOLS}
NEWS_TOOL_NAMES    = {t["function"]["name"] for t in NEWS_TOOLS}
PORTFOLIO_TOOL_NAMES = {t["function"]["name"] for t in PORTFOLIO_TOOLS}
BACKTEST_TOOL_NAMES = {t["function"]["name"] for t in BACKTEST_TOOLS}
PRICE_ALERT_TOOL_NAMES = {t["function"]["name"] for t in PRICE_ALERT_TOOLS}
WHALE_TOOL_NAMES = {t["function"]["name"] for t in WHALE_TOOLS}
SOCIAL_TOOL_NAMES = {t["function"]["name"] for t in SOCIAL_TOOLS}
FORECAST_TOOL_NAMES = {t["function"]["name"] for t in FORECAST_TOOLS}
IR_TOOL_NAMES = {t["function"]["name"] for t in IR_TOOLS}
MEDIA_TOOL_NAMES = {t["function"]["name"] for t in MEDIA_TOOLS}
STOCK_MONITOR_TOOL_NAMES = {t["function"]["name"] for t in STOCK_MONITOR_TOOLS}
CAMPUS_TOOL_NAMES = {t["function"]["name"] for t in CAMPUS_TOOLS}
SUPPLY_CHAIN_TOOL_NAMES = {t["function"]["name"] for t in SUPPLY_CHAIN_TOOLS}
OPENCLAW_TOOL_NAMES = {t["function"]["name"] for t in OPENCLAW_TOOLS}
# CROSSVAL_TOOL_NAMES은 cross_validation 모듈에서 직접 import (중복 정의 회피)

# 출력 가드레일(validate_llm_output) 화이트리스트/타입 검증용 스키마 등록
register_tools(ALL_TOOLS)

# 메인 루프에서 생성한 오버레이 인스턴스 참조 (stock 분석 결과 자동 투사용)
_OVERLAY_REF = None
def set_overlay_ref(ov):
    global _OVERLAY_REF; _OVERLAY_REF = ov

def warnings_screen_only():
    """선제적 경고(매크로/고래/소셜/주가/지정가/리스크)를 음성 없이 '화면으로만' 설명할지.
    기본 ON. JARVIS_WARN_SCREEN_ONLY=0 으로 끄면 기존처럼 음성으로도 브리핑."""
    return os.getenv("JARVIS_WARN_SCREEN_ONLY", "1").strip().lower() not in ("0", "false", "off")

def _dispatch_tool(name, args):
    # ── 보안 게이트 ──
    if not tool_rate_limiter.allow():
        log("WARN", f"⛔ 속도 제한 초과 — 도구 호출 차단: {name}")
        chain_event("RATE_LIMIT_BLOCK", {"tool": name})
        return {"success": False, "message": "도구 호출 속도 제한 초과. 잠시 후 다시 시도하세요."}
    safe, reason, args = sanitize_tool_args(name, args)
    if not safe:
        log("WARN", f"⛔ 입력 검증 실패 — {name}: {reason}")
        _evt = "PATH_VIOLATION" if ("경로" in reason or "path" in reason.lower()) else "INPUT_VALIDATION_BLOCK"
        write_audit_log(_evt, f"tool={name} 사유={reason}")
        return {"success": False, "message": f"보안 검증 실패: {reason}"}
    # 파괴적(비가역) 도구 전용 가드 — 전용 속도 제한 + 감사
    d_ok, d_reason = guard_destructive_tool(name, args)
    if not d_ok:
        log("WARN", f"⛔ 파괴적 작업 차단 — {name}: {d_reason}")
        chain_event("DESTRUCTIVE_BLOCK", {"tool": name, "reason": d_reason})
        return {"success": False, "message": f"보안 차단: {d_reason}"}
    # FDS 동적 신뢰 — 고위험 도구(금융·파괴적) 행동 이상 탐지
    f_ok, f_reason = guard_fds_tool(name, args)
    if not f_ok:
        log("WARN", f"⛔ FDS 차단 — {name}: {f_reason}")
        return {"success": False, "message": f"보안 차단(FDS): {f_reason}"}
    # ── 출력 가드레일 (Outbound) — execute_xxx_tool() 호출 직전 최종 검증 ──
    o_ok, o_reason = validate_llm_output(name, args)
    if not o_ok:
        log("WARN", f"⛔ 출력 가드레일 차단 — {name}: {o_reason}")
        write_audit_log("OUTBOUND_GUARD", f"tool={name} 차단 사유={o_reason}")
        return {"success": False, "message": f"보안 차단(출력 가드레일): {o_reason}"}
    audit_tool_call(name, args)

    try:
        note_tool_invocation(name)
    except Exception:
        pass

    try:
        local_result = try_local_tool_execution(name, args)
        if local_result is not None:
            return local_result
    except Exception:
        pass

    if name in FILE_TOOL_NAMES:    return execute_file_tool(name, args)
    if name in MAC_CONTROL_TOOL_NAMES: return execute_mac_control_tool(name, args)
    if name in SEARCH_TOOL_NAMES:
        # 웹/뉴스 검색 결과는 LLM 컨텍스트로 되돌아가므로 간접 프롬프트 인젝션 중화
        result = execute_search_tool(name, args)
        try:
            for r in result.get("results", []) if isinstance(result, dict) else []:
                if isinstance(r, dict) and "content" in r:
                    r["content"] = sanitize_external_content(r["content"], source=name)
            if isinstance(result, dict) and isinstance(result.get("answer"), str):
                result["answer"] = sanitize_external_content(result["answer"], source=name)
        except Exception:
            pass
        return result
    if name in DISPLAY_TOOL_NAMES: return execute_display_tool(name, args)
    if name in FACE_TOOL_NAMES:    return execute_face_tool(name, args)
    if name in WEATHER_TOOL_NAMES: return execute_weather_tool(name, args)
    if name in SYSTEM_TOOL_NAMES:  return execute_system_tool(name, args)
    if name in CALENDAR_TOOL_NAMES: return execute_calendar_tool(name, args)
    if name in MONITOR_TOOL_NAMES: return execute_monitor_tool(name, args)
    if name in STOCK_TOOL_NAMES:
        result = execute_stock_tool(name, args)
        # 분석 성공 시 overlay_table을 웹캠 화면에 자동 투사 (LLM의 별도 display_table 불필요)
        if result.get("success") and result.get("overlay_table") and _OVERLAY_REF:
            try:
                table = dict(result["overlay_table"])
                risk = result.get("risk_level")
                reasons = result.get("reasons") or []
                fx_warn = any("환율" in str(r) or "원달러" in str(r) for r in reasons)
                use_alert = bool(
                    result.get("guardrail_active")
                    or risk in ("위험", "경계")
                    or fx_warn
                )
                dur = 60 if use_alert else 20
                _OVERLAY_REF.set_data(table, dur, alert=use_alert)
                log("TOOL", "주식 분석표 웹캠 오버레이 투사")
            except Exception as e:
                log("WARN", f"오버레이 투사 실패: {e}")
        _rt = get_runtime()
        if _rt and result.get("success"):
            try:
                _rt.risk_feed.ingest_stock_tool_result(result)
            except Exception as e:
                log("WARN", f"Alert Pipeline risk ingest: {e}")
        # 심층 분석을 먼저 답한 뒤, 모든 무료 데이터·AI로 백그라운드 교차검증 후속(정정/보강)
        try:
            from cv_followup import two_phase_enabled, get_cv_followup
            if (two_phase_enabled() and result.get("success")
                    and name in ("analyze_stock_investment", "get_comprehensive_matrix_score")):
                _tk = str((args or {}).get("ticker") or result.get("ticker") or "").strip()
                if _tk:
                    get_cv_followup(logger=lambda lvl, msg: log(lvl, msg)).start(
                        _tk, preliminary_price=result.get("price"),
                    )
        except Exception as e:
            log("WARN", f"교차검증 후속 트리거 실패: {e}")
        return result
    if name in NEWS_TOOL_NAMES:
        result = execute_news_tool(name, args)
        # 온디맨드 뉴스 분석 결과도 표를 웹캠 화면에 자동 투사
        if result.get("success") and result.get("overlay_table") and _OVERLAY_REF:
            try:
                _OVERLAY_REF.set_data(result["overlay_table"], 22)
                log("TOOL", "뉴스 분석표 웹캠 오버레이 투사")
            except Exception as e:
                log("WARN", f"오버레이 투사 실패: {e}")
        return result
    if name in PORTFOLIO_TOOL_NAMES:
        result = execute_portfolio_tool(name, args)
        # 계좌 리포트 표를 웹캠 화면에 자동 투사
        if result.get("success") and result.get("overlay_table") and _OVERLAY_REF:
            try:
                _OVERLAY_REF.set_data(result["overlay_table"], 22)
                log("TOOL", "포트폴리오 리포트 웹캠 오버레이 투사")
            except Exception as e:
                log("WARN", f"오버레이 투사 실패: {e}")
        return result
    if name in BACKTEST_TOOL_NAMES:
        result = execute_backtest_tool(name, args)
        # 백테스트 차트 이미지 팝업(+지표 표)을 우측에 반투명 투사. 차트가 없으면 표로 폴백.
        if result.get("success") and _OVERLAY_REF:
            try:
                img = result.get("overlay_image")
                if img and img.get("image_path"):
                    _OVERLAY_REF.set_data(img, 30)
                    log("TOOL", "백테스트 차트 팝업 투사")
                elif result.get("overlay_table"):
                    _OVERLAY_REF.set_data(result["overlay_table"], 30)
                    log("TOOL", "백테스트 결과표 투사")
            except Exception as e:
                log("WARN", f"오버레이 투사 실패: {e}")
        return result
    if name in PRICE_ALERT_TOOL_NAMES:
        return execute_price_alert_tool(name, args)
    if name in WHALE_TOOL_NAMES:
        result = execute_whale_tool(name, args)
        if result.get("success") and result.get("overlay_table") and _OVERLAY_REF:
            try:
                _OVERLAY_REF.set_data(result["overlay_table"], 25)
                log("TOOL", "고래 추적 리포트 웹캠 오버레이 투사")
            except Exception as e:
                log("WARN", f"오버레이 투사 실패: {e}")
        return result
    if name in SOCIAL_TOOL_NAMES:
        result = execute_social_tool(name, args)
        if result.get("success") and result.get("overlay_table") and _OVERLAY_REF:
            try:
                _OVERLAY_REF.set_data(result["overlay_table"], 25, alert=True)
                log("TOOL", "소셜 스파이크 분석표 웹캠 오버레이 투사")
            except Exception as e:
                log("WARN", f"오버레이 투사 실패: {e}")
        return result
    if name in FORECAST_TOOL_NAMES:
        result = execute_forecast_tool(name, args)
        if result.get("success") and result.get("overlay_table") and _OVERLAY_REF:
            try:
                alert = (result.get("direction") or "").upper() in ("UP", "DOWN")
                _OVERLAY_REF.set_data(result["overlay_table"], 28, alert=alert)
                log("TOOL", "내일 주가 전망표 웹캠 오버레이 투사")
            except Exception as e:
                log("WARN", f"오버레이 투사 실패: {e}")
        return result
    if name in IR_TOOL_NAMES:
        return execute_ir_tool(name, args)
    if name in MEDIA_TOOL_NAMES:
        result = execute_media_tool(name, args)
        if result.get("success") and result.get("overlay_table") and _OVERLAY_REF:
            try:
                _OVERLAY_REF.set_data(result["overlay_table"], 22, alert=False)
                log("TOOL", "미디어 파이프라인 상태표 웹캠 오버레이 투사")
            except Exception as e:
                log("WARN", f"오버레이 투사 실패: {e}")
        return result
    if name in STOCK_MONITOR_TOOL_NAMES:
        result = execute_stock_monitor_tool(name, args)
        if result.get("success") and result.get("overlay_table") and _OVERLAY_REF:
            try:
                _OVERLAY_REF.set_data(result["overlay_table"], 20)
                log("TOOL", "주가 알림 규칙표 웹캠 오버레이 투사")
            except Exception as e:
                log("WARN", f"오버레이 투사 실패: {e}")
        return result
    if name in CAMPUS_TOOL_NAMES:
        result = execute_campus_tool(name, args)
        if result.get("success") and result.get("overlay_table") and _OVERLAY_REF:
            try:
                _OVERLAY_REF.set_data(result["overlay_table"], 22)
                log("TOOL", "캠퍼스 학사 정보표 웹캠 오버레이 투사")
            except Exception as e:
                log("WARN", f"오버레이 투사 실패: {e}")
        return result
    if name in SUPPLY_CHAIN_TOOL_NAMES:
        result = execute_supply_chain_tool(name, args)
        if result.get("success") and result.get("overlay_table") and _OVERLAY_REF:
            try:
                _OVERLAY_REF.set_data(result["overlay_table"], 22)
                kind = f"Multi-Shock 시뮬레이션({result.get('shock_type','?')})" if result.get("shock_type") else "밸류체인 분석"
                log("TOOL", f"{kind} 결과 HUD 오버레이 투사")
            except Exception as e:
                log("WARN", f"오버레이 투사 실패: {e}")
        return result
    if name in OPENCLAW_TOOL_NAMES:
        return execute_openclaw_tool(name, args)
    if name in CROSSVAL_TOOL_NAMES:
        return execute_crossval_tool(name, args)
    if name in BACKGROUND_TOOL_NAMES:
        return execute_background_tool(name, args)
    return {"success":False,"message":f"Unknown tool: {name}"}


def execute_any_tool(name, args):
    """그 외 작업은 백엔드 → 맥 CPU → 오프라인 순으로 되돌린다."""
    from runtime_backtrack import call_tool_backend, guard_tool

    def _backend():
        safe, reason, clean = sanitize_tool_args(name, args)
        if not safe:
            return {"success": False, "message": f"보안 검증 실패: {reason}"}
        d_ok, d_reason = guard_destructive_tool(name, clean)
        if not d_ok:
            return {"success": False, "message": f"보안 차단: {d_reason}"}
        return call_tool_backend(name, clean)

    return guard_tool(name, args, lambda: _dispatch_tool(name, args), log_fn=log, backend=_backend)


JARVIS_SYSTEM_PROMPT = """You are J.A.R.V.I.S., a personal AI assistant. All replies MUST be spoken aloud in natural, calm, articulate English.
""" + USER_NAME_RULES + VOICE_STYLE_INSTRUCTIONS + """
★ ON-DEVICE AGENT (local LLM + function calling):
- Understand both English and Korean user inputs, and ALWAYS reply in natural spoken English. Multi-step requests need multiple tool calls in order.
  Example: "Open that document I was looking at earlier and turn the music down" → open_file(recent #1 path) then set_volume(direction="down").
- Deictic references: "that document / the file I just opened" → [RECENT FILES] context #1 path with open_file.
- Volume/music: "turn down the music / volume up / mute" → set_volume. "pause / resume music" → media_play_pause.
- Timers: "5 minute timer / remind me in 10 minutes" → set_timer(minutes=...).
- Apps: "open Safari / launch Chrome" → open_app. Never guess shell commands — always use tools.
- When a tool returns message/briefing (even if in Korean), translate/summarize it into natural spoken English (1~3 sentences).

★ ECOSYSTEM (differentiation — Mac + mobile + campus):
- Stock opinion: "How is Naver?", "Analyze Samsung Electronics", "Check NVDA" → analyze_stock_investment or analyze_stock_multi_source (local pipeline may answer first).
- Calendar: "What's on my schedule tomorrow?" → get_events(date="tomorrow"). Mac Calendar.app fallback when Google OAuth absent.
- Reminders: "What reminders do I have?" → macOS Reminders via get_events path or list reminders.
- SBU campus: "Where is my next class?" → get_next_class_location. Campus Quest schedule: data/campus_schedule.json.
- Mobile (ZFoldMacBridge): Fold notifications arrive via jarvis_link — read aloud; voice replies processed as user commands.

TOOLS:
1. WEB: web_search, news_search
2. FILES: search_files, list_folder, organize_folder, find_recent_files, move_files, open_file, open_app, get_disk_usage, find_large_files
3. MAC CONTROL: set_volume(level|direction), get_volume, set_timer(minutes, label), media_play_pause — volume, timers, media (macOS offline)
4. SCREEN: display_table, compare_stocks_trend — show tables/data/comparison charts on webcam screen
4. FACE: register_face, remove_face, list_faces
5. LOCATION: get_current_location — GPS/IP auto location + local time. "Where am I?" → get_current_location.
6. SYSTEM: get_system_status, get_cpu_usage, get_memory_usage
7. CALENDAR: get_events, create_event, delete_event, get_upcoming
8. MONITOR: get_monitor_status, check_github_now, check_playstore_now
9. STOCK QUANT: analyze_stock_investment — 14 technical indicators + fundamentals + investment score + position sizing, get_macro_market_status — VIX/US10Y/DXY/USDKRW market risk, get_comprehensive_matrix_score — Quant + Whale + Social 3-layer matrix analysis
10. STOCK NEWS: analyze_stock_news — AI noise filtering + sentiment score (-10~+10), get_news_monitor_status
11. PORTFOLIO: report_portfolio_status — virtual account live asset/return report, record_stock_trade, deposit_portfolio_cash
12. BACKTEST: run_strategy_backtest — validate trading strategy over historical N-year data (cumulative return/benchmark/MDD/trades)
13. PRICE ALERT: get_price_alert_status, check_watchlist_now, set_price_alert, list_price_alerts, cancel_price_alert
14. WHALE TRACKER: fetch_whale_trades, mirror_whale_portfolio, get_whale_tracker_status — politician/institutional trades & 13F portfolio tracking
15. SOCIAL DETECTOR: analyze_social_trends, get_social_detector_status — Reddit meme stock spike / volume spike / risk analysis
15b. NEXT-DAY FORECAST: analyze_tomorrow_stock — CNN news + Twitter/X sentiment next-trading-day forecast
16. IR INTERPRETER: start_ir_interpreter, stop_ir_interpreter, get_ir_interpreter_status — live IR audio Whisper interpretation / 5-min summary / HUD board
17. MEDIA PIPELINE: submit_media_url(url, title?), get_media_pipeline_status, cancel_media_job(job_id)
18. STOCK MONITOR: add_stock_alert(ticker, condition_type, threshold), remove_stock_alert(alert_id), list_stock_alerts(), get_stock_monitor_status()
19. CAMPUS AGENT: check_upcoming_academic_schedule(days), refresh_campus_agent_status()
20. SUPPLY CHAIN: analyze_supply_chain_impact(body_text, title, source), simulate_supply_chain_scenario(scenario_text), simulate_structural_shock(shock_kind, origin_ticker, intensity?, duration_weeks?, target_country?)
21. MULTI-SOURCE CROSS-VALIDATION: analyze_stock_multi_source(query) — parallel multi-provider consensus for price, volume, PER, PBR, market cap

★ CAMPUS AGENT: For academic schedule, registration, housing, or tuition deadlines → check_upcoming_academic_schedule(days=60).
  For campus monitor status → refresh_campus_agent_status.
  Overlay tables are projected automatically. Deliver the returned message in 1~2 concise English sentences.

★ STOCK MONITOR: "Alert me if NVIDIA drops more than 5%" → add_stock_alert(ticker="NVDA", condition_type="change_pct_down", threshold=5).
  "Show my stock alerts" → list_stock_alerts. "Remove NVDA alert" → remove_stock_alert(alert_id=...).
  condition_type: change_pct_down, change_pct_up, price_below, price_above.

★ PRICE ALERT: "Check my watchlist now", "Any buy signals?" → check_watchlist_now. "Price alert status" → get_price_alert_status.
★ TARGET PRICE ALERT: "Alert me when Tesla hits 300 dollars" → set_price_alert(ticker=..., price=300, direction=above/below).
  Pass the ticker/name as spoken. If ambiguous, the system will prompt the user with 5 candidates by voice — do not ask follow-up questions yourself.

★ IR INTERPRETER: "Start IR live interpretation for this URL" → start_ir_interpreter(url, company).
  "Stop IR interpreter" → stop_ir_interpreter. "IR interpreter status" → get_ir_interpreter_status.

★ MEDIA PIPELINE: "Capture and preprocess audio from this YouTube/stream URL" → submit_media_url(url, title).

★ SOCIAL DETECTOR: "Any meme stock spikes on Reddit?", "Analyze social trends" → analyze_social_trends.

★ NEXT-DAY FORECAST (CNN + Twitter): "How will NVIDIA trade tomorrow?", "Tomorrow's stock forecast for Tesla" → analyze_tomorrow_stock(ticker=...).
  Summarize the briefing in 1~2 English sentences and briefly note that it is a sentiment scenario, not financial advice.

★ WHALE TRACKER: "Mirror Warren Buffett's portfolio" → mirror_whale_portfolio(person="buffett", apply=true).
  "Show Nancy Pelosi's recent trades" → fetch_whale_trades.

★ STOCK COMPARISON CHART: "Compare Samsung Electronics and SK Hynix trends", "Compare AAPL and NVDA VWMA" → compare_stocks_trend.

★ BACKTEST: "Backtest buying when RSI is below 30 and selling at 10% profit" → run_strategy_backtest.

★ PORTFOLIO: "Report my portfolio status", "How are my returns?" → report_portfolio_status.
  "I bought 10 shares of Apple at 175 dollars" → record_stock_trade(ticker, shares, price). "Deposit 5 million won" → deposit_portfolio_cash.

★ STOCK NEWS: "Analyze NVIDIA news", "Any news on Tesla?" → analyze_stock_news.

★ COMPREHENSIVE MATRIX: "JARVIS, run a comprehensive matrix analysis on [ticker]" → get_comprehensive_matrix_score.

★ SUPPLY CHAIN: For real news/filings supply chain impact → analyze_supply_chain_impact.
  For What-If scenarios → simulate_supply_chain_scenario or simulate_structural_shock.

★ MULTI-SOURCE CROSS-VALIDATION: General stock lookup ("Check Samsung Electronics", "Tell me about Tesla", "Look up NVIDIA") → analyze_stock_multi_source(query=...).
  Deliver the returned briefing in natural English (1~3 sentences). Do not call the same tool twice.
★ STOCK ANALYSIS: For investment/stock analysis questions → analyze_stock_investment(ticker=..., available_cash_krw=0).
  The overlay_table is projected automatically (do not call display_table separately). Summarize the score, grade, and key buy/sell indicators in 2~3 English sentences.

★ MACRO MARKET: "How is the macro market right now?", "Check the fear index / macro risk" → get_macro_market_status.

★ CALENDAR: "Tomorrow's schedule", "What classes do I have?" → get_events. "Schedule a meeting at 3 PM" → create_event. "Upcoming events" → get_upcoming.
  After getting events, call display_table type="table" with headers=["Time","Event","Location"] and rows.
★ SYSTEM: get_system_status → display_table type="info".
★ STOCKS: web_search → display_table → verbal summary.
★ ALWAYS display_table after getting data. ALWAYS respond in natural spoken English."""

def _debug_enabled() -> bool:
    return os.getenv("JARVIS_DEBUG", "0").strip().lower() in ("1", "true", "yes", "on")


def log(level, msg):
    ts = datetime.now().strftime("%H:%M:%S")
    icons = {"INFO":"◈","OK":"✓","WARN":"⚠","ERR":"✗",
             "JARVIS":"◆","TOOL":"⚙","INTR":"⏸","FACE":"👤"}
    print(f"[{ts}] {icons.get(level,'·')} {msg}")


def _debug_exc() -> None:
    if not _debug_enabled():
        return
    import traceback
    traceback.print_exc()

class InterruptiblePlayer:
    def __init__(self):
        self._process=None; self._playing=False; self._lock=threading.Lock()
    @property
    def is_playing(self): return self._playing

    @staticmethod
    def _wait_process(proc, timeout=PLAY_TIMEOUT):
        """재생 프로세스 대기 — 무한 wait()로 CoreAudio/afplay가 UI를 붙잡는 것 방지."""
        if not proc:
            return
        deadline = time.time() + max(5.0, float(timeout))
        while proc.poll() is None:
            if time.time() >= deadline:
                try:
                    proc.terminate()
                    proc.wait(timeout=1)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                break
            time.sleep(0.05)

    def play(self, fp):
        self._playing=True
        proc = None
        try:
            if sys.platform=="darwin":
                proc=subprocess.Popen(["afplay",fp],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            elif sys.platform=="win32":
                for p in ["ffplay", "ffplay.exe"]:
                    if shutil.which(p):
                        proc=subprocess.Popen(
                            [p, "-nodisp", "-autoexit", "-loglevel", "quiet", fp],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        break
            elif sys.platform.startswith("linux"):
                for p in ["mpg123","aplay","ffplay"]:
                    if shutil.which(p):
                        a=[p]
                        if p=="ffplay": a+=["-nodisp","-autoexit"]
                        a.append(fp)
                        proc=subprocess.Popen(a,
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                        break
            with self._lock:
                self._process = proc
            if proc:
                self._wait_process(proc)
        except Exception:
            pass
        finally:
            with self._lock:
                self._process = None
            self._playing=False

    def stop(self):
        with self._lock:
            self._playing=False
            proc=self._process
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        with self._lock:
            self._process=None

class VoiceActivityDetector:
    def __init__(self):
        self.threshold=VAD_THRESHOLD; self.consecutive=VAD_CONSECUTIVE
        self._stream=None; self.last_rms=0
    def start(self, pa):
        try: self._stream=pa.open(format=RECORD_FORMAT,channels=CHANNELS,
                                  rate=SAMPLE_RATE,input=True,frames_per_buffer=CHUNK)
        except: self._stream=None
    def check_voice(self):
        if not self._stream: return False
        loud=0
        try:
            for _ in range(self.consecutive+2):
                d=self._stream.read(CHUNK,exception_on_overflow=False)
                c=len(d)//2
                if c==0: continue
                s=struct.unpack(f"{c}h",d)
                self.last_rms=int((sum(x*x for x in s)/c)**0.5)
                if self.last_rms>self.threshold: loud+=1
                if loud>=self.consecutive: return True
        except: pass
        return False
    def stop(self):
        if self._stream:
            try: self._stream.stop_stream(); self._stream.close()
            except: pass
            self._stream=None

class SmartRecorder:
    """
    스마트 녹음기
    - 정적 감지(기본 1.2초, JARVIS_SILENCE_TIMEOUT) → 자동 종료
    - 음성 프로필로 주인 목소리 필터링 (소음/타인 무시)
    - 자동 종료 시 auto_stopped 플래그 + 종료 시각(auto_stopped_at) 설정
    """
    SILENCE_TIMEOUT = float(os.getenv("JARVIS_SILENCE_TIMEOUT", "1.2"))  # 정적 N초 → 자동 종료
    NOISE_THRESHOLD = int(os.getenv("JARVIS_NOISE_THRESHOLD", "500"))    # 이 이하는 무음
    VOICE_CHECK_INTERVAL = 8  # N청크마다 음성 검증

    def __init__(self, voice_profile=None):
        self.pa = pyaudio.PyAudio()
        self._frames = []
        self._recording = False
        self._thread = None
        self.last_rms = 0
        self.auto_stopped = False       # 정적으로 자동 종료됨
        self.auto_stopped_at = None     # 자동 종료된 시각 (정적 감지 시점)
        self._voice_profile = voice_profile
        self._noise_rejected = 0        # 소음 거부 횟수

    def start(self):
        if self._recording: return
        self._frames = []
        self._recording = True
        self.auto_stopped = False
        self.auto_stopped_at = None
        self._noise_rejected = 0
        self._thread = threading.Thread(target=self._smart_loop, daemon=True)
        self._thread.start()
        log("INFO", "🎙 녹음 시작 (3초 정적 → 자동 종료)")

    def stop(self, *, save: bool = True, timeout: float = 0.4):
        # 자동 종료(정적 감지)로 이미 _recording=False가 된 경우에도
        # 녹음된 프레임이 있으면 저장한다 (단, 앱 종료 시 save=False 로 즉시 중단).
        was_active = self._recording
        self._recording = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        if not save:
            return None
        if not was_active and not self._frames:
            return None
        return self._save()

    def _smart_loop(self):
        # macOS CoreAudio: 웨이크 스트림 해제 직후 장치 전환 안정화
        if sys.platform == "darwin":
            time.sleep(0.08)
        stream = self.pa.open(
            format=RECORD_FORMAT, channels=CHANNELS,
            rate=SAMPLE_RATE, input=True, frames_per_buffer=CHUNK)

        last_voice_time = time.time()   # 마지막으로 음성이 감지된 시각
        chunk_count = 0
        voice_buffer = []               # 음성 검증용 임시 버퍼

        while self._recording:
            data = stream.read(CHUNK, exception_on_overflow=False)
            chunk_count += 1

            # RMS 계산
            c = len(data) // 2
            if c > 0:
                shorts = struct.unpack(f"{c}h", data)
                self.last_rms = int((sum(x * x for x in shorts) / c) ** 0.5)
            else:
                self.last_rms = 0

            # ── 무음 판단 ──
            if self.last_rms < self.NOISE_THRESHOLD:
                # 무음 → 정적 타이머 체크
                elapsed = time.time() - last_voice_time
                if elapsed >= self.SILENCE_TIMEOUT:
                    log("INFO", f"⏹ {self.SILENCE_TIMEOUT}초 정적 → 녹음 중지")
                    self.auto_stopped = True
                    self.auto_stopped_at = time.time()
                    self._recording = False
                    break
                continue  # 무음 프레임은 저장하지 않음

            # ── 소리가 있는 경우 ──
            # 음성 프로필로 주인 목소리인지 확인
            if self._voice_profile and self._voice_profile.trained:
                voice_buffer.append(data)

                # N청크 모이면 음성 검증
                if len(voice_buffer) >= self.VOICE_CHECK_INTERVAL:
                    combined = b"".join(voice_buffer)
                    is_owner, confidence = self._voice_profile.match(combined)
                    voice_buffer = []

                    if not is_owner:
                        # 주인 목소리가 아님 → 소음으로 간주, 무시
                        self._noise_rejected += 1
                        if self._noise_rejected <= 3:  # 처음 3번만 로그
                            log("INFO", f"🔇 소음 무시 (유사도={confidence})")
                        continue  # 이 데이터를 저장하지 않음

            # 주인 목소리 확인됨 (또는 프로필 미학습) → 저장
            self._frames.append(data)
            last_voice_time = time.time()

            # 최대 녹음 시간 제한
            total_sec = len(self._frames) * CHUNK / SAMPLE_RATE
            if total_sec >= MAX_RECORD_SECONDS:
                log("INFO", f"⏹ 최대 녹음 시간({MAX_RECORD_SECONDS}초) 도달")
                self.auto_stopped = True
                self.auto_stopped_at = time.time()
                self._recording = False
                break

        try:
            stream.stop_stream()
            stream.close()
        except Exception:
            pass
        self._recording = False

        if self._noise_rejected > 0:
            log("INFO", f"총 {self._noise_rejected}회 소음 필터링됨")

    def _save(self):
        if not self._frames: return None
        p = str(AUDIO_DIR / f"input_{int(time.time())}.wav")
        with wave.open(p, "wb") as w:
            w.setnchannels(CHANNELS)
            w.setsampwidth(self.pa.get_sample_size(RECORD_FORMAT))
            w.setframerate(SAMPLE_RATE)
            w.writeframes(b"".join(self._frames))
        dur = len(self._frames) * CHUNK / SAMPLE_RATE
        log("OK", f"WAV 저장: {p} ({dur:.1f}초)")
        return p

    def __del__(self):
        # macOS CoreAudio에서 데몬 스레드가 스트림을 닫는 도중 Pa_Terminate()를 호출하면
        # HAL 뮤텍스 데드락(응답 없음)이 발생할 수 있으므로 종료 시점 GC에서는 생략한다.
        if sys.platform == "darwin":
            return
        try: self.pa.terminate()
        except: pass

def transcribe_audio(wav, client=None):
    """STT — stt_backend selects local/api/subprocess mode and language (default en)."""
    from stt_backend import transcribe_audio as _stt
    return _stt(wav, client, role="main")

def _gaze_context_suffix():
    """시선 컨텍스트(current_focused_ticker)가 신선하면 System Prompt 에 주입할 문구 생성."""
    try:
        ctx = get_gaze_context()
        ticker = ctx.get_focused_ticker()
        if not ticker:
            return ""
        label = ctx.get_focused_label() or ticker
        return (
            f"\n\n[Context] User is currently looking at: {ticker} ({label}).\n"
            f"If the user refers to 'this', 'this stock', 'how is it?', or 'matrix score' without naming a ticker, "
            f"interpret it as {ticker} and invoke the appropriate stock tool "
            f"(get_comprehensive_matrix_score / get_stock_quote / analyze_stock_investment) with ticker=\"{ticker}\"."
        )
    except Exception:
        return ""


def _compose_system_prompt(extra=""):
    """JARVIS_SYSTEM_PROMPT + 실시간 선택 언어 지시 + 시선·미디어·파일·메모리 컨텍스트 + 추가 지시(extra)를 합성."""
    mem = ""
    try:
        from memory_store import get_memory_context_suffix
        mem = get_memory_context_suffix()
    except ImportError:
        pass
    lang_directive = build_language_directive()
    return (
        JARVIS_SYSTEM_PROMPT
        + "\n"
        + lang_directive
        + _gaze_context_suffix()
        + get_media_context_suffix()
        + get_file_context_suffix()
        + mem
        + (extra or "")
    )


def _llm_brain_enabled() -> bool:
    """키워드 우회 없이 로컬 LLM function calling을 두뇌로 사용."""
    v = os.getenv("JARVIS_LLM_BRAIN", "").strip().lower()
    if v in ("0", "false", "off", "no"):
        return False
    if v in ("1", "true", "on", "yes"):
        return True
    # 기본: Ollama/무료 모드면 LLM 두뇌 ON (키워드 주식 우회 비활성)
    return no_gemini_mode() or LLM_PROVIDER == "ollama"


_DEICTIC_STOCK_RE = re.compile(
    r"아까|그\s*종목|방금\s*(그|말한)|전에\s*(말한|조회|브리핑)",
    re.I,
)


def _extract_ticker_hint(text: str, history: list | None = None) -> str:
    """음성/텍스트에서 종목 티커 힌트 추출 — '아까 그 종목'은 최신 캐시 스냅샷."""
    raw = (text or "").strip()
    if not raw:
        return ""

    if _DEICTIC_STOCK_RE.search(raw):
        try:
            from cv_cache_manager import get_latest_snapshot, cv_cache_enabled
            if cv_cache_enabled():
                latest = get_latest_snapshot()
                if latest:
                    return (latest.get("ticker") or latest.get("query") or "").strip()
        except ImportError:
            pass

    cleaned = re.sub(r"^(자비스|jarvis)[,\s]*", "", raw, flags=re.I).strip()
    cleaned = re.sub(
        r"(브리핑|알려\s*줘|알아\s*봐|조회|정보|뉴스|어때|상황|분석)[^\w가-힣]*$",
        "",
        cleaned,
    ).strip(" ,.")

    try:
        from stock_quant import resolve_alert_query, _KR_TICKER_MAP, _KR_STT_ALIASES
        for q in (cleaned, raw):
            if not q:
                continue
            r = resolve_alert_query(q)
            if r.get("confident") and r.get("ticker"):
                return str(r["ticker"])

        merged = {**_KR_STT_ALIASES, **_KR_TICKER_MAP}
        for alias, ticker in sorted(merged.items(), key=lambda x: -len(x[0] or "")):
            if alias and len(alias) >= 2 and alias in cleaned:
                return str(ticker)

        for m in re.finditer(r"\b[A-Z]{1,5}\b", cleaned.upper()):
            return m.group(0)
    except ImportError:
        pass

    if history:
        for msg in reversed(history):
            if msg.get("role") != "user":
                continue
            prev = (msg.get("content") or "").strip()
            if not prev or prev == raw:
                continue
            hint = _extract_ticker_hint(prev, history=None)
            if hint:
                return hint
    return ""


def _apply_cv_cache_to_user_text(text: str, history: list | None = None) -> str:
    """CV 샌드박스 compact_digest 를 유저 메시지에 최소 토큰으로 접두."""
    try:
        from cv_cache_manager import cv_cache_enabled
        if not cv_cache_enabled():
            return text
    except ImportError:
        return text

    hint = _extract_ticker_hint(text, history)
    if not hint:
        return text

    enriched = inject_market_snapshot_into_user_message(text, hint)
    if enriched != text:
        log("INFO", f"CV 캐시 digest 주입 → {hint}")
    return enriched


def ask_jarvis_openai(text, client, history, pipeline=None):
    msgs=[{"role":"system","content":_compose_system_prompt()}]
    msgs.extend(get_context_window(history)); msgs.append({"role":"user","content":text})

    # 1차: 도구 호출 판단 (비스트리밍)
    resp=client.chat.completions.create(model="gpt-4o",messages=msgs,
        tools=ALL_TOOLS,tool_choice="auto",temperature=0.4,max_tokens=250)
    msg=resp.choices[0].message

    # 도구 호출 처리
    for _ in range(5):
        if not msg.tool_calls: break
        msgs.append(msg)
        for tc in msg.tool_calls:
            tn=tc.function.name; ta=json.loads(tc.function.arguments)
            log("TOOL",f"{tn}({json.dumps(ta,ensure_ascii=False)[:80]})")
            r=execute_any_tool(tn,ta); msgs.append({"role":"tool","tool_call_id":tc.id,
                "content":json.dumps(r,ensure_ascii=False)})
        resp=client.chat.completions.create(model="gpt-4o",messages=msgs,
            tools=ALL_TOOLS,tool_choice="auto",temperature=0.4,max_tokens=250)
        msg=resp.choices[0].message

    # 도구 호출 완료 후 최종 답변 → 스트리밍으로 재요청
    if pipeline and msg.content:
        # 이미 답변이 있으면 바로 피드
        pipeline.start()
        pipeline.feed(msg.content)
        pipeline.finish()
        return msg.content.strip()
    elif pipeline:
        # 도구 결과로 스트리밍 답변 생성
        pipeline.start()
        stream=client.chat.completions.create(model="gpt-4o",messages=msgs,
            temperature=0.4,max_tokens=250,stream=True)
        for chunk in stream:
            delta=chunk.choices[0].delta
            if delta.content:
                pipeline.feed(delta.content)
        pipeline.finish()
        return pipeline.get_full_text()

    return polish_for_voice(msg.content.strip()) if msg.content else "All done, Sir."


def _gemini_func_calls(response):
    """응답에서 모든 function_call part 를 안전하게 추출 (parts[0] 가정 제거)."""
    try:
        parts = response.candidates[0].content.parts
    except (AttributeError, IndexError, TypeError):
        return []
    calls = []
    for p in parts:
        fc = getattr(p, "function_call", None)
        if fc and getattr(fc, "name", ""):
            calls.append(fc)
    return calls


def _gemini_text(response):
    """응답의 모든 text part 를 안전하게 이어붙여 반환.
    function_call part 만 있으면 .text 접근이 예외를 내므로 part 별로 직접 추출한다."""
    try:
        parts = response.candidates[0].content.parts
    except (AttributeError, IndexError, TypeError):
        return ""
    out = []
    for p in parts:
        t = getattr(p, "text", "")
        if t:
            out.append(t)
    return "".join(out).strip()


def ask_jarvis_gemini(text, history, pipeline=None):
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)

    cache_mgr = get_cache_manager()
    cache_mgr.configure(api_key=GEMINI_API_KEY, flash_model=GEMINI_FLASH_MODEL)
    model, cache_hit = cache_mgr.get_flash_model(
        system_instruction=JARVIS_SYSTEM_PROMPT,
        tools_list=ALL_TOOLS,
    )
    if cache_hit:
        log("OK", "Gemini Context Cache 활성 — system+tools+소스 재사용")
    else:
        log("INFO", "Gemini 비캐시 모드 — 전체 system_instruction 전송")

    user_text = text
    gaze = _gaze_context_suffix()
    if gaze:
        user_text = gaze.strip() + "\n\n" + text

    win = get_context_window(history)
    gh=[{"role":"user" if m["role"]=="user" else "model","parts":[m["content"]]}
        for m in win]
    chat=model.start_chat(history=gh)

    MAX_TOOL_ITERS = 8
    response=chat.send_message(user_text)
    log_gemini_usage(response, label="Gemini[1]")
    final_text=""
    for i in range(MAX_TOOL_ITERS):
        calls=_gemini_func_calls(response)
        if not calls:
            final_text=_gemini_text(response)   # 텍스트만 있는 응답 → 종료
            break
        fn_responses=[]
        for fc in calls:
            tn=fc.name; ta=dict(fc.args) if fc.args else {}
            for k,v in ta.items():
                if isinstance(v,str):
                    if v.lower() in ("true","false"): ta[k]=v.lower()=="true"
                    elif v.isdigit(): ta[k]=int(v)
            log("TOOL",f"{tn}({json.dumps(ta,ensure_ascii=False)[:80]})")
            r=execute_any_tool(tn,ta)
            fn_responses.append(genai.protos.Part(
                function_response=genai.protos.FunctionResponse(name=tn,response=r)))
        response=chat.send_message(genai.protos.Content(parts=fn_responses))
        log_gemini_usage(response, label=f"Gemini[tool-{i+1}]")
    else:
        final_text=_gemini_text(response)
        if not final_text:
            try:
                _lang_name = get_lang_meta()["llm_language"]
                response=chat.send_message(
                    f"Based on the tool results above, please reply in 2 concise {_lang_name} sentences without calling any more tools.")
                log_gemini_usage(response, label="Gemini[summary]")
                final_text=_gemini_text(response)
            except Exception as e:
                log("WARN", f"Gemini summary retry failed: {e}")
                final_text=""

    if not final_text:
        final_text = "I apologize, Sir. I could not generate a response."
    if pipeline and final_text:
        final_text = polish_for_voice(final_text)
        pipeline.start()
        # Gemini는 이미 전체 텍스트가 있으므로 문장 단위로 분할하여 피드
        for char in final_text:
            pipeline.feed(char)
        pipeline.finish()

    return final_text


# ── Ollama 오프라인 폴백 ──
_ollama_available: bool|None = None  # None=미확인, True/False=캐시

def _check_ollama() -> bool:
    """Ollama 서버 접속 가능 여부 (1초 타임아웃, 결과 캐시)."""
    global _ollama_available
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=2.5)
        _ollama_available = r.status_code == 200
    except Exception:
        _ollama_available = False
    return _ollama_available

def _try_ecosystem_response(text: str, pipeline=None) -> str | None:
    """생태계 fast-path — 주식·캘린더·캠퍼스·미리알림."""
    try:
        from ecosystem_router import try_ecosystem_response
        result = try_ecosystem_response(text)
        if not result or not result.get("success"):
            return None
        if result.get("overlay_table") and _OVERLAY_REF:
            try:
                _OVERLAY_REF.set_data(result["overlay_table"], 20)
                log("TOOL", f"생태계 HUD 투사 ({result.get('domain')})")
            except Exception:
                pass
        briefing = result.get("briefing") or result.get("message") or ""
        if pipeline and briefing:
            pipeline.start()
            for ch in briefing:
                pipeline.feed(ch)
            pipeline.finish()
        log("OK", f"🔗 생태계 라우터 — domain={result.get('domain')}")
        return briefing
    except Exception as e:
        log("WARN", f"생태계 라우터: {e}")
        return None


def _try_mac_local_stock(text: str, pipeline=None) -> str | None:
    """Mac 로컬 주식 분석 — Gemini 0, Ollama multi-agent + verify_stock."""
    if _llm_brain_enabled():
        return None
    try:
        from local_stock_router import try_local_stock_response
        result = try_local_stock_response(text)
        if not result or not result.get("success"):
            return None
        if result.get("overlay_table") and _OVERLAY_REF:
            try:
                _OVERLAY_REF.set_data(result["overlay_table"], 20)
                log("TOOL", "로컬 주식 분석표 HUD 투사 (Mac AI)")
            except Exception:
                pass
        briefing = result.get("briefing") or ""
        if pipeline and briefing:
            pipeline.start()
            for ch in briefing:
                pipeline.feed(ch)
            pipeline.finish()
        log("OK", f"🍎 Mac 로컬 분석 — ollama={result.get('ollama_used')} gemini=0")
        return briefing
    except Exception as e:
        log("WARN", f"로컬 주식 라우터: {e}")
        return None


def ask_jarvis_ollama(text: str, history: list, pipeline=None) -> str:
    """Mac 로컬 Ollama(Metal GPU) — 도구 호출 포함, Gemini 0."""
    eco = _try_ecosystem_response(text, pipeline)
    if eco:
        return eco
    local = _try_mac_local_stock(text, pipeline)
    if local:
        return local
    if not model_supports_tools():
        log("WARN", f"{OLLAMA_MODEL} — tools 미지원, 대화 모드")
        return ask_jarvis_ollama_chat_only(text, history, pipeline)
    return ollama_chat_with_tools(
        text,
        history,
        system_prompt=_compose_system_prompt(),
        tools=ALL_TOOLS,
        execute_tool=execute_any_tool,
        pipeline=pipeline,
        get_context_window=get_context_window,
        log_fn=log,
    )


def ask_jarvis_ollama_chat_only(text: str, history: list, pipeline=None) -> str:
    """도구 없이 대화만 (폴백)."""
    msgs = [{"role":"system","content":_compose_system_prompt(
             "\n\n(Offline mode: tool calling is unavailable. Reply in concise English.)")}]
    for m in get_context_window(history):
        msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role":"user","content":text})

    ollama_options = {"num_ctx": OLLAMA_NUM_CTX}
    if OLLAMA_NUM_GPU != 0:
        ollama_options["num_gpu"] = OLLAMA_NUM_GPU

    try:
        r = requests.post(f"{OLLAMA_URL}/api/chat",
                          json={"model":OLLAMA_MODEL, "messages":msgs, "stream":True,
                                "options": ollama_options},
                          stream=True, timeout=60)
        r.raise_for_status()
    except Exception as e:
        log("ERR", f"Ollama call failed: {e}")
        local = offline_reply(text)
        if local:
            return local
        return "Could not connect to the local AI engine. Time, system status, files, calendar, and volume controls remain available offline."

    if pipeline:
        pipeline.start()

    full = []
    for line in r.iter_lines():
        if not line:
            continue
        try:
            chunk = json.loads(line)
            token = chunk.get("message", {}).get("content", "")
            if token:
                full.append(token)
                if pipeline:
                    for ch in token:
                        pipeline.feed(ch)
        except json.JSONDecodeError:
            continue

    if pipeline:
        pipeline.finish()

    return "".join(full).strip() or "I could not generate a response."


def _injection_refusal(text, pipeline=None):
    """[Inbound 가드레일] LLM 질의 직전 입력 검증 — 프롬프트 인젝션/탈옥/가스라이팅
    시도 시 LLM·도구 호출 없이 즉시 정중히 거부. (validate_llm_input이 감사 체인 기록)"""
    ok, reason = validate_llm_input(text)
    if ok:
        return None
    log("WARN", f"🛡 Input guardrail blocked: {reason}")
    write_audit_log("INPUT_GUARD", f"reason={reason} | input={text[:120]}")
    refusal = f"I'm sorry, {USER_DISPLAY_NAME}. That request was blocked by security policy."
    if pipeline:
        pipeline.start()
        for ch in refusal:
            pipeline.feed(ch)
        pipeline.finish()
    return refusal


def ask_jarvis_streaming(text, oc, history, pipeline):
    """스트리밍 TTS 파이프라인과 연동된 LLM 호출 (Ollama/Gemini)"""
    log("INFO",f"LLM({LLM_PROVIDER}) streaming...")
    refusal = _injection_refusal(text, pipeline)
    if refusal is not None:
        return refusal
    text = _apply_cv_cache_to_user_text(text, history)
    # 생태계 fast-path (주식·캘린더·캠퍼스)
    eco = _try_ecosystem_response(text, pipeline)
    if eco:
        return eco
    # Mac 로컬 주식 분석 우선 (Gemini 0)
    if no_gemini_mode() or LLM_PROVIDER == "ollama":
        local = _try_mac_local_stock(text, pipeline)
        if local:
            return local
    if LLM_PROVIDER == "ollama" or (no_gemini_mode() and _check_ollama()):
        if not _check_ollama():
            return f"Local AI (Ollama) is not running. Please run 'ollama serve' in Terminal, {USER_DISPLAY_NAME}."
        try:
            if hybrid_routing_enabled() and LLM_PROVIDER != "ollama":
                a = ask_with_hybrid_routing(
                    text, history, pipeline,
                    gemini_fn=ask_jarvis_gemini,
                    ollama_fn=ask_jarvis_ollama,
                    check_ollama=_check_ollama,
                )
            else:
                a = ask_jarvis_ollama(text, history, pipeline)
        except Exception as e:
            log("WARN", f"Ollama failed: {e}")
            if no_gemini_mode():
                return f"I apologize, {USER_DISPLAY_NAME}. Local AI processing failed."
            if GEMINI_API_KEY and _check_ollama() is False:
                return "I apologize, Sir. The network connection is unavailable."
            a = ask_jarvis_gemini(text, history, pipeline) if GEMINI_API_KEY else "No Gemini API key configured."
    elif LLM_PROVIDER=="gemini":
        if not GEMINI_API_KEY and no_gemini_mode():
            return ask_jarvis_ollama(text, history, pipeline) if _check_ollama() else "Local AI required."
        if not GEMINI_API_KEY: return "No Gemini API key configured."
        try:
            if hybrid_routing_enabled():
                a = ask_with_hybrid_routing(
                    text, history, pipeline,
                    gemini_fn=ask_jarvis_gemini,
                    ollama_fn=ask_jarvis_ollama,
                    check_ollama=_check_ollama,
                )
            else:
                a=ask_jarvis_gemini(text,history,pipeline)
        except Exception as e:
            log("WARN",f"Gemini failed → Ollama fallback: {e}")
            if _check_ollama():
                a=ask_jarvis_ollama(text,history,pipeline)
            else:
                return "I apologize, Sir. The network connection is unavailable."
    else:
        a=ask_jarvis_openai(text,oc,history,pipeline)
    a = filter_llm_output(a)  # API 키 유출 방지
    log("JARVIS",f"Reply: {a[:50]}...")
    return a


def ask_jarvis(text, oc, history):
    """비스트리밍 (도구 전용 / 폴백) — Ollama/Gemini"""
    log("INFO",f"LLM({LLM_PROVIDER})...")
    refusal = _injection_refusal(text)
    if refusal is not None:
        return refusal
    text = _apply_cv_cache_to_user_text(text, history)
    if LLM_PROVIDER == "ollama" or (no_gemini_mode() and _check_ollama()):
        if not _check_ollama():
            return f"Local AI (Ollama) is not running, {USER_DISPLAY_NAME}."
        a = ask_jarvis_ollama(text, history)
    elif LLM_PROVIDER=="gemini":
        try:
            if hybrid_routing_enabled():
                a = ask_with_hybrid_routing(
                    text, history, None,
                    gemini_fn=lambda t, h, p: ask_jarvis_gemini(t, h, p),
                    ollama_fn=lambda t, h, p: ask_jarvis_ollama(t, h, p),
                    check_ollama=_check_ollama,
                )
            else:
                a=ask_jarvis_gemini(text,history)
        except Exception as e:
            log("WARN",f"Gemini failed → Ollama fallback: {e}")
            if _check_ollama():
                a=ask_jarvis_ollama(text,history)
            else:
                return "I apologize, Sir. The network connection is unavailable."
    else:
        a=ask_jarvis_openai(text,oc,history)
    a = filter_llm_output(a)  # API 키 유출 방지
    log("JARVIS",f"Reply: {a}"); return a


def synthesize_speech(text):
    """단일 TTS (비스트리밍 폴백용) — 무료: macOS say / 유료: Fish Audio"""
    if free_only_enabled() or not use_paid_tts():
        return synthesize_macos(text, audio_dir=AUDIO_DIR)
    if not FISH_API_KEY or not FISH_VOICE_ID:
        return synthesize_macos(text, audio_dir=AUDIO_DIR)
    r=net.post("https://api.fish.audio/v1/tts",
        headers={"Authorization":f"Bearer {FISH_API_KEY}","Content-Type":"application/json"},
        json={"text":text,"reference_id":FISH_VOICE_ID,"format":"mp3",
              "mp3_bitrate":192,"normalize":True,"latency":"normal"},timeout=(5,30))
    if r.status_code!=200: return None
    p=str(AUDIO_DIR/f"jarvis_{int(time.time())}.mp3")
    with open(p,"wb") as f: f.write(r.content)
    return p

JARVIS_VISION_PROMPT = """You are J.A.R.V.I.S., analyzing a financial chart held up to the webcam.

Analyze: candle trend, moving averages, support/resistance, and a stance (Buy / Hold / Sell).
Style: calm, natural, articulate spoken English in 3 to 5 sentences suitable for voice output.
Do not guess if the chart is unclear — simply ask the user to hold the chart steady in front of the camera."""


def analyze_chart_image(image_path):
    """차트 이미지 기술분석 — 무료: Ollama Vision / 유료: Gemini Vision."""
    if free_only_enabled() or no_gemini_mode():
        from ollama_vision import analyze_chart_with_ollama
        out = analyze_chart_with_ollama(
            image_path,
            system_prompt=JARVIS_VISION_PROMPT,
        )
        if out.get("success"):
            log("OK", f"🍎 Ollama Vision ({out.get('model')}) chart analysis complete")
            return out["text"]
        log("WARN", f"Ollama Vision: {out.get('message', '')}")
        msg = out.get("message") or "Local chart analysis failed."
        return polish_for_voice(with_user_address(msg) if not msg.startswith(USER_DISPLAY_NAME) else msg)
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    with open(image_path, "rb") as f:
        img_bytes = f.read()

    cache_mgr = get_cache_manager()
    cache_mgr.configure(api_key=GEMINI_API_KEY, vision_model=GEMINI_PRO_MODEL)
    model, cache_hit = cache_mgr.get_vision_model(system_instruction=JARVIS_VISION_PROMPT)
    if cache_hit:
        log("OK", "Gemini Vision Context Cache active")

    resp = model.generate_content([
        "Please analyze this chart and explain the technical setup concisely in English.",
        {"mime_type": "image/png", "data": img_bytes},
    ])
    log_gemini_usage(resp, label="Gemini[vision]")
    return (resp.text or "").strip()


mp_hands = _LazyModuleAttr("mediapipe", "solutions.hands")
mp_draw = _LazyModuleAttr("mediapipe", "solutions.drawing_utils")
mp_styles = _LazyModuleAttr("mediapipe", "solutions.drawing_styles")

def detect_gesture(hl, frame_w: int = 640, frame_h: int = 480):
    """음성 모드용 MediaPipe HandState 제스처 (OPEN/FIST/THUMB_UP)."""
    return detect_from_landmarks(hl, frame_w, frame_h)


# ──────────────────────────────────────────────
#  카메라 — 앱 실행 시 권한 거부해도 HUD는 뜨게
# ──────────────────────────────────────────────

def _is_app_launch() -> bool:
    return os.getenv("JARVIS_APP_LAUNCH", "").strip().lower() in ("1", "true", "yes")


def _fast_boot_enabled() -> bool:
    return os.getenv("JARVIS_FAST_BOOT", "").strip().lower() in ("1", "true", "yes", "on")


def _open_camera_privacy_settings() -> None:
    try:
        subprocess.Popen(
            ["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


class _PlaceholderCapture:
    """카메라 권한 대기 중 검은 화면 + 안내."""
    def __init__(self, w: int = 1280, h: int = 720):
        self._w, self._h = w, h
        self._frame = np.zeros((h, w, 3), dtype=np.uint8)

    def isOpened(self) -> bool:
        return True

    def read(self):
        return True, self._frame.copy()

    def set(self, prop, value):
        return False

    def release(self):
        pass


def _open_camera_with_retry(*, w: int, h: int, retries: int = 20, delay: float = 0.5):
    """카메라 열기 — 앱 실행 시 권한 대화상자·설정 안내 후 재시도."""
    if _is_app_launch() or _fast_boot_enabled():
        retries = min(retries, 4)
        delay = min(delay, 0.25)
    opened_settings = False
    for attempt in range(max(1, retries)):
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ok, frame = cap.read()
            if ok and frame is not None:
                return cap, False
            cap.release()
        if _is_app_launch() and not opened_settings and attempt in (0, 5, 12):
            opened_settings = True
            _open_camera_privacy_settings()
            notify_msg = (
                "카메라 권한이 필요합니다. 시스템 설정 → 개인정보 → 카메라에서 "
                "「JARVIS」를 허용해 주세요."
            )
            log("WARN", notify_msg)
            try:
                subprocess.Popen(
                    ["osascript", "-e", f'display notification "{notify_msg}" with title "J.A.R.V.I.S"'],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass
        time.sleep(delay)
    if _is_app_launch():
        log("WARN", "카메라 없음 — 권한 대기 모드로 HUD 시작 (웨이크워드·음성은 가능)")
        return _PlaceholderCapture(w, h), True
    return None, False


def _draw_camera_permission_banner(frame):
    """권한 대기 중 프레임 상단 안내."""
    if frame is None:
        return frame
    h, w = frame.shape[:2]
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 120), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.72, frame, 0.28, 0, frame)
    lines = (
        "Camera permission is required",
        "System Settings > Privacy & Security > Camera > Allow JARVIS",
        "Webcam feed will start automatically once allowed",
    )
    y = 34
    for line in lines:
        cv2.putText(frame, line, (24, y), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0, 220, 255), 2, cv2.LINE_AA)
        y += 30
    return frame


# ──────────────────────────────────────────────
#  비디오 프레임 I/O 멀티스레딩 — 메인 루프 부드러움 확보
# ──────────────────────────────────────────────

class FrameReader:
    """카메라 캡처 전용 스레드. cap.read() I/O 지터(USB hiccup 등)가 메인 렌더 루프를 막지 않도록 분리.
    최신 프레임 1장만 유지하고 Event로 메인을 깨워 카메라 fps에 자연스럽게 페이싱된다."""
    def __init__(self, cap):
        self._cap=cap; self._frame=None
        self._lock=threading.Lock(); self._evt=threading.Event()
        self._running=False; self._ok=True; self._thread=None

    def start(self):
        self._running=True
        self._thread=threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self._running:
            ret,f=self._cap.read()
            if not ret:
                self._ok=False
                time.sleep(0.05)  # 카메라 hiccup 시 CPU 스핀 방지
                continue
            self._ok=True
            with self._lock: self._frame=f
            self._evt.set()

    def read(self, timeout=1.0):
        """새 프레임 도착까지 대기 후 사본 반환. (ret=False, frame=None)이면 카메라 영구 종료."""
        if not self._evt.wait(timeout): return self._ok, None
        self._evt.clear()
        with self._lock:
            return self._ok, (self._frame.copy() if self._frame is not None else None)

    def stop(self, timeout: float = 0.25):
        self._running=False
        self._evt.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def is_thread_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())


class _HandsStub:
    """저사양 모드 — MediaPipe Hands 미생성 시 메인 루프 호환용."""
    class _Result:
        multi_hand_landmarks = None
    def process(self, rgb):
        return self._Result()
    def close(self):
        pass


class _NoOpFaceWorker:
    """비전 비활성 시 얼굴 워커 스레드를 만들지 않음."""
    def submit(self, frame):
        pass
    def start(self):
        pass
    def stop(self):
        pass


class FaceWorker:
    """얼굴 인식 백그라운드 워커 — recognize()(수십~수백ms)가 메인 프레임 시간을 잡아먹지 않게.
    submit(frame)으로 최신 프레임만 받아 interval 주기로 1장씩만 처리, 결과는 기존 face_event_q로 push."""
    def __init__(self, face_rec, event_queue, interval=0.5, gate=None):
        self._fr=face_rec; self._q=event_queue
        self._interval=interval; self._gate=gate or (lambda: True)
        self._latest=None; self._lock=threading.Lock(); self._running=False

    def submit(self, frame):
        with self._lock: self._latest=frame

    def start(self):
        if not self._fr.enabled: return
        self._running=True
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self._running:
            time.sleep(self._interval)
            if not self._gate(): continue
            with self._lock:
                f=self._latest; self._latest=None
            if f is None: continue
            try:
                self._fr.recognize(f)
                for evt in self._fr.events: self._q.put(evt)
            except Exception as e:
                log("WARN", f"face worker: {e}")

    def stop(self): self._running=False


def main():
    log("INFO","J.A.R.V.I.S v3.2 IRON MAN EDITION 초기화...")
    _existing_pid = read_pid()
    if _existing_pid and _existing_pid != os.getpid():
        try:
            os.kill(_existing_pid, 0)
            log("WARN", f"Jarvis가 이미 실행 중입니다 (pid {_existing_pid}) — 중복 기동 종료")
            sys.exit(0)
        except OSError:
            clear_pid()
    write_pid(os.getpid())
    _auto_quit_sec = auto_quit_idle_seconds()
    if _auto_quit_sec > 0:
        log("OK", f"💤 유휴 {_auto_quit_sec}초 후 절전 종료 — JARVIS.app 클릭으로 재기동 (CPU 0)")
    _stt_mode_boot = resolve_stt_mode(OPENAI_API_KEY or "")
    if free_only_enabled():
        _fm = free_mode_summary()
        log("OK", f"💸 완전 무료 모드 — LLM={_fm['llm']} STT={_fm['stt']} "
                  f"TTS={_fm['tts']} 검색={_fm['search']}")
        if _stt_mode_boot == "api":
            log("ERR", "무료 모드에서는 JARVIS_STT_BACKEND=local 이 필요합니다.")
            sys.exit(1)
    elif _stt_mode_boot == "api" and not OPENAI_API_KEY:
        log("ERR","OPENAI_API_KEY 필요 (STT=api)"); sys.exit(1)
    if LLM_PROVIDER == "ollama":
        if not _check_ollama():
            if _is_app_launch() or _fast_boot_enabled():
                log("WARN", "Ollama 준비 중 — HUD 먼저 시작 (백그라운드 대기)")
                def _wait_for_ollama():
                    for _ in range(45):
                        if _check_ollama():
                            log("OK", "🍎 Ollama 준비 완료")
                            return
                        time.sleep(1)
                    log("WARN", "Ollama 대기 시간 초과 — brew install ollama && ollama serve")
                threading.Thread(target=_wait_for_ollama, daemon=True, name="ollama-wait").start()
            else:
                log("ERR", "Ollama 미실행 — brew install ollama && ollama serve")
                sys.exit(1)
        if _check_ollama():
            log("OK", f"🍎 Mac 로컬 AI — 대화/도구={OLLAMA_MODEL} | "
                      f"주식={OLLAMA_STOCK_MODEL} | 차트비전={OLLAMA_VISION_MODEL}")
            if _llm_brain_enabled():
                log("OK", f"🧠 로컬 LLM 두뇌 ON — function calling ({len(ALL_TOOLS)} tools, 키워드 우회 OFF)")
            else:
                log("OK", "⚡ 주식 키워드 fast-path ON (JARVIS_LLM_BRAIN=0)")
        else:
            log("INFO", f"🍎 Mac 로컬 AI — Ollama 준비 대기 (모델={OLLAMA_MODEL})")
        import os as _os
        if _os.getenv("JARVIS_ECOSYSTEM_ROUTER", "1").strip().lower() not in ("0", "false", "off"):
            log("OK", "🔗 생태계 라우터 ON — 주식·Calendar·SBU·ZFoldMacBridge")
    elif LLM_PROVIDER=="gemini" and not GEMINI_API_KEY and not no_gemini_mode():
        log("ERR","GEMINI_API_KEY 필요"); sys.exit(1)
    elif no_gemini_mode():
        log("OK", f"🍎 Gemini 차단 — Ollama {OLLAMA_MODEL} (주식={OLLAMA_STOCK_MODEL}, "
                  f"비전={OLLAMA_VISION_MODEL})")

    set_cache_logger(log)
    set_router_logger(log)
    if LLM_PROVIDER == "gemini" and cache_enabled() and not no_gemini_mode():
        def _warm_gemini_cache():
            try:
                mgr = get_cache_manager()
                mgr.configure(api_key=GEMINI_API_KEY, flash_model=GEMINI_FLASH_MODEL,
                              vision_model=GEMINI_PRO_MODEL)
                mgr.warm_up(
                    flash_system_instruction=JARVIS_SYSTEM_PROMPT,
                    flash_tools=ALL_TOOLS,
                    vision_system_instruction=JARVIS_VISION_PROMPT,
                )
                st = mgr.status()
                profiles = ", ".join(
                    f"{k}={v.get('tokens', '?')}tok"
                    for k, v in (st.get("profiles") or {}).items()
                )
                if profiles:
                    log("OK", f"Gemini Context Cache 준비 — {profiles}")
            except Exception as e:
                log("WARN", f"Gemini Context Cache 워밍업 실패: {e}")
        threading.Thread(target=_warm_gemini_cache, daemon=True, name="gemini-cache-warm").start()
    elif LLM_PROVIDER == "gemini":
        log("INFO", "Gemini Context Cache 비활성 (GEMINI_CACHE_ENABLED=0)")

    # Supply Chain Java 백엔드 자동 기동 (미실행 시)
    _sc_ok = False
    if _fast_boot_enabled():
        def _boot_supply_chain():
            nonlocal _sc_ok
            try:
                _sc_ok = start_supply_chain_backend()
            except Exception as e:
                log("WARN", f"Supply Chain 백엔드 기동: {e}")
        threading.Thread(target=_boot_supply_chain, daemon=True, name="supply-chain-boot").start()
    else:
        _sc_ok = start_supply_chain_backend()

    # 느린/불안정한 WiFi 대응: 자동 재시도 + 적응형 타임아웃 (connect 빠르게, read 인내)
    oc = openai_client_for_stt(OPENAI_API_KEY or "")
    wake=WakeWordDetector(oc)
    recorder=SmartRecorder(voice_profile=wake.voice_profile)
    player=InterruptiblePlayer(); vad=VoiceActivityDetector()
    overlay=ScreenOverlay(); set_overlay_instance(overlay); set_overlay_ref(overlay)
    _vision_on=vision_enabled()
    _hands_on=_vision_on and hands_enabled()
    _low_mem=is_low_mem()
    face_rec=FaceRecognizer(enabled=_vision_on); set_face_instance(face_rec)
    hud=IronManHUD()
    try:
        from hud_resources import get_resource_sampler
        _resource_sampler = get_resource_sampler()
    except Exception:
        _resource_sampler = None
    gesture_ctrl=GestureController()
    if not _hands_on:
        gesture_ctrl.enabled=False
    bg_monitor=BackgroundMonitor()
    set_monitor_instance(bg_monitor)
    news_analyzer=NewsAnalyzer()
    set_news_analyzer_instance(news_analyzer)
    portfolio=PortfolioManager()
    set_portfolio_instance(portfolio)
    price_alert=PriceAlertWorker()
    set_price_alert_instance(price_alert)
    whale_tracker=WhaleTracker()
    set_whale_tracker_instance(whale_tracker, portfolio)
    social_detector=SocialSpikeDetector()
    set_social_detector_instance(social_detector)
    ir_interpreter=IRInterpreter()
    set_ir_interpreter_instance(ir_interpreter)
    stock_monitor=StockMonitor()
    set_stock_monitor_instance(stock_monitor)
    campus_agent=CampusAgent()
    set_campus_agent_instance(campus_agent)
    chat_log=ChatLog()
    chat_log.add("system", "J.A.R.V.I.S v3.2 Online")
    history=[]
    try:
        from memory_store import init_db, load_history, stats as mem_stats
        init_db()
        history = load_history()
        if history:
            log("OK", f"🧠 대화 메모리 복원 — {len(history)}건 ({mem_stats().get('db', '')})")
    except ImportError:
        pass

    tk=os.getenv("TAVILY_API_KEY","")
    _tts_ok = bool(FISH_API_KEY)
    if free_only_enabled():
        try:
            from local_tts import macos_tts_available
            _tts_ok = macos_tts_available()
        except Exception:
            _tts_ok = False
    _web_ok = True if free_only_enabled() else bool(tk)
    modules={"STT": _stt_mode_boot == "local" or bool(OPENAI_API_KEY),
             "LLM": True,
             "TTS": _tts_ok,
             "WEB": _web_ok,
             "WEATHER":False,"FILES":True,"FACE":face_rec.enabled}
    log("OK","━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log("OK","  IRON MAN HUD v3.2")
    log("OK","━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    # 보안 정책 요약
    _sec_pol = security_summary()
    _lvl = _sec_pol["level"]
    log("OK", f"🔒 보안 레벨: {_lvl} "
              f"(SSRF={'strict' if _sec_pol['ssrf_strict'] else 'normal'} | "
              f"FDS={'ON' if _sec_pol['fds_tools'] else 'OFF'} | "
              f"egress={'ON' if _sec_pol['net_egress_guard'] else 'OFF'})")
    if get_security_level() >= SecurityLevel.MAXIMUM:
        log("INFO", "🔒 최대 보안: Keychain 전용·샌드박스 강제·고위험 FDS·음성 인증 필수")
    # 민감 파일 권한 하드닝 (점검 전 — at-rest 0600)
    _hardened_pre = harden_sensitive_files()
    if _hardened_pre:
        log("OK", f"🔒 파일 권한 하드닝: {', '.join(_hardened_pre)}")
    # 보안 점검
    sec_warnings = check_env_security()
    if sec_warnings:
        for sw in sec_warnings:
            log("WARN", f"🔒 보안: {sw}")
        _blocking = [w for w in sec_warnings if is_boot_blocking_security_warning(w)]
        if is_maximum_security() and boot_halt_on_security_warn() and _blocking:
            log("ERR", "🔒 최대 보안 — 치명적 보안 경고로 부팅 중단 "
                       "(secure_config --migrate, git rm --cached 등)")
            chain_event("BOOT_SECURITY_HALT", {"warnings": _blocking[:8]})
            clear_pid()
            sys.exit(2)
        elif sec_warnings:
            log("INFO", "🔒 권한 등 경고는 자동 하드닝됨 — 부팅 계속")
    else:
        log("OK", "🔒 보안 점검 통과")
    _sec_st = get_security_status()
    _kc_keys = sum(1 for v in _sec_st["keys"].values() if "Keychain" in v)
    _env_keys = sum(1 for v in _sec_st["keys"].values() if "평문" in v)
    if _kc_keys > 0:
        log("OK", f"🔒 Keychain: {_kc_keys}개 키 암호화 보관")
    if _env_keys > 0:
        log("WARN", f"🔒 .env 평문: {_env_keys}개 키 — 'python3 secure_config.py --migrate' 권장")
    # 파일 시스템 샌드박스 초기화 + OS 레벨 격리
    try:
        import sandbox as _sandbox
        _sbx = _sandbox.enforce_os_isolation()
        log("OK", f"🔒 파일 샌드박스: root={_sbx['root']} "
                  f"강제={_sbx['enforced']} 조치={','.join(_sbx['measures'])}")
    except Exception as _e:
        log("WARN", f"🔒 파일 샌드박스 초기화 실패: {_e}")
    def _run_audit_chain_check():
        _chain_ok, _chain_msg = verify_audit_chain()
        if _chain_ok:
            log("OK", f"🔒 감사 로그 무결성: {_chain_msg}")
            return
        _enforce = os.getenv("JARVIS_AUDIT_ENFORCE", "warn").strip().lower()
        log("ERR", f"🔒 감사 로그 변조/누락 감지: {_chain_msg}")
        if _enforce in ("strict", "halt", "exit"):
            log("ERR", "🔒 안전 모드: 감사 로그 무결성 위반 — 종료 예정 "
                       "(JARVIS_AUDIT_ENFORCE=strict). 'python3 security.py --reinit' 후 재시작하세요.")
            chain_event("AUDIT_INTEGRITY_HALT", {"reason": _chain_msg})
            os._exit(2)
        _arch = reinitialize_audit_log(reason="boot_integrity_fail")
        log("WARN", f"🔒 손상 감사 로그를 격리하고 체인을 재초기화했습니다 → {_arch}")

    if _fast_boot_enabled():
        threading.Thread(target=_run_audit_chain_check, daemon=True, name="audit-chain").start()
        log("INFO", "🔒 감사 로그 무결성 검사 — 백그라운드")
    else:
        _run_audit_chain_check()
    chain_event("BOOT", {"version": "v3.2", "keychain_keys": _kc_keys})
    # 100-pass 보안 감사 (앱 실행 시 스킵 — 기동 속도·GUI 권한 우선)
    if os.getenv("JARVIS_SKIP_BOOT_AUDIT", "").strip().lower() not in ("1", "true", "yes"):
        try:
            from security_bootstrap import run_security_audit_passes, patch_requests_egress_guard
            patch_requests_egress_guard()
            _boot_audit = run_security_audit_passes(100, auto_fix=True)
            if _boot_audit.get("findings"):
                for _bf in _boot_audit["findings"][:5]:
                    log("WARN", f"🔒 감사: {_bf}")
                if len(_boot_audit["findings"]) > 5:
                    log("WARN", f"🔒 감사: 외 {len(_boot_audit['findings']) - 5}건 — security_bootstrap.py --audit 100")
            else:
                log("OK", "🔒 100-pass 보안 감사 통과")
            if _boot_audit.get("fixes"):
                log("OK", f"🔒 자동 하드닝: {', '.join(_boot_audit['fixes'][:3])}")
        except Exception as _ae:
            log("WARN", f"🔒 security_bootstrap: {_ae}")
    else:
        log("INFO", "🔒 앱 실행 — 100-pass 부팅 감사 스킵")
    # OpenClaw 가드 (앱 실행 시 스킵 — CLI 40초+ 지연 방지, jarvis_link는 유지)
    if os.getenv("JARVIS_SKIP_OPENCLAW_BOOT", "").strip().lower() not in ("1", "true", "yes"):
        try:
            _oc = boot_openclaw_guard(log_fn=log)
            if _oc.get("enabled"):
                log("OK", f"🦞 OpenClaw 가드 ON — 변경 {_oc.get('changes', [])[:3]}")
            elif Path.home().joinpath(".openclaw").exists():
                log("INFO", "🦞 OpenClaw 설치됨 — OPENCLAW_ENABLED=1 로 가드 활성화")
        except Exception as _e:
            log("WARN", f"OpenClaw 가드 초기화: {_e}")
    else:
        log("INFO", "🦞 앱 실행 — OpenClaw 부팅 하드닝 스킵 (터미널: openclaw_guard.py --harden)")
    log("OK", "🔒 추가 레이어: 프롬프트 인젝션 방어 · SSRF 가드 · 파괴적 작업 제한 · 변조방지 로그 활성")
    log("OK", f"📶 네트워크 최적화: 연결 재사용·자동 재시도·적응형 타임아웃 "
              f"(프로파일={os.getenv('NET_PROFILE','normal')}, timeout={net.DEFAULT_TIMEOUT})")
    vp=wake.voice_profile
    if voice_auth_required() and voice_setup_required(vp):
        log("WARN", "🔒 최대 보안 — 음성 미등록: V키로 5회 수집 후 웨이크/명령 가능")
    elif vp.trained:
        _dev_tag = " (등록 기기 — 재수집 불필요)" if is_known_device() else ""
        log("INFO",f"음성 프로필: 학습완료 ({vp._sample_count}샘플){_dev_tag}")
        log("INFO","→ \"자비스\"로만 녹음 시작 (주인 목소리 전용)")
        log("INFO","→ 5초 정적 시 자동 종료 | 소음 자동 필터링")
    else:
        log("INFO","음성 프로필: 미학습 → V키로 음성 5회 수집")
        if _hands_on:
            log("INFO","→ 학습 전: 주먹/스페이스/웨이크워드 모두 사용 가능")
        else:
            log("INFO","→ 학습 전: 스페이스/웨이크워드로 녹음 (손 인식 OFF)")
    if _hands_on:
        log("INFO",f"제스처 마우스: G키로 ON/OFF (☝이동 🤏클릭 ✌스크롤 👍볼륨)")
        log("INFO","손짓 명령: 검지 유지 → 녹음 시작 · 브이 유지 → 전송 (G키 마우스 OFF일 때)")
    else:
        log("INFO", "MediaPipe 손 OFF — 웨이크워드·스페이스·화면 키보드 (JARVIS_HANDS_ENABLED=1 로 복구)")
    log("INFO","화면 키보드: Enter 또는 / · 맥 입력기는 영어, 글자는 한글 두벌식 · Tab 한/영")
    log("INFO",f"차트 비전 분석: S키 — 웹캠에 차트를 비추고 누르면 기술적 분석 브리핑")
    log("INFO",f"백그라운드: GitHub={'ON' if bg_monitor.github_enabled else 'OFF'} | "
               f"PlayStore={'ON' if bg_monitor.playstore_enabled else 'OFF'} | "
               f"간격={MONITOR_INTERVAL}초")
    log("INFO",f"뉴스 감시: {'ON' if news_analyzer.enabled else 'OFF'}"
               + (f" ({', '.join(NEWS_WATCHLIST)})" if news_analyzer.enabled
                  else " — NEWS_WATCHLIST 설정 필요"))
    log("INFO",f"포트폴리오: 예수금 {portfolio.data.get('available_cash_krw',0):,}원, "
               f"보유 {len(portfolio.data.get('holdings',{}))}개 ({portfolio.path})")
    try:
        from price_alert import technical_signal_alerts_enabled as _tech_sig_on
    except ImportError:
        _tech_sig_on = lambda: False  # noqa: E731
    log("INFO",f"지정가 알림: {'ON' if price_alert.enabled else 'OFF'}"
               + (f" ({', '.join(WATCH_LIST)}) RSI<{price_alert.rsi_oversold:g}, {price_alert.interval}초 간격"
                  if price_alert.enabled else " — WATCH_LIST 설정 필요")
               + (f" | RSI·볼린저 자동알림={'ON' if _tech_sig_on() else 'OFF(환율·목표가만)'}"
                  if price_alert.enabled else ""))
    log("INFO",f"고래 추적: {'ON' if whale_tracker.enabled else 'OFF'}"
               + (f" ({', '.join(WHALE_WATCHLIST)}) {whale_tracker.interval}초 간격"
                  if whale_tracker.enabled else " — WHALE_WATCHLIST 설정 필요"))
    log("INFO",f"소셜 감지: {'ON' if social_detector.enabled else 'OFF'}"
               + (f" ({', '.join(SOCIAL_SUBREDDITS)}) {social_detector.interval}초 간격"
                  if social_detector.enabled else " — SOCIAL_SUBREDDITS 설정 필요"))
    log("INFO","IR 통역: 대기 (start_ir_interpreter 호출 시 streamlink/ffmpeg 사용)")
    log("INFO","Media Pipeline: 대기 (submit_media_url 호출 시 streamlink/ffmpeg 캡처+전처리)")
    log("INFO",f"주가 감시: ON (동적 알림 {len(stock_monitor._rules)}개 등록됨 | {stock_monitor.interval}초 간격)")
    _campus_on = campus_agent.enabled and bool(campus_mail_account())
    log("INFO",f"캠퍼스 감시(SBU): {'ON' if _campus_on else 'OFF'}"
               + (f" (Gmail/Calendar {campus_agent.interval}초 간격"
                  + (", 인증완료)" if campus_agent.status['token_exists'] else ", 토큰없음 → 'python3 campus_agent.py --auth' 필요)")
                  if _campus_on else " — 로그인에서 학교 메일을 등록하면 시작합니다"))
    log("INFO", f"Supply Chain Agent: {os.getenv('SUPPLY_CHAIN_URL','http://localhost:8080')} "
               f"({'ON' if _sc_ok else 'OFF'})")
    _ollama_ok = _check_ollama()
    log("INFO",f"Ollama 폴백: {'ON' if _ollama_ok else 'OFF'}"
               + (f" (모델={OLLAMA_MODEL}, ctx={OLLAMA_NUM_CTX}, GPU={'Metal' if OLLAMA_NUM_GPU else 'CPU'}, {OLLAMA_URL})" if _ollama_ok
                  else " — 오프라인 시: brew install ollama && ollama pull qwen2.5:7b-instruct-q4_K_M"))
    _perf = performance_profile_summary()
    if _perf["apple_silicon"]:
        log("INFO", f"Apple Silicon 프로파일: STT프로세스={'ON' if _perf['stt_process'] else 'OFF'}"
                   f" | 워커순차기동={'ON' if _perf['stagger_workers'] else 'OFF'}"
                   f"({_perf['worker_stagger_sec']:.0f}s)"
                   f" | Whisper=CPU")
    log("INFO", "실행 경로: 메인=맥북 CPU · 그 외=백엔드 후 CPU로 백트래킹 · 시간·시스템·파일·일정·소리는 오프라인")
    log("INFO", f"대화 컨텍스트: 최근 {context_turn_limit()}턴 / 요약={'ON' if os.getenv('JARVIS_HISTORY_SUMMARIZE','1').strip() not in ('0','false','off') else 'OFF'}")
    try:
        from wake_engine import wake_engine_mode, oww_enabled
        _we = wake_engine_mode()
        log("INFO", f"웨이크워드 엔진: {_we} | openWakeWord={'ON' if oww_enabled() else 'OFF'} | STT=CPU Whisper")
    except ImportError:
        pass
    try:
        from memory_store import stats as _mem_st
        _ms = _mem_st()
        if _ms.get("enabled"):
            log("INFO", f"🧠 SQLite 메모리: {_ms.get('messages', 0)}건 / prefs {_ms.get('prefs', 0)}")
    except ImportError:
        pass

    state="IDLE"; last_gesture="NONE"; gesture_hold=0; GESTURE_THRESHOLD=12
    last_transcript=""; last_answer=""; space_recording=False; frame_count=0

    _cam_w, _cam_h = camera_resolution()
    cap, camera_pending = _open_camera_with_retry(w=_cam_w, h=_cam_h)
    if cap is None:
        log("ERR", "웹캠없음")
        clear_pid()
        sys.exit(1)
    if camera_pending:
        chat_log.add("system", "📷 Camera permission required — allow JARVIS in System Settings")
        _vision_on = False
        _hands_on = False
        hands = _HandsStub()
        log("INFO", "Waiting for camera permission — face/hands disabled, voice & typing available")
    _fps_cap = main_loop_fps()
    _stt_mode = resolve_stt_mode(OPENAI_API_KEY or "")
    log("INFO", f"카메라 해상도 {_cam_w}x{_cam_h}"
               f"{' (저사양)' if _low_mem else ''} | 메인루프 ≤{_fps_cap:g} FPS"
               f"{' | 권한대기' if camera_pending else ''}")
    if _low_mem:
        log("INFO", f"저사양 프로파일: 비전={'ON' if _vision_on else 'OFF'} | STT={_stt_mode}"
                   f" | 얼굴={'ON' if _vision_on else 'OFF'} | 손={'ON' if _hands_on else 'OFF'}")
    # 프레임 캡처를 전용 스레드로 분리 — 메인 루프는 즉시 최신 프레임 수신
    frame_reader=FrameReader(cap); frame_reader.start()
    _camera_retry_at = time.time() + 8.0
    if _hands_on and not camera_pending:
        hands=mp_hands.Hands(model_complexity=hand_complexity(), max_num_hands=1,
                             min_detection_confidence=0.7, min_tracking_confidence=0.6)
    elif not camera_pending:
        hands=_HandsStub()
        if _vision_on and not _hands_on:
            log("INFO", "MediaPipe Hands OFF — 얼굴·웨이크워드·타이핑·스페이스")
        elif not _vision_on:
            log("INFO", "비전 OFF — 웨이크워드/타이핑/스페이스로 명령 입력")

    result_q=queue.Queue(); interrupt_flag=threading.Event(); face_event_q=queue.Queue()
    _edit_q=queue.Queue()   # (ChatMessage객체, 새텍스트) 쌍 — 터미널 편집 결과 전달용
    _sel_result_q=queue.Queue()  # 종목 직접입력(타이핑) 결과 메시지 — 메인 루프가 음성 안내
    _text_cmd_q=queue.Queue()    # T키 타이핑 명령(터미널 입력) → 메인 루프가 IDLE에서 처리
    typing_input={"active":False, "proc":None}  # 타이핑 팝업 입력창 상태 (중복 방지 + 종료 정리)
    screen_kb = ScreenKeyboard()
    show_help = {"on": True}
    show_bg = {"on": False}
    show_perf = {"on": False}
    # 종목명 오인식 → 후보 선택 대기 상태 (음성 번호 1~5 / 6=직접 타이핑)
    pending_sel={"active":False, "ctx":None, "await_record":False, "rearm":False, "retries":0}
    # 얼굴 인식 워커 — 저사양 시 스레드 미생성
    if _vision_on and face_rec.enabled:
        face_worker=FaceWorker(face_rec, face_event_q, interval=face_worker_interval(),
                               gate=lambda: state!="RECORDING")
        face_worker.start()
    else:
        face_worker=_NoOpFaceWorker()

    # 웨이크워드 감지 시작 (별도 마이크 스트림) — 즉시 기동
    wake.start(recorder.pa)

    bind_task("prefetch", start_prefetch, stop_prefetch, prefetch_running)
    bind_task("github", bg_monitor.start, bg_monitor.stop, lambda: bg_monitor._running)
    bind_task("news", news_analyzer.start, news_analyzer.stop, lambda: news_analyzer._running)
    bind_task("price", price_alert.start, price_alert.stop, lambda: price_alert._running)
    bind_task("whale", whale_tracker.start, whale_tracker.stop, lambda: whale_tracker._running)
    bind_task("social", social_detector.start, social_detector.stop, lambda: social_detector._running)
    bind_task("stock", stock_monitor.start, stock_monitor.stop, lambda: stock_monitor._running)
    bind_task("campus", campus_agent.start, campus_agent.stop, lambda: campus_agent._running)

    def _start_background_workers(stagger=True):
        """백그라운드 워커 순차 기동 — 꺼 둔 작업은 시작하지 않는다."""
        workers = [
            ("prefetch", "관심 미리받기", start_prefetch),
            ("github", "GitHub/PlayStore", bg_monitor.start),
            ("news", "뉴스", news_analyzer.start),
            ("price", "지정가", price_alert.start),
            ("whale", "고래", whale_tracker.start),
            ("social", "소셜", social_detector.start),
            ("stock", "주가", stock_monitor.start),
            ("campus", "캠퍼스", campus_agent.start),
        ]
        delay = worker_stagger_seconds() if stagger else 0
        for key, name, fn in workers:
            if not is_enabled(key):
                log("INFO", f"백그라운드 워커({name}) 꺼짐 — 사용자 설정")
                continue
            try:
                fn()
            except Exception as e:
                log("WARN", f"백그라운드 워커({name}) 기동 실패: {e}")
            if delay:
                time.sleep(delay)

    if stagger_workers_enabled():
        threading.Thread(target=_start_background_workers, daemon=True, name="worker-stagger").start()
        log("INFO", f"Background workers scheduled with stagger ({worker_stagger_seconds():.0f}s interval)")
    else:
        _start_background_workers(stagger=False)

    def _warmup_lazy_modules():
        """Preload deferred heavy modules (pandas/yfinance, face_recognition) in background after HUD init."""
        try:
            time.sleep(1.5)
            from stock_quant import pd as _pd, yf as _yf
            if hasattr(_pd, "_load"):
                _pd._load()
            if _yf is not None and hasattr(_yf, "_load"):
                _yf._load()
            if _vision_on and face_rec.enabled:
                from face_module import face_recognition as _fr
                if _fr is not None and hasattr(_fr, "_load"):
                    _fr._load()
        except Exception:
            pass

    threading.Thread(target=_warmup_lazy_modules, daemon=True, name="lazy-warmup").start()

    # 선제적 매크로 가드레일 감시 — IDLE 중 '위험' 격상 시 먼저 음성 브리핑
    macro_guard = MacroGuardWatcher(status_fn=get_macro_market_status,
                                    logger=lambda lvl, msg: log(lvl, msg))
    macro_guard.start()
    _hud_theme_level="안정"   # HUD 전역 테마 등급 추적(변경 시에만 set_theme 호출)
    log("INFO", f"매크로 가드레일 감시 시작 (주기 {macro_guard.poll_sec:.0f}초)")

    # 주식 2단계 분석: 예비 답변을 먼저 말하고, 모든 무료 데이터·AI 정밀 교차검증은 백그라운드에서
    os.environ.setdefault("JARVIS_STOCK_TWO_PHASE", "1")
    from cv_followup import get_cv_followup
    cv_followup = get_cv_followup(logger=lambda lvl, msg: log(lvl, msg))
    log("INFO", "주식 2단계 교차검증 후속 브리핑 ON (예비 답변 → 백그라운드 멀티AI 정밀 분석)")

    # ── JARVIS Link: 모바일 미러링 게이트웨이(웹소켓 Pub/Sub + 원격 승인 + 위험 푸시) ──
    link = init_link(logger=lambda lvl, msg: log(lvl, msg))
    try:
        link.start()
    except Exception as e:
        log("WARN", f"JARVIS Link 기동 실패 — 화면·음성은 계속합니다: {e}")
    # 외출(자리비움) 상태 머신: 일정 시간 얼굴 미검출 시 자동 AWAY → 위험 시 모바일 푸시
    AWAY_TIMEOUT_SEC = float(os.getenv("JARVIS_AWAY_TIMEOUT_SEC", "120"))
    away_state = {"away": False, "last_seen": time.time(), "manual": False}
    # 테스트 플래그: 1이면 자리에 있어도(AWAY 아니어도) 항상 모바일 푸시
    PUSH_ALWAYS = os.getenv("TELEGRAM_ALWAYS_PUSH", "0").strip().lower() not in ("0", "", "false", "no")
    _last_hud_pub = 0.0   # HUD 스트림 발행 스로틀(과도 브로드캐스트 방지)

    def push_if_away(message, context=None):
        """외출 상태(또는 TELEGRAM_ALWAYS_PUSH)일 때 모바일로 위험 알림 푸시(+원격 승인 코드 발급). 데스크톱 HUD 는 그대로 유지."""
        if not away_state["away"] and not PUSH_ALWAYS:
            return None
        ctx = dict(context or {})
        res = link.push_alert(message, context=ctx, issue_code=ctx.get("rebalance", False))
        chs = ", ".join(res.get("channels") or []) or "WebSocket"
        log("INTR", f"📱 외출 중 위험 알림 모바일 푸시({chs})"
                   + (f" · 승인코드 {res['approval_code']}" if res.get("approval_code") else ""))
        return res
    if wake.enabled:
        if wake.voice_profile.trained:
            chat_log.add("system", "Voice Auth: owner-only wake word (\"Jarvis\")")
            chat_log.add("system", "Auto-stops after 3s silence | Noise filtering active")
        else:
            chat_log.add("system", "Press V to enroll voice (5 samples) for owner-only wake")
    chat_log.add("system", "⌨ Enter or / : On-screen keyboard (Tab EN/KO, Enter Send, Esc Cancel)")
    chat_log.add("system", "B: Background Tasks  ·  Z: Power Save (Webcam & Whisper OFF)")
    chat_log.add("system", "☝ Hold index finger to talk · ✌ Hold peace sign (V) to send")

    # 스트리밍 TTS 파이프라인
    tts_pipeline = StreamingTTSPipeline()
    tts_pipeline.set_player(player)

    _wake_before_power_save = {"enabled": True}

    def _power_save_camera_off():
        nonlocal cap, frame_reader, _vision_on, _hands_on, camera_pending, face_worker, hands
        try:
            frame_reader.stop()
        except Exception:
            pass
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass
        cap = None
        _vision_on = False
        _hands_on = False
        camera_pending = False
        try:
            face_worker.stop()
        except Exception:
            pass
        frame_reader = PowerSaveFrameReader(_cam_w, _cam_h)

    def _power_save_camera_on():
        nonlocal cap, frame_reader, _vision_on, _hands_on, camera_pending, face_worker, hands
        new_cap, still_pending = _open_camera_with_retry(w=_cam_w, h=_cam_h, retries=4, delay=0.35)
        if new_cap is None:
            frame_reader = PowerSaveFrameReader(_cam_w, _cam_h)
            chat_log.add("system", "📷 Failed to restore webcam — staying in power save display")
            return
        cap = new_cap
        camera_pending = still_pending
        _vision_on = vision_enabled() and not still_pending
        _hands_on = _vision_on and hands_enabled()
        frame_reader = FrameReader(cap)
        frame_reader.start()
        if _hands_on:
            try:
                hands.close()
            except Exception:
                pass
            hands = mp_hands.Hands(
                model_complexity=hand_complexity(), max_num_hands=1,
                min_detection_confidence=0.7, min_tracking_confidence=0.6,
            )
        elif _vision_on:
            hands = _HandsStub()
        if _vision_on and face_rec.enabled:
            face_worker = FaceWorker(
                face_rec, face_event_q, interval=face_worker_interval(),
                gate=lambda: state != "RECORDING",
            )
            face_worker.start()
        else:
            face_worker = _NoOpFaceWorker()
        if still_pending:
            chat_log.add("system", "📷 Camera permission required — allow in System Settings")
        else:
            chat_log.add("system", "📷 Webcam restored")

    def _power_save_wake_off():
        _wake_before_power_save["enabled"] = wake.enabled
        wake.enabled = False
        wake.pause()

    def _power_save_wake_on():
        wake.enabled = _wake_before_power_save.get("enabled", True)
        if wake.enabled:
            wake.resume()

    def _apply_power_save(on: bool) -> str:
        ps_kwargs = dict(
            camera_off=_power_save_camera_off,
            camera_on=_power_save_camera_on,
            wake_off=_power_save_wake_off,
            wake_on=_power_save_wake_on,
            log_fn=log,
        )
        if on:
            if state == "RECORDING":
                recorder.stop()
            return set_power_save(True, **ps_kwargs)
        return set_power_save(False, **ps_kwargs)

    def _reply_control(clean):
        lang_reply = interpret_language_command(clean)
        if lang_reply:
            chat_log.add("user", clean)
            chat_log.add("jarvis", lang_reply)
            try:
                speak_text(lang_reply)
            except Exception as e:
                log("WARN", f"Language switch voice failed: {e}")
            result_q.put(("OK", clean, lang_reply, None))
            return True
        ps_hint = interpret_power_save_command(clean)
        if ps_hint:
            reply = ps_hint
        elif wants_power_save_on(clean):
            reply = _apply_power_save(True)
        elif wants_power_save_off(clean):
            reply = _apply_power_save(False)
        else:
            reply = None
        if not reply:
            reply = interpret_performance_command(clean)
        if not reply:
            reply = interpret_control_command(clean)
        if not reply:
            return False
        chat_log.add("user", clean)
        chat_log.add("jarvis", reply)
        try:
            speak_text(reply)
        except Exception as e:
            log("WARN", f"백그라운드 제어 음성 실패: {e}")
        result_q.put(("OK", clean, reply, None))
        return True

    def process_async(wav):
        """STT → 스트리밍 LLM → 실시간 TTS (구간별 지연 타이밍 측정 포함)"""
        try:
            t_start = time.perf_counter()
            text = transcribe_audio(wav, oc)
            t_stt = time.perf_counter()
            stt_ms = (t_stt - t_start) * 1000
            if not text:
                result_q.put(("ERROR", "", ""))
                return
            ok, reason, clean = validate_command_input(text, source="user_voice")
            if not ok:
                chat_log.add("system", f"⛔ Blocked: {reason}")
                log("WARN", f"Voice input blocked: {reason}")
                result_q.put(("ERROR", "", ""))
                return
            if _reply_control(clean):
                return
            chat_log.add("user", clean)
            acknowledge()

            # 스트리밍 LLM + 실시간 TTS
            t_llm0 = time.perf_counter()
            answer = ask_jarvis_streaming(clean, oc, history, tts_pipeline)
            llm_ms = (time.perf_counter() - t_llm0) * 1000

            chat_log.add("jarvis", answer)
            append_exchange(history, clean, answer,
                            gemini_api_key=GEMINI_API_KEY, gemini_model=GEMINI_FLASH_MODEL,
                            log_fn=log)

            # 스트리밍이므로 audio_path는 None (이미 재생 중/완료)
            result_q.put(("OK", clean, answer, None))

            first_tts_ms = tts_pipeline.latency * 1000 if tts_pipeline.latency > 0 else 0.0
            if os.getenv("JARVIS_TIMING_LOG", "0").strip().lower() in ("1", "true", "yes", "on"):
                log("INFO", f"⚡ [Timing] Voice pipeline — STT={stt_ms:.0f}ms | LLM={llm_ms:.0f}ms | FirstTTS={first_tts_ms:.0f}ms")

            # 재생 완료 대기
            tts_pipeline.wait_done(timeout=30)

        except Exception as e:
            log("ERR", f"{e}")
            _debug_exc()
            result_q.put(("ERROR", str(e), ""))

    def process_text_async(text):
        """타이핑 입력 → (음성과 동일 파이프라인) 스트리밍 LLM → 실시간 TTS.
        음성 STT와 동일하게 공통 입력 게이트(validate_command_input)로
        명령어 주입/경로 조작을 차단하고 프롬프트 인젝션을 중화한 뒤 LLM에 전달한다."""
        try:
            ok, reason, clean = validate_command_input(text, source="user_typed")
            if not ok:
                chat_log.add("system", f"⛔ Blocked: {reason}")
                log("WARN", f"Typed input blocked: {reason}")
                result_q.put(("ERROR", "", ""))
                return
            if _reply_control(clean):
                return
            chat_log.add("user", clean)
            acknowledge()

            t_llm0 = time.perf_counter()
            answer = ask_jarvis_streaming(clean, oc, history, tts_pipeline)
            llm_ms = (time.perf_counter() - t_llm0) * 1000

            chat_log.add("jarvis", answer)
            append_exchange(history, clean, answer,
                            gemini_api_key=GEMINI_API_KEY, gemini_model=GEMINI_FLASH_MODEL,
                            log_fn=log)

            result_q.put(("OK", clean, answer, None))
            first_tts_ms = tts_pipeline.latency * 1000 if tts_pipeline.latency > 0 else 0.0
            if os.getenv("JARVIS_TIMING_LOG", "0").strip().lower() in ("1", "true", "yes", "on"):
                log("INFO", f"⚡ [Timing] Text pipeline — LLM={llm_ms:.0f}ms | FirstTTS={first_tts_ms:.0f}ms")
            tts_pipeline.wait_done(timeout=30)
        except Exception as e:
            log("ERR", f"{e}")
            _debug_exc()
            result_q.put(("ERROR", str(e), ""))

    def process_chart_async(snap_frame):
        """웹캠 프레임 캡처 → Gemini 비전 차트 분석 → 실시간 TTS 브리핑
        기존 상태머신(PROCESSING→SPEAKING→IDLE)을 그대로 재사용한다."""
        try:
            # 미러 플립을 되돌려(원본 방향) 차트 텍스트/캔들이 정상 방향이 되도록 저장
            snap_path = str(AUDIO_DIR / "chart_snapshot.png")
            cv2.imwrite(snap_path, cv2.flip(snap_frame, 1))
            log("OK", f"차트 스냅샷 저장: {snap_path}")
            chat_log.add("user", "📷 차트 이미지 기술적 분석 요청")

            if LLM_PROVIDER != "ollama" and not GEMINI_API_KEY and not no_gemini_mode():
                chat_log.add("system", "비전 API 없음 — 차트 분석 불가")
                result_q.put(("ERROR","",""))
                return
            if (free_only_enabled() or no_gemini_mode() or LLM_PROVIDER == "ollama") and not _check_ollama():
                chat_log.add("system", "Ollama 미실행 — 차트 비전 분석 불가")
                result_q.put(("ERROR","",""))
                return

            # 스트리밍 TTS 시작 → 문장 단위로 끊어 부드럽게 음성 브리핑
            tts_pipeline.start()
            answer = analyze_chart_image(snap_path)
            if not answer:
                tts_pipeline.finish()
                result_q.put(("ERROR","",""))
                return

            for ch in answer:
                tts_pipeline.feed(ch)
            tts_pipeline.finish()

            chat_log.add("jarvis", answer)
            append_exchange(history, "[웹캠 차트 이미지 분석 요청]", answer,
                            gemini_api_key=GEMINI_API_KEY, gemini_model=GEMINI_FLASH_MODEL,
                            log_fn=log)

            result_q.put(("OK","📷 차트 분석", answer, None))

            tts_pipeline.wait_done(timeout=40)
            if tts_pipeline.latency > 0:
                log("INFO", f"첫 응답 지연: {tts_pipeline.latency:.1f}초")

        except Exception as e:
            log("ERR", f"차트 분석 오류: {e}")
            _debug_exc()
            result_q.put(("ERROR", str(e), ""))

    def speak_text(text):
        """단일 텍스트 TTS — 다른 발화가 끝난 뒤 한 번에 하나만."""
        if is_shutting_down():
            return
        text = polish_for_voice(text or "")
        if not text or is_shutting_down():
            return
        if not wait_audio_idle(tts_pipeline=tts_pipeline, player=player, timeout=180):
            log("WARN", "다른 음성 재생 대기 초과 — speak_text 스킵")
            return
        if not begin_speech(timeout=180):
            log("WARN", "음성 게이트 확보 실패 — speak_text 스킵")
            return
        audio = None
        try:
            if is_shutting_down():
                return
            audio = synthesize_speech(text)
            if audio and not is_shutting_down():
                wake.pause()
                player.play(audio)
        finally:
            if audio:
                try:
                    Path(audio).unlink(missing_ok=True)
                except Exception:
                    pass
            end_speech()

    # ── 명령 수신 확인 멘트 (음성/타이핑 공통) — 기본 OFF(즉시 본 답변 시작), JARVIS_ACK_FIRST=1 로 활성화 가능 ──
    ACK_FIRST = os.getenv("JARVIS_ACK_FIRST", "0").strip().lower() in ("1","true","yes","on")
    def acknowledge():
        """처리 시작 전, 명령을 이해했고 수행하겠다는 짧은 멘트를 먼저 음성으로 출력(JARVIS_ACK_FIRST=1 일 때만)."""
        if not ACK_FIRST:
            return
        import random
        try:
            phrases = list(get_ack_phrases())
            if phrases:
                speak_text(random.choice(phrases))
        except Exception as e:
            log("WARN", f"Acknowledge failed: {e}")

    # ── 종목 후보 선택 (오인식 → 음성 번호 / 6=직접 타이핑) ──
    def _parse_choice(text):
        """음성 답변에서 후보 번호(1~6) 추출. 직접/타이핑/입력/6 → 6번(직접입력)."""
        import re
        t=(text or "").strip().lower(); tc=t.replace(" ","")
        if any(k in tc for k in ("직접","타이핑","입력","type","typing","manual")): return 6
        m=(re.search(r"([1-6])\s*번", t) or re.search(r"\b([1-6])\b", t)
           or re.search(r"([1-6])", t))
        if m: return int(m.group(1))
        words={"여섯":6,"육":6,"six":6,"sixth":6,"다섯":5,"오":5,"five":5,"fifth":5,
               "네번":4,"넷":4,"사":4,"four":4,"fourth":4,"셋":3,"세번":3,"삼":3,"three":3,"third":3,
               "둘":2,"두번":2,"이번":2,"two":2,"second":2,"하나":1,"한번":1,"첫":1,"일번":1,"one":1,"first":1}
        for w,n in words.items():
            if w in tc: return n
        return None

    def announce_candidates(msg):
        """후보 목록을 음성 안내 → 끝나면 사용자 답변 자동 녹음 준비."""
        speak_text(msg)
        pending_sel["await_record"] = True

    def _arm(query, price, direction, cands):
        """후보 목록을 안내하고 음성 번호 답변 대기 상태로 무장."""
        lines=", ".join(f"number {i}: {c['name']}" for i,c in enumerate(cands,1))
        msg=(f"I couldn't find an exact match for '{query}'. Here are the closest candidates: "
             f"{lines}, or say number 6 to type it directly. Please say a number.")
        pending_sel["active"]=True
        pending_sel["ctx"]={"query":query,"price":price,"direction":direction,"candidates":cands}
        pending_sel["await_record"]=False; pending_sel["rearm"]=False
        chat_log.add("jarvis", msg)
        log("INFO", f"🔢 Candidate selection prompt: {[c['name'] for c in cands]}")
        threading.Thread(target=announce_candidates,args=(msg,),daemon=True).start()

    def _speak_and_signal(text_in, msg):
        """고정 메시지를 스트리밍 TTS로 말하고 상태머신에 완료 신호."""
        tts_pipeline.start()
        for ch in msg: tts_pipeline.feed(ch)
        tts_pipeline.finish()
        result_q.put(("OK", text_in, msg, None))
        tts_pipeline.wait_done(timeout=30)

    def process_typing(ctx):
        """6번 직접 입력 — 터미널에서 종목명 타이핑 → 지정가 알림 등록."""
        try:
            print("\n[Direct Input] Enter stock name or ticker and press Enter: ", end="", flush=True)
            q=input().strip()
        except Exception:
            q=""
        if not q:
            _sel_result_q.put("No input received. Price alert setup canceled."); return
        res=add_target_by_query(q, ctx.get("price"), ctx.get("direction"))
        _sel_result_q.put(res.get("message","Price alert registered."))

    def process_selection_async(wav):
        """후보 선택 답변 처리 — 음성 번호(1~5) 파싱 / 6=직접입력 / 실패 시 재안내."""
        try:
            text=transcribe_audio(wav,oc) or ""
            ctx=dict(pending_sel.get("ctx") or {})
            chat_log.add("user", text)
            cands=ctx.get("candidates",[]); price=ctx.get("price"); direction=ctx.get("direction")
            choice=_parse_choice(text)
            if choice==6:
                pending_sel["active"]=False
                threading.Thread(target=process_typing,args=(ctx,),daemon=True).start()
                _speak_and_signal(text, "Direct input mode. Please type the ticker in the terminal."); return
            if choice and 1<=choice<=5 and choice-1 < len(cands):
                res=add_target_choice(cands[choice-1], price, direction)
                pending_sel["active"]=False; pending_sel["ctx"]=None
                _speak_and_signal(text, res.get("message","Price alert has been set.")); return
            # 인식 실패 / 범위 밖 → 재안내 (최대 3회)
            pending_sel["retries"]+=1
            if pending_sel["retries"]>=3:
                pending_sel["active"]=False; pending_sel["ctx"]=None
                _speak_and_signal(text, "Could not recognize a number. Price alert setup canceled."); return
            pending_sel["rearm"]=True
            _speak_and_signal(text, "I didn't catch that number. Please say a number from 1 to 5, or 6 for direct input.")
        except Exception as e:
            log("ERR",f"Selection error: {e}")
            pending_sel["active"]=False
            result_q.put(("ERROR",str(e),""))

    def dispatch_processing(wav):
        """선택 대기 중이면 번호 파싱, 아니면 일반 LLM 처리."""
        if pending_sel.get("active"):
            threading.Thread(target=process_selection_async,args=(wav,),daemon=True).start()
        else:
            threading.Thread(target=process_async,args=(wav,),daemon=True).start()

    def startup_greeting():
        """시작 인사 — 시각·위치 후 환율(종목 제외) + CPU/RAM."""
        try:
            from weather_module import (
                ask_location_on_boot, build_auto_startup_briefing,
                build_fast_startup_greeting, build_location_weather_followup,
                load_user_city, startup_location_prompt, _fast_boot_enabled as _wm_fast,
            )
            from startup_briefing import (
                build_startup_fx_briefing, build_startup_fx_overlay,
                build_startup_resource_line,
                startup_fx_enabled, startup_resource_voice_enabled,
            )

            def _speak_block(text: str, tag: str):
                if is_shutting_down():
                    return
                text = polish_for_voice(text or "")
                if not text or is_shutting_down():
                    return
                chat_log.add("jarvis", text)
                log("INFO", f"🌅 {tag}: {text[:80]}...")
                speak_text(text)

            if is_shutting_down():
                return
            if ask_location_on_boot() and not load_user_city():
                msg = startup_location_prompt()
                chat_log.add("system", "📍 City voice input mode")
                _speak_block(msg, "City Input")
                return
            if _wm_fast() or _fast_boot_enabled():
                _speak_block(build_fast_startup_greeting(), "Greeting")
                if not is_shutting_down():
                    _speak_block(build_location_weather_followup(try_gps=True), "Location")
            else:
                _speak_block(build_auto_startup_briefing(), "Greeting")
            if not is_shutting_down() and startup_fx_enabled():
                fx_msg = build_startup_fx_briefing()
                fx_hud = build_startup_fx_overlay()
                if fx_hud and not is_shutting_down():
                    overlay.set_data(fx_hud, duration=45, alert=False)
                _speak_block(fx_msg, "FX")
            if not is_shutting_down() and startup_resource_voice_enabled():
                _speak_block(build_startup_resource_line(), "System")
        except Exception as e:
            log("WARN", f"시작 인사 실패: {e}")

    def emergency_voice_briefing(message):
        """긴급/후속 브리핑(뉴스/지정가/교차검증) — 스트리밍 TTS로 브리핑.
        음성 게이트(다른 발화와 겹침 방지)는 tts_pipeline.start() 내부에서 확보/반환한다."""
        try:
            message = polish_for_voice(message or "")
            if not message:
                return
            # start() 가 wait_audio_idle + begin_speech 로 다른 발화가 끝날 때까지 대기 후 점유.
            # 확보 실패(다른 발화 지속) 시 False → 이번 브리핑은 스킵(호출부에서 requeue됨).
            if not tts_pipeline.start():
                log("WARN", "다른 음성 재생 중 — 브리핑 대기/스킵")
                return
            for ch in message:
                tts_pipeline.feed(ch)
            tts_pipeline.finish()
            tts_pipeline.wait_done(timeout=30)
        except Exception as e:
            log("ERR", f"긴급 브리핑 오류: {e}")

    def show_warning_on_screen(table, msg, *, alert_text=None, dur=26,
                               system_line=None, push_text=None, push_ctx=None, log_chat=True):
        """경고를 음성 없이 화면(HUD)으로만 설명한다. 상태/발화와 무관하게 즉시 표시.
        · 상단 긴급 배너(alert_text) + 설명문(alert_message=msg) 을 화면에 렌더.
        · 외출 중이면 모바일 푸시는 유지(원격 통지는 음성이 아님)."""
        try:
            t = dict(table) if isinstance(table, dict) else {"type": "info", "title": alert_text or "경고"}
            if alert_text:
                t["alert_text"] = alert_text
            if msg:
                t["alert_message"] = msg
            overlay.set_data(t, dur, alert=True)
        except Exception as e:
            log("WARN", f"경고 화면 표시 실패: {e}")
        if log_chat and system_line:
            chat_log.add("system", system_line)
        if log_chat and msg:
            chat_log.add("jarvis", msg)
        if push_text:
            try:
                push_if_away(push_text, context=push_ctx or {})
            except Exception:
                pass

    # ── 실시간 위험·자산 분석 알림 파이프라인 (risk_engine + fds) ──
    alert_rt = None
    if os.getenv("ALERT_PIPELINE_ENABLED", "1").strip().lower() not in ("0", "false", "off"):
        try:
            alert_rt = init_runtime(create_jarvis_runtime(
                overlay_fn=lambda table, dur, alert=False: overlay.set_data(table, dur, alert=alert),
                push_fn=push_if_away,
                logger=log,
            ))
            _ap_st = alert_rt.stats()
            log("OK", f"🛡 Alert Pipeline ON — FDS={'ON' if alert_rt.fds_engine else 'OFF'} "
                      f"| bridge_pending={_ap_st['bridge']['pending']}")
            if alert_rt.fds_engine:
                set_fds_engine(alert_rt.fds_engine)
        except Exception as e:
            log("WARN", f"Alert Pipeline 초기화 실패: {e}")

    # Alert Pipeline 없어도 strict/maximum 이면 독립 FDS 엔진 부착
    if alert_rt is None or alert_rt.fds_engine is None:
        try:
            from security_policy import fds_tools_enabled
            if fds_tools_enabled():
                from fds import FDSEngine, FDSConfig
                _standalone_fds = FDSEngine(FDSConfig())
                set_fds_engine(_standalone_fds)
                log("OK", "🔒 독립 FDS 엔진 활성 (도구 실행 게이트)")
        except Exception as e:
            log("WARN", f"독립 FDS 초기화 실패: {e}")

    # ── 외부 미디어 스트리밍·오디오 파싱 파이프라인 ──
    media_rt = None
    if os.getenv("MEDIA_PIPELINE_ENABLED", "1").strip().lower() not in ("0", "false", "off"):
        try:
            media_rt = init_media_runtime(create_media_runtime(logger=log))
            set_media_runtime(media_rt)
            _m_st = media_rt.stats()
            log("OK", f"📡 Media Pipeline ON — queued={_m_st['worker'].get('queued', 0)}")
        except Exception as e:
            log("WARN", f"Media Pipeline 초기화 실패: {e}")
            set_media_runtime(None)

    def play_interruptible(audio):
        """비스트리밍 재생 + 인터럽트 (폴백용).
        macOS에서는 VAD용 2번째 마이크 스트림이 CoreAudio 데드락을 유발하므로 재생만 수행."""
        if not audio:
            return
        # 다른 발화(스트리밍 응답/알림 브리핑)와 목소리가 겹치지 않게 전역 게이트 확보
        if not wait_audio_idle(tts_pipeline=tts_pipeline, player=player, timeout=180):
            log("WARN", "다른 음성 재생 대기 초과 — 재생 스킵")
            return
        if not begin_speech(timeout=180):
            log("WARN", "음성 게이트 확보 실패 — 재생 스킵")
            return
        try:
            use_vad = sys.platform != "darwin"
            if use_vad:
                vad.start(recorder.pa)
            pt=threading.Thread(target=player.play,args=(audio,),daemon=True); pt.start()
            deadline=time.time()+PLAY_TIMEOUT
            while player.is_playing and time.time()<deadline:
                if use_vad and vad.check_voice():
                    player.stop()
                    interrupt_flag.set()
                    break
                time.sleep(0.05)
                if use_vad:
                    hud.push_audio_level(vad.last_rms)
            if player.is_playing:
                player.stop()
            if use_vad:
                vad.stop()
            pt.join(timeout=2)
        finally:
            end_speech()
            try:
                Path(audio).unlink(missing_ok=True)
            except Exception:
                pass

    # 시작 인사: 위치 기반 현지 시간대(아침/저녁) 음성 — 부팅을 막지 않게 백그라운드 실행
    threading.Thread(target=startup_greeting, daemon=True).start()

    prev_state=state; state_since=time.time()
    STUCK_TIMEOUT=60.0  # PROCESSING/SPEAKING 비정상 고착 시 강제 IDLE 복구 (초)
    boot_ts=time.time(); GREET_BOOT_SUPPRESS=45.0  # 부팅 직후 종목·매크로 음성 억제 — 환율 시작 인사만

    def _boot_voice_quiet() -> bool:
        return time.time() - boot_ts < GREET_BOOT_SUPPRESS
    idle_tracker = IdleTracker()
    _loop_min_interval = 1.0 / _fps_cap
    _loop_next = time.monotonic()

    def _begin_recording(reason=""):
        """웨이크 마이크 해제 후 녹음 시작 — CoreAudio 장치 충돌 방지."""
        wake.pause()
        if sys.platform == "darwin":
            time.sleep(0.05)
        recorder.start()
        if reason:
            log("INFO", reason)

    def _gesture_pause():
        nonlocal state
        tts_pipeline.stop()
        player.stop()
        if recorder._recording:
            try:
                recorder.stop()
            except Exception:
                pass
        interrupt_flag.clear()
        wake.resume()
        if state in ("SPEAKING", "PROCESSING", "RECORDING"):
            state = "IDLE"
            chat_log.add("system", "✋ Gesture — Paused")

    def _gesture_mute():
        try:
            from mac_control import set_volume
            res = set_volume(direction="mute")
            chat_log.add("system", res.get("message", "Muted"))
        except Exception as e:
            log("WARN", f"Gesture mute failed: {e}")

    def _gesture_confirm():
        if pending_sel.get("active"):
            ctx = pending_sel.get("ctx") or {}
            cands = ctx.get("candidates") or []
            if cands:
                res = add_target_choice(cands[0], ctx.get("price"), ctx.get("direction"))
                pending_sel["active"] = False
                pending_sel["ctx"] = None
                msg = res.get("message", "Confirmed.")
                chat_log.add("jarvis", msg)
                threading.Thread(target=speak_text, args=(msg,), daemon=True).start()
                return
        chat_log.add("system", "👍 No pending item to confirm")

    def _gesture_play_pause():
        try:
            from mac_control import media_play_pause
            res = media_play_pause()
            chat_log.add("system", res.get("message", "Play/Pause"))
        except Exception as e:
            log("WARN", f"Gesture media toggle failed: {e}")

    def _gesture_command_start() -> bool:
        """검지 유지 → 웨이크워드 없이 음성 명령 녹음."""
        nonlocal state
        if state != "IDLE" or player.is_playing or tts_pipeline.is_playing or recorder._recording:
            return False
        if voice_auth_required() and voice_setup_required(wake.voice_profile):
            chat_log.add("system", "🔒 Maximum Security — Press V to enroll voice before using gesture commands")
            return True
        state = "RECORDING"
        chat_log.add("system", "☝ Gesture command — Speak now (peace sign V or 3s silence to send)")
        _begin_recording("Gesture command recording started")
        return True

    def _gesture_command_stop() -> bool:
        """브이 유지 → 녹음을 끝내고 명령으로 보낸다."""
        nonlocal state
        if state != "RECORDING":
            return False
        state = "PROCESSING"
        try:
            wav = recorder.stop()
        except Exception as e:
            log("WARN", f"Gesture command stop failed: {e}")
            state = "IDLE"
            wake.resume()
            return True
        if wav:
            chat_log.add("system", "✌ Gesture — Sending command")
            dispatch_processing(wav)
        else:
            state = "IDLE"
            wake.resume()
            chat_log.add("system", "✌ Recording empty — command not sent")
        return True

    vg_handler = VoiceGestureHandler(
        on_pause=_gesture_pause,
        on_mute=_gesture_mute,
        on_confirm=_gesture_confirm,
        on_play_pause=_gesture_play_pause,
        on_command_start=_gesture_command_start,
        on_command_stop=_gesture_command_stop,
        log_fn=log,
    )

    _shutdown_evt = threading.Event()
    _window_created = False

    def _on_term_signal(_signum, _frame):
        _shutdown_evt.set()

    try:
        import signal as _signal
        _signal.signal(_signal.SIGINT, _on_term_signal)
        _signal.signal(_signal.SIGTERM, _on_term_signal)
    except Exception:
        pass

    while not _shutdown_evt.is_set():
        if consume_wake_request():
            idle_tracker.touch()
            chat_log.add("system", "▶ JARVIS 앱으로 포커스")

        ret,frame=frame_reader.read()
        if not ret and not camera_pending and not power_save_active():
            break                    # 카메라 영구 종료
        if frame is None:
            time.sleep(0.01)                 # 프레임 타임아웃 시 busy-wait 방지
            continue

        if camera_pending and not power_save_active() and time.time() >= _camera_retry_at:
            _camera_retry_at = time.time() + 5.0
            new_cap, still_pending = _open_camera_with_retry(
                w=_cam_w, h=_cam_h, retries=4, delay=0.35,
            )
            if new_cap is not None and not still_pending:
                try:
                    frame_reader.stop()
                    cap.release()
                except Exception:
                    pass
                cap = new_cap
                camera_pending = False
                _vision_on = vision_enabled()
                _hands_on = _vision_on and hands_enabled()
                frame_reader = FrameReader(cap)
                frame_reader.start()
                if _hands_on:
                    try:
                        hands.close()
                    except Exception:
                        pass
                    hands = mp_hands.Hands(
                        model_complexity=hand_complexity(), max_num_hands=1,
                        min_detection_confidence=0.7, min_tracking_confidence=0.6,
                    )
                elif _vision_on:
                    try:
                        hands.close()
                    except Exception:
                        pass
                    hands = _HandsStub()
                if _vision_on and face_rec.enabled:
                    face_worker = FaceWorker(
                        face_rec, face_event_q, interval=face_worker_interval(),
                        gate=lambda: state != "RECORDING",
                    )
                    face_worker.start()
                log("OK", "카메라 연결됨 — 웹캠 활성화")
                chat_log.add("system", "📷 카메라 연결됨")

        frame=cv2.flip(frame,1); frame_count+=1
        if camera_pending:
            frame = _draw_camera_permission_banner(frame)
        # 차트(S키)/얼굴 도구용 — 비전 OFF여도 S키 비전 분석은 clean_frame 필요
        clean_frame=frame.copy() if _vision_on else frame
        if _vision_on:
            set_current_frame(clean_frame)

        # 오디오 레벨 → HUD 파형
        if recorder._recording:
            hud.push_audio_level(recorder.last_rms)
        elif tts_pipeline.is_playing or player.is_playing:
            hud.push_audio_level(3000)  # 말하는 중 시각 효과
        else:
            hud.push_audio_level(0)

        cg="NONE"; gc=False
        if _hands_on:
            rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
            rgb.flags.writeable=False; results=hands.process(rgb); rgb.flags.writeable=True
            if results.multi_hand_landmarks:
                for hl in results.multi_hand_landmarks:
                    mp_draw.draw_landmarks(frame,hl,mp_hands.HAND_CONNECTIONS,
                        mp_styles.get_default_hand_landmarks_style(),
                        mp_styles.get_default_hand_connections_style())

                    if gesture_ctrl.enabled:
                        h_frame,w_frame = frame.shape[:2]
                        gesture_ctrl.process(hl, w_frame, h_frame)
                        frame = gesture_ctrl.draw_overlay(frame, hl)
                    else:
                        h_frame, w_frame = frame.shape[:2]
                        cg = detect_gesture(hl, w_frame, h_frame)
            if cg==last_gesture: gesture_hold=min(gesture_hold+1,GESTURE_THRESHOLD+5)
            else: gesture_hold=0; last_gesture=cg
            gc=gesture_hold>=GESTURE_THRESHOLD
            vg_handler.observe(cg)
            if gc and not gesture_ctrl.enabled:
                if vg_handler.handle(cg):
                    gesture_hold = 0
                    last_gesture = "NONE"
                    gc = False
            _g_evt=overlay.handle_gesture(cg, gc)
            if _g_evt:
                log("INFO", f"HUD 제스처 → {_g_evt[0]} (radar={'ON' if _g_evt[1] else 'OFF'})")

        if _vision_on and face_rec.enabled and state!="RECORDING":
            face_worker.submit(frame)

        # ── 외출(자리비움) — 얼굴 인식 활성 시에만 자동 AWAY ──
        if _vision_on and face_rec.last_results:
            away_state["last_seen"] = time.time()
            if away_state["away"] and not away_state["manual"]:
                away_state["away"] = False; link.set_away(False)
                log("INFO", "🏠 사용자 복귀 감지 — AWAY 해제")
                chat_log.add("system", "🏠 복귀 감지 — 외출 모드 해제")
        elif _vision_on and not away_state["away"] and not away_state["manual"]:
            if time.time() - away_state["last_seen"] > AWAY_TIMEOUT_SEC:
                away_state["away"] = True; link.set_away(True)
                log("INFO", f"🚶 {AWAY_TIMEOUT_SEC:.0f}초 미검출 — AWAY 자동 전환(모바일 푸시 활성)")
                chat_log.add("system", "🚶 외출 모드 — 위험 발생 시 모바일로 푸시합니다")

        # ── '한 번에 한 내용만' 가드: 이번 루프 사이클에 자율 발화를 1건만 허용 ──
        #  여러 자율 소스(승인·인사·종목안내·모니터·뉴스·지정가·고래·소셜·IR·주가·캠퍼스·매크로)가
        #  같은 사이클에 동시에 음성 스레드를 띄워 겹쳐 말하던 문제를 차단한다. 말하지 못한
        #  이벤트는 가능한 경우 각 소스 큐로 되돌려(requeue/_enqueue) 다음 사이클에 순차 처리.
        spoke_this_cycle = False

        # ── 모바일 원격 리밸런싱 승인 수신 → 자율 실행 ──
        for _appr in link.get_approval_events():
            _ctx = _appr.get("context") or {}
            _hc = float(_ctx.get("haircut_pct", 10) or 10)
            log("INTR", f"✅ 원격 승인 실행 → 리밸런싱 {_hc:.0f}% (code={_appr.get('code')})")
            try:
                _rb = portfolio.apply_rebalance(_hc, reason=_ctx.get("reason", "원격 승인 리밸런싱"))
            except Exception as _e:
                _rb = {"success": False, "message": str(_e)}
            if _rb.get("success"):
                _bmsg = _rb.get("briefing", "리밸런싱을 완료했습니다.")
                chat_log.add("system", f"📱 원격 승인 리밸런싱 실행: {_rb['raised_cash_krw']:,.0f}원 현금 확보")
                chat_log.add("jarvis", _bmsg); last_answer=_bmsg
                link.publish("rebalance_done", {"summary": _rb})
                if (not spoke_this_cycle and not away_state["away"]
                        and not player.is_playing and not tts_pipeline.is_playing):
                    threading.Thread(target=speak_text, args=(_bmsg,), daemon=True).start()
                    spoke_this_cycle = True
            else:
                chat_log.add("system", f"리밸런싱 실패: {_rb.get('message','')}")

        # ── ZFoldMacBridge — 모바일 알림 TTS / 음성 명령 ──
        for _mob in link.get_mobile_inbound_events():
            if _mob.get("process_as_user") and (_mob.get("text") or "").strip():
                log("INFO", f"📱 모바일 음성 명령: {_mob['text'][:80]}")
                threading.Thread(
                    target=process_text_async,
                    args=(_mob["text"].strip(),),
                    daemon=True,
                ).start()
                continue
            if _mob.get("read_aloud") and not spoke_this_cycle:
                tts = (_mob.get("tts_text") or _mob.get("text") or "").strip()
                if tts and state == "IDLE" and not player.is_playing and not tts_pipeline.is_playing:
                    chat_log.add("system", f"📱 알림: {_mob.get('title', '')} — {tts[:120]}")
                    last_answer = tts
                    threading.Thread(target=speak_text, args=(tts,), daemon=True).start()
                    spoke_this_cycle = True

        if (_vision_on and not spoke_this_cycle and not face_event_q.empty() and state=="IDLE"
                and not player.is_playing and not tts_pipeline.is_playing):
            evt=face_event_q.get_nowait()
            # 부팅 직후엔 시작 인사(시간/위치)만 — 얼굴 자동 인사는 억제 (경고는 통과)
            if evt.get("type")=="greet" and time.time()-boot_ts < GREET_BOOT_SUPPRESS:
                log("FACE",f"부팅 인사 억제: {evt.get('name')}")
            else:
                last_answer=evt["message"]
                chat_log.add("jarvis", evt["message"])
                log("FACE",f"{evt['type']}:{evt['message']}")
                threading.Thread(target=speak_text,args=(evt["message"],),daemon=True).start()
                spoke_this_cycle = True

        # ── 종목 후보 선택 (오인식 → 음성 번호) ──
        _sel_idle = (state=="IDLE" and not player.is_playing
                     and not tts_pipeline.is_playing and not recorder._recording)
        if _sel_idle and not pending_sel["active"]:
            _sel=take_pending_selection()
            if _sel and _sel.get("candidates"):
                pending_sel["retries"]=0
                _arm(_sel["query"], _sel["price"], _sel["direction"], _sel["candidates"][:5])
        elif _sel_idle and pending_sel["active"] and pending_sel["rearm"]:
            _c=pending_sel["ctx"]
            _arm(_c["query"], _c["price"], _c["direction"], _c["candidates"])
        elif _sel_idle and pending_sel["active"] and pending_sel["await_record"]:
            # 안내 완료 → 사용자 번호 답변 자동 녹음
            pending_sel["await_record"]=False
            state="RECORDING"
            _begin_recording()
            chat_log.add("system","🔢 후보 번호를 말씀해주세요 (1~5, 6=직접입력)")

        # ── 직접 입력(6번 타이핑) 결과 음성 안내 ──
        if (not _sel_result_q.empty() and not spoke_this_cycle and state=="IDLE"
                and not player.is_playing and not tts_pipeline.is_playing):
            _m=_sel_result_q.get_nowait(); last_answer=_m
            chat_log.add("jarvis", _m)
            threading.Thread(target=speak_text,args=(_m,),daemon=True).start()
            spoke_this_cycle = True

        # ── 타이핑 명령(T키) 처리 — 입력이 실제 도착했을 때만 PROCESSING 전환 ──
        if not _text_cmd_q.empty() and state=="IDLE" and not player.is_playing and not tts_pipeline.is_playing:
            _typed=_text_cmd_q.get_nowait()
            if _typed:
                state="PROCESSING"; wake.pause()
                threading.Thread(target=process_text_async,args=(_typed,),daemon=True).start()
            else:
                chat_log.add("system", "입력이 없어 취소했습니다.")

        # ── 백그라운드 모니터 이벤트 ──
        monitor_events = bg_monitor.get_events()
        for idx, mevt in enumerate(monitor_events):
            msg = mevt.get("message", "")
            if (msg and not spoke_this_cycle and state=="IDLE"
                    and not player.is_playing and not tts_pipeline.is_playing):
                last_answer = msg
                chat_log.add("system", f"📡 {mevt.get('source','')}: {mevt.get('type','')}")
                chat_log.add("jarvis", msg)
                state = "SPEAKING"; spoke_this_cycle = True
                threading.Thread(target=speak_text, args=(msg,), daemon=True).start()
                bg_monitor.requeue(monitor_events[idx+1:])
                break
            elif msg:
                bg_monitor.requeue(monitor_events[idx:])
                break

        # ── 실시간 뉴스 이벤트 (호재/악재 감성 분석) ──
        news_events = news_analyzer.get_events()
        if _boot_voice_quiet():
            for _ne in news_events:
                news_analyzer._enqueue(_ne)
            news_events = []
        for nevt in news_events:
            msg = nevt.get("message", "")
            if nevt.get("urgent"):
                # 초강력 호재/악재(|score|>=7): TTS 인터럽트 + 테두리 깜빡임 + 표 + 즉시 긴급 브리핑.
                # 단, 사용자가 녹음 중(RECORDING)일 때는 마이크 입력을 끊지 않기 위해 다음 사이클로 미룬다.
                if state == "RECORDING":
                    news_analyzer._enqueue(nevt)  # 녹음 종료 후 재처리
                    continue
                # '한 번에 한 내용만': 이번 사이클에 이미 무언가 말하기 시작했다면 다음 사이클로 미룬다.
                #  (직전 사이클부터 재생 중이던 발화는 아래에서 중단(stop)하고 긴급 뉴스로 교체)
                if spoke_this_cycle:
                    news_analyzer._enqueue(nevt)
                    continue
                log("INTR", f"🚨 긴급 뉴스 인터럽트: {nevt['name']} ({nevt['score']:+d})")
                tts_pipeline.stop()                  # 자비스가 하던 말 중단 (TTS Interrupt)
                if player.is_playing: player.stop()
                interrupt_flag.clear()
                # 화면 테두리 깜빡임 + 감성 점수 표 투사
                overlay.set_data(nevt["overlay_table"], 25, alert=True)
                last_answer = msg
                chat_log.add("system", f"🚨 긴급 뉴스: {nevt['name']} ({nevt['score']:+d})")
                chat_log.add("jarvis", msg)
                # 외출 중이면 텔레그램으로도 즉시 푸시
                push_if_away(f"🚨 긴급 뉴스 — {nevt['name']} ({nevt['score']:+d})\n\n{msg}",
                             context={"kind": "news", "name": nevt['name'], "score": nevt['score']})
                state = "SPEAKING"; spoke_this_cycle = True
                threading.Thread(target=emergency_voice_briefing, args=(msg,), daemon=True).start()
            else:
                # 일반 CRITICAL: 대기 상태에서만 차분히 브리핑 (사이클당 1건)
                if (msg and not spoke_this_cycle and state=="IDLE"
                        and not player.is_playing and not tts_pipeline.is_playing):
                    overlay.set_data(nevt["overlay_table"], 20)
                    last_answer = msg
                    chat_log.add("system", f"📰 뉴스: {nevt['name']} ({nevt['score']:+d})")
                    chat_log.add("jarvis", msg)
                    state = "SPEAKING"; spoke_this_cycle = True
                    threading.Thread(target=speak_text, args=(msg,), daemon=True).start()
                elif msg:
                    news_analyzer._enqueue(nevt)  # 못 말한 일반 뉴스는 다음 사이클로 보존

        # ── 실시간 지정가/지표 알림 (RSI 과매도·볼린저 하단 돌파) ──
        alert_events = price_alert.get_events()
        for idx, aevt in enumerate(alert_events):
            amsg = aevt.get("message", "")
            if not amsg:
                continue
            if aevt.get("source") == "price_alert":
                try:
                    from price_alert import technical_signal_alerts_enabled
                except ImportError:
                    technical_signal_alerts_enabled = lambda: False  # noqa: E731
                if not technical_signal_alerts_enabled():
                    continue
            if _boot_voice_quiet():
                price_alert.requeue(alert_events[idx:])
                break
            # HUD 적색 펄스 배너 + 시그널 표 투사
            table = dict(aevt["overlay_table"]); table["alert_text"] = aevt["alert_text"]
            _push = f"🚨 매수 시그널 — {aevt['name']} ({aevt['triggers']})\n\n{amsg}"
            _pctx = {"kind": "price_alert", "name": aevt['name'], "triggers": aevt['triggers']}
            if warnings_screen_only():
                # 경고는 음성 없이 화면으로만 설명 (상태 무관 즉시 표시)
                log("INTR", f"🚨 지정가 알림(화면): {aevt['name']} ({aevt['triggers']})")
                show_warning_on_screen(table, amsg, alert_text=aevt["alert_text"], dur=25,
                    system_line=f"🚨 매수 시그널(화면): {aevt['name']} — {aevt['triggers']}",
                    push_text=_push, push_ctx=_pctx)
                last_answer = amsg
                continue
            # IDLE(대기) 상태에서만 먼저 말 걸기 — 녹음/처리/발화 중에는 다음 사이클로 미룸
            if (not spoke_this_cycle and state=="IDLE"
                    and not player.is_playing and not tts_pipeline.is_playing):
                log("INTR", f"🚨 지정가 알림: {aevt['name']} ({aevt['triggers']})")
                overlay.set_data(table, 25, alert=True)
                last_answer = amsg
                chat_log.add("system", f"🚨 매수 시그널: {aevt['name']} — {aevt['triggers']}")
                chat_log.add("jarvis", amsg)
                # 외출 중이면 텔레그램으로도 즉시 푸시
                push_if_away(_push, context=_pctx)
                state = "SPEAKING"; spoke_this_cycle = True
                # Fish Audio TTS 강제 트리거 (스트리밍 파이프라인 가로채기)
                threading.Thread(target=emergency_voice_briefing, args=(amsg,), daemon=True).start()
            else:
                # 다른 작업(녹음/처리/발화) 중 → 남은 이벤트 전부 큐로 되돌려 다음 IDLE 사이클에 처리
                price_alert.requeue(alert_events[idx:])
                break

        # ── 고래/정치인 대량 매도 경고 (하루 1회 백그라운드 감시) ──
        whale_events = whale_tracker.get_events()
        for idx, wevt in enumerate(whale_events):
            wmsg = wevt.get("message", "")
            if not wmsg:
                continue
            table = dict(wevt["overlay_table"]); table["alert_text"] = wevt.get("alert_text", "🚨 WHALE SELL ALERT")
            if warnings_screen_only():
                log("INTR", "🚨 고래 대량 매도 경고(화면)")
                show_warning_on_screen(table, wmsg, alert_text=table["alert_text"], dur=30,
                    system_line="🚨 고래 매도 시그널 포착(화면)")
                last_answer = wmsg
                continue
            if (not spoke_this_cycle and state=="IDLE"
                    and not player.is_playing and not tts_pipeline.is_playing):
                log("INTR", "🚨 고래 대량 매도 경고")
                overlay.set_data(table, 30, alert=True)
                last_answer = wmsg
                chat_log.add("system", "🚨 고래 매도 시그널 포착")
                chat_log.add("jarvis", wmsg)
                state = "SPEAKING"; spoke_this_cycle = True
                threading.Thread(target=emergency_voice_briefing, args=(wmsg,), daemon=True).start()
            else:
                whale_tracker.requeue(whale_events[idx:])
                break

        # ── 소셜 밈 주식 급증 경보 (Reddit 언급량 + 거래량 스파이크 + AI 위험도) ──
        social_events = social_detector.get_events()
        for idx, sevt in enumerate(social_events):
            smsg = sevt.get("message", "")
            if not smsg:
                continue
            table = dict(sevt["overlay_table"]); table["alert_text"] = sevt.get("alert_text", "⚠ 밈 주식 급증 경보")
            if warnings_screen_only():
                log("INTR", f"⚠ 소셜 스파이크 경보(화면): {sevt.get('ticker','')}")
                show_warning_on_screen(table, smsg, alert_text=table["alert_text"], dur=28,
                    system_line=f"⚠ 소셜 스파이크(화면): {sevt.get('ticker','')}")
                last_answer = smsg
                continue
            if (not spoke_this_cycle and state=="IDLE"
                    and not player.is_playing and not tts_pipeline.is_playing):
                log("INTR", f"⚠ 소셜 스파이크 경보: {sevt.get('ticker','')}")
                overlay.set_data(table, 28, alert=True)
                last_answer = smsg
                chat_log.add("system", f"⚠ 소셜 스파이크: {sevt.get('ticker','')}")
                chat_log.add("jarvis", smsg)
                state = "SPEAKING"; spoke_this_cycle = True
                threading.Thread(target=emergency_voice_briefing, args=(smsg,), daemon=True).start()
            else:
                social_detector.requeue(social_events[idx:])
                break

        # ── IR 라이브 통역 5분 요약 보드 ──
        ir_events = ir_interpreter.get_events()
        for idx, ievt in enumerate(ir_events):
            imsg = ievt.get("message", "")
            if ievt.get("overlay_table"):
                # 요약 보드는 말하는 중이어도 최신 내용으로 HUD를 갱신한다.
                overlay.set_data(ievt["overlay_table"], 320)
            if (imsg and not spoke_this_cycle and state=="IDLE"
                    and not player.is_playing and not tts_pipeline.is_playing):
                log("INFO", f"IR 요약 업데이트: {ievt.get('company','')}")
                last_answer = imsg
                chat_log.add("system", f"📊 IR 요약: {ievt.get('company','')}")
                chat_log.add("jarvis", imsg)
                state = "SPEAKING"; spoke_this_cycle = True
                threading.Thread(target=emergency_voice_briefing, args=(imsg,), daemon=True).start()
            elif imsg:
                ir_interpreter.requeue(ir_events[idx:])
                break

        # ── 동적 주가 알림 (사용자 등록 조건 — 급락/급등/목표가 돌파) ──
        smon_events = stock_monitor.get_events()
        for idx, sevt in enumerate(smon_events):
            smsg = sevt.get("message", "")
            if not smsg:
                continue
            table = dict(sevt["overlay_table"]); table["alert_text"] = sevt["alert_text"]
            if warnings_screen_only():
                log("INTR", f"🚨 주가 알림(화면): {sevt['name']} ({sevt.get('condition','')})")
                show_warning_on_screen(table, smsg, alert_text=sevt["alert_text"], dur=25, log_chat=False)
                chat_log.add("system", f"🚨 주가 알림(화면): {sevt['name']} — {sevt['alert_text']}")
                if sevt.get("news_summary"):
                    chat_log.add("system", f"🧠 AI 원인: {sevt['news_summary']}")
                for h in sevt.get("headlines", [])[:2]:
                    chat_log.add("system", f"📰 {h.get('title','')[:55]}")
                chat_log.add("jarvis", smsg)
                last_answer = smsg
                continue
            if _boot_voice_quiet():
                stock_monitor.requeue(smon_events[idx:])
                break
            if (not spoke_this_cycle and state=="IDLE"
                    and not player.is_playing and not tts_pipeline.is_playing):
                log("INTR", f"🚨 주가 알림: {sevt['name']} ({sevt.get('condition','')})")
                overlay.set_data(table, 25, alert=True)
                last_answer = smsg
                chat_log.add("system", f"🚨 주가 알림: {sevt['name']} — {sevt['alert_text']}")
                # AI 뉴스 원인 요약이 있으면 시스템 메시지로 표시
                if sevt.get("news_summary"):
                    chat_log.add("system", f"🧠 AI 원인: {sevt['news_summary']}")
                # 관련 헤드라인 최대 2건 chat_log 노출 (제목 55자 트런케이트)
                for h in sevt.get("headlines", [])[:2]:
                    chat_log.add("system", f"📰 {h.get('title','')[:55]}")
                chat_log.add("jarvis", smsg)
                state = "SPEAKING"; spoke_this_cycle = True
                threading.Thread(target=emergency_voice_briefing, args=(smsg,), daemon=True).start()
            else:
                stock_monitor.requeue(smon_events[idx:])
                break

        # ── 캠퍼스 행정 감시 (스토니브룩 Gmail/SOLAR — I-20/비자/Hold/수강신청 등) ──
        campus_events = campus_agent.get_events()
        for idx, cevt in enumerate(campus_events):
            cmsg = cevt.get("message", "")
            if (cmsg and not spoke_this_cycle and state=="IDLE"
                    and not player.is_playing and not tts_pipeline.is_playing):
                urgent = cevt.get("urgent", False)
                log("INTR", f"🎓 캠퍼스 {'긴급 ' if urgent else ''}알림: "
                            f"[{cevt.get('category','')}] {cevt.get('subject','')[:40]}")
                table = dict(cevt["overlay_table"]); table["alert_text"] = cevt["alert_text"]
                overlay.set_data(table, 26, alert=True)
                last_answer = cmsg
                chat_log.add("system", f"🎓 캠퍼스 알림: {cevt.get('dept','')} — {cevt['alert_text']}")
                chat_log.add("system", f"✉ {cevt.get('subject','')[:55]}")
                chat_log.add("jarvis", cmsg)
                state = "SPEAKING"; spoke_this_cycle = True
                threading.Thread(target=emergency_voice_briefing, args=(cmsg,), daemon=True).start()
            elif cmsg:
                campus_agent.requeue(campus_events[idx:])
                break

        # ── Alert Pipeline (risk_engine + fds) — IDLE 시 TTS/AudioCue/push 1건 ──
        if alert_rt:
            for _cue in alert_rt.audio_cues.drain():
                log("INFO", f"🎵 AudioCue motif={_cue.get('motif')} "
                           f"intensity={_cue.get('intensity')} bpm={_cue.get('tempo_bpm')}")
            _ap_screen = warnings_screen_only()
            # 화면 전용이면 상태와 무관하게 즉시 화면 통지, 아니면 기존처럼 IDLE 게이트
            _ap_can = _ap_screen or (not spoke_this_cycle and state == "IDLE"
                       and not player.is_playing and not tts_pipeline.is_playing)
            if _ap_screen:
                _ap_out = alert_rt.poll_idle(
                    can_speak=_ap_can,
                    on_speak=lambda m: show_warning_on_screen(
                        None, m, alert_text="🛡 위험·자산 알림", dur=24,
                        system_line="🛡 위험·자산 알림(화면)"),
                    on_emergency=lambda m: show_warning_on_screen(
                        None, m, alert_text="🛡 위험·자산 경고", dur=26,
                        system_line="🛡 위험·자산 경고(화면)"),
                    on_push=push_if_away,
                )
                if _ap_out and _ap_out.get("tts"):
                    last_answer = _ap_out["tts"]
            else:
                _ap_out = alert_rt.poll_idle(
                    can_speak=_ap_can,
                    on_speak=lambda m: threading.Thread(target=speak_text, args=(m,), daemon=True).start(),
                    on_emergency=lambda m: threading.Thread(
                        target=emergency_voice_briefing, args=(m,), daemon=True).start(),
                    on_push=push_if_away,
                )
                if _ap_out and _ap_can:
                    spoke_this_cycle = True
                    state = "SPEAKING"
                    if _ap_out.get("tts"):
                        last_answer = _ap_out["tts"]
                        chat_log.add("system", "🛡 위험·자산 알림")
                        chat_log.add("jarvis", last_answer)

        # ── Media Pipeline — 캡처·전처리 완료 HUD / 시스템 컨텍스트 / IDLE TTS ──
        if media_rt:
            for _mart in media_rt.drain_prep_queue():
                register_media_artifact(_mart)
            media_events = media_rt.get_events()
            for idx, mevt in enumerate(media_events):
                mtype = mevt.get("type", "")
                meta = mevt.get("meta") or {}
                if mtype == "media_ready":
                    table = build_media_overlay_table(mevt)
                    overlay.set_data(table, 28, alert=False)
                    if mevt.get("artifact"):
                        register_media_artifact(mevt["artifact"])
                    _mtitle = meta.get("title") or "Media"
                    _mbrief = (
                        f"{USER_DISPLAY_NAME}, audio preprocessing for '{_mtitle}' is complete. "
                        f"It is ready for transcription or vocal analysis."
                    )
                    if (not spoke_this_cycle and state == "IDLE"
                            and not player.is_playing and not tts_pipeline.is_playing):
                        log("INFO", f"🎵 Media ready: {mevt.get('job_id')}")
                        last_answer = _mbrief
                        chat_log.add("system", f"🎵 Media preprocessing complete — {mevt.get('job_id')}")
                        chat_log.add("jarvis", _mbrief)
                        state = "SPEAKING"
                        spoke_this_cycle = True
                        threading.Thread(target=speak_text, args=(_mbrief,), daemon=True).start()
                    else:
                        media_rt.requeue(media_events[idx:])
                        break
                elif mtype == "media_failed":
                    log("WARN", f"Media job failed: {mevt.get('message', '')[:120]}")

        # ── 선제적 매크로 가드레일 위험 브리핑 (요청 없이 먼저 말 걸기) ──
        macro_events = macro_guard.get_events()
        for idx, gevt in enumerate(macro_events):
            gmsg = gevt.get("message", "")
            # 전역 '경고는 화면으로만' 설정이 켜져 있으면 음성 없이 화면 설명으로 강제
            screen_only = gevt.get("screen_only", False) or warnings_screen_only()

            # 화면(HUD) 경고 — 녹음/재생 중이어도 즉시 표시
            if gevt.get("overlay_table"):
                table = dict(gevt["overlay_table"])
                table["alert_text"] = (
                    gevt.get("alert_text") or table.get("alert_text") or "⚠ 매크로 경고"
                )
                if gmsg:
                    table["alert_message"] = gmsg   # 경고 설명문을 화면에 렌더
                _dur = int(gevt.get("overlay_duration", 60))
                overlay.set_data(table, _dur, alert=True)
                _fx_tag = "환율" if "환율" in table.get("alert_text", "") else "매크로"
                chat_log.add("system", f"📊 {_fx_tag} 경고 — 화면에 표시됨 ({gevt.get('risk_level', '')})")
                if screen_only:
                    _disp = gmsg or table.get("alert_text")
                    if _disp:
                        last_answer = _disp
                        chat_log.add("jarvis", _disp)

            if screen_only:
                continue

            if _boot_voice_quiet():
                macro_guard.requeue(macro_events[idx:])
                break

            if (gmsg and not spoke_this_cycle and state=="IDLE"
                    and not player.is_playing and not tts_pipeline.is_playing):
                log("INTR", f"🚨 매크로 위험 격상 선제 브리핑 (이전={gevt.get('prev_level')})")
                last_answer = gmsg
                chat_log.add("system", "🚨 매크로 가드레일: 시장 위험 등급 격상")
                chat_log.add("jarvis", gmsg)
                # 외출 중이면 모바일로도 즉시 푸시 + 원격 리밸런싱 승인 코드 발급
                _pmsg = polish_for_voice(
                    "지금 밖에 있을 수도 있는데, 시장 쪽 위험 신호가 올라왔어.\n\n" + gmsg
                )
                push_if_away(_pmsg, context={"rebalance": True, "haircut_pct": 10,
                                             "reason": "매크로 위험 격상",
                                             "risk_level": gevt.get("risk_level", "위험")})
                state = "SPEAKING"; spoke_this_cycle = True
                threading.Thread(target=emergency_voice_briefing, args=(gmsg,), daemon=True).start()
            elif gmsg:
                macro_guard.requeue(macro_events[idx:])
                break

        # ── 주식 교차검증 후속 브리핑 (예비 답변 후 정밀 교차검증+멀티AI 완료 시 정정/확정) ──
        cvf_events = cv_followup.get_events()
        for idx, cvevt in enumerate(cvf_events):
            cvmsg = cvevt.get("message", "")
            if _boot_voice_quiet() and cvmsg:
                cv_followup.requeue(cvf_events[idx:])
                break
            if (cvmsg and not spoke_this_cycle and state=="IDLE"
                    and not player.is_playing and not tts_pipeline.is_playing):
                log("INTR", f"🔎 교차검증 후속 브리핑: {cvevt.get('name','')}")
                last_answer = cvmsg
                chat_log.add("system", f"🔎 교차검증·AI 정밀 분석 완료 — {cvevt.get('name','')}")
                chat_log.add("jarvis", cvmsg)
                state = "SPEAKING"; spoke_this_cycle = True
                threading.Thread(target=emergency_voice_briefing, args=(cvmsg,), daemon=True).start()
            elif cvmsg:
                cv_followup.requeue(cvf_events[idx:])
                break

        if interrupt_flag.is_set():
            interrupt_flag.clear()
            tts_pipeline.stop()  # 스트리밍 재생 중단
            state="RECORDING"
            _begin_recording()

        # ── 웨이크워드 트리거 ──
        if wake.triggered and state=="IDLE" and not power_save_active():
            wake.reset()
            idle_tracker.touch()
            state="RECORDING"
            chat_log.add("system", "🔔 Voice verified — Recording started")
            _begin_recording()

        # ── 상태 전이 ──
        trained = wake.voice_profile.trained

        if state=="IDLE":
            # 오디오 재생 중엔 재개 금지 — TTS 에코가 웨이크워드로 오감지되는 것 방지
            if not player.is_playing and not tts_pipeline.is_playing:
                wake.resume()
            if _hands_on and not trained and not voice_auth_required():
                # 미학습: 주먹 제스처 녹음 시작 (음성 제스처 OFF일 때만)
                if gc and cg=="FIST" and not voice_gestures_enabled():
                    state="RECORDING"; gesture_hold=0
                    _begin_recording()

        elif state=="RECORDING":
            # 정적으로 녹음이 자동 중지됨 → 즉시 PROCESSING 전환 (불필요한 'One moment' 발화로 본 답변이 밀리는 현상 제거)
            if recorder.auto_stopped:
                waited = time.time() - (recorder.auto_stopped_at or time.time())
                if waited >= PROCESS_DELAY:
                    state="PROCESSING"
                    if os.getenv("JARVIS_SPEAK_WAIT_PROMPT", "0").strip().lower() in ("1", "true", "yes", "on"):
                        threading.Thread(target=speak_text, args=(tr("one_moment"),), daemon=True).start()
                    wav=recorder.stop()
                    if wav:
                        chat_log.add("system", f"⏹ {recorder.SILENCE_TIMEOUT:g}s silence → Processing command")
                        dispatch_processing(wav)
                    else:
                        state="IDLE"
        elif state=="PROCESSING":
            if not result_q.empty():
                r=result_q.get_nowait()
                if r[0]=="OK":
                    _,last_transcript,last_answer,audio=r
                    state="SPEAKING"
                    # 스트리밍 모드: audio=None, tts_pipeline이 이미 재생 중
                    # 비스트리밍 폴백: audio가 있으면 기존 방식으로 재생
                    if audio:
                        threading.Thread(target=play_interruptible,args=(audio,),daemon=True).start()
                else: state="IDLE"
        elif state=="SPEAKING":
            # 스트리밍 파이프라인 또는 기존 플레이어 종료 확인
            pipeline_done = not tts_pipeline.is_playing
            player_done = not player.is_playing
            # 백그라운드 발화(긴급/후속 브리핑)가 게이트를 잡고 재생 시작 직전일 수 있으므로
            # 게이트가 비었을 때만 IDLE 전환 — 데몬 스레드가 아직 재생 시작 전인 순간에
            # 성급히 IDLE로 돌아가 두 번째 발화가 launch돼 목소리가 겹치는 것을 방지.
            gate_free = not speech_active()
            if pipeline_done and player_done and gate_free and not interrupt_flag.is_set():
                state="IDLE"

        # ── 상태 머신 워치독: PROCESSING/SPEAKING 비정상 고착 강제 복구 ──
        # TTS _play_done 미설정으로 is_playing이 영구 True가 되면 SPEAKING에 고착되고,
        # wake.resume()은 IDLE에서만 호출되므로 웨이크워드가 영구 pause된다. 시간으로 끊어 복구.
        if state!=prev_state:
            prev_state=state; state_since=time.time()
        elif state in ("PROCESSING","SPEAKING") and time.time()-state_since>STUCK_TIMEOUT:
            log("WARN", f"{state} {STUCK_TIMEOUT:.0f}초 초과 고착 — 강제 IDLE 복구")
            chat_log.add("system", f"⚠ {state} 고착 감지 → 복구 (웨이크워드 재개)")
            try: tts_pipeline.stop()
            except: pass
            if player.is_playing:
                try: player.stop()
                except: pass
            try: reset_speech_gate()   # 고착으로 미반환된 음성 게이트 강제 초기화(향후 발화 불능 방지)
            except: pass
            state="IDLE"; prev_state="IDLE"; state_since=time.time()
            wake.resume()

        # ── 렌더링: 얼굴 → HUD → 채팅로그 → 오버레이 ──
        if face_rec.enabled:
            frame=face_rec.draw_faces(frame)
        _cpu_hud = _mem_hud = None
        if _resource_sampler is not None and _resource_sampler.ok():
            _cpu_hud, _mem_hud = _resource_sampler.snapshot()
        frame=hud.render(frame, state=state, transcript=last_transcript,
                         answer=last_answer, face_count=len(face_rec.last_results) if face_rec.enabled else 0,
                         llm_provider=LLM_PROVIDER, cpu_pct=_cpu_hud, mem_pct=_mem_hud)
        frame=chat_log.render(frame)
        # 매크로 가드레일 등급 → HUD 전역 테마 색 동기화 (변경 시에만 갱신)
        _macro_level=macro_guard.current_level()
        if _macro_level!=_hud_theme_level:
            overlay.set_theme(_macro_level); _hud_theme_level=_macro_level
            log("INFO", f"HUD 테마 전환 → {_macro_level}")
        frame=overlay.render(frame)
        frame=screen_kb.draw(frame)
        frame=draw_background_panel(frame, show_bg["on"])
        frame=draw_performance_panel(frame, show_perf["on"])
        _fh, _fw = frame.shape[:2]
        frame=draw_help(
            frame, show_help["on"],
            hud_state=state,
            overlay_active=overlay.is_active,
            overlay_rect=overlay.content_rect(_fw, _fh),
            has_answer=bool(last_answer),
            hands_enabled=_hands_on,
        )
        frame = draw_power_save_button(frame, power_save_active())

        # ── JARVIS Link: HUD/매트릭스/레이더 실시간 모바일 스트리밍(스로틀 ≈4Hz) ──
        _now=time.time()
        if _now-_last_hud_pub > 0.25:
            _last_hud_pub=_now
            link.publish_hud(
                hud={"state": state, "macro_level": _macro_level,
                     "face_count": len(face_rec.last_results) if face_rec.enabled else 0,
                     "transcript": last_transcript[:120], "answer": last_answer[:160]},
                radar=overlay.get_radar_data(),
                state=state, away=away_state["away"])

        # ── 터미널 편집 결과 반영 ──
        if not _edit_q.empty():
            edited_msg, new_text = _edit_q.get_nowait()
            chat_log.update_message(edited_msg, new_text)
            last_answer = new_text
            # LLM 히스토리의 마지막 assistant 메시지도 교체
            for i in range(len(history)-1,-1,-1):
                if history[i]["role"]=="assistant":
                    history[i]["content"]=new_text; break
            chat_log.add("system","✏ 수정 완료")
            log("INFO","JARVIS 응답 수정 반영")

        # ── 유휴 절전: IDLE + 무음/무재생 N초 → 프로세스 종료 (CPU·배터리 0) ──
        _idle_quiet = (
            state == "IDLE"
            and not recorder._recording
            and not player.is_playing
            and not tts_pipeline.is_playing
            and time.time() - boot_ts > 60
        )
        if _idle_quiet and idle_tracker.should_auto_quit():
            _sec = int(auto_quit_idle_seconds())
            chat_log.add("system", f"💤 유휴 {_sec}초 — 절전 종료 (JARVIS 앱 클릭 시 재기동)")
            log("OK", f"유휴 절전 종료 — CPU 0 (JARVIS.app으로 재시작)")
            break

        cv2.imshow("J.A.R.V.I.S",frame)
        _window_created = True
        # 메인 루프 FPS 상한 — monotonic 기준 누적 스케줄로 sleep 보정
        _loop_next += _loop_min_interval
        _sleep_rem = _loop_next - time.monotonic()
        if _sleep_rem > 0:
            time.sleep(_sleep_rem)
        else:
            _loop_next = time.monotonic()
        key=cv2.waitKey(1)&0xFF
        if _window_created:
            try:
                if cv2.getWindowProperty("J.A.R.V.I.S", cv2.WND_PROP_VISIBLE) < 1:
                    log("INFO", "Window closed by user — shutting down")
                    break
            except Exception:
                pass
        if key not in (255, 0):
            idle_tracker.touch()
        if screen_kb.active and key not in (255, 0):
            _typed_line = screen_kb.feed(key)
            if _typed_line is None:
                pass
            elif _typed_line:
                _text_cmd_q.put(_typed_line)
                chat_log.add("system", f"⌨ Sent: {_typed_line[:80]}")
            else:
                chat_log.add("system", "⌨ On-screen input canceled")
        elif key in (13, 10, ord("/")):
            if state == "IDLE" and not player.is_playing and not tts_pipeline.is_playing:
                screen_kb.open()
                chat_log.add("system", "⌨ On-screen keyboard — Tab toggles EN/KO, Enter sends, Esc cancels")
            else:
                chat_log.add("system", "⌨ On-screen input is only available while IDLE")
        elif key in (ord("k"), ord("K")):
            new_lang = cycle_lang(1)
            msg = tr("lang_switched_msg", new_lang)
            chat_log.add("jarvis", msg)
            log("OK", f"🌐 Language switched → {LANGUAGE_META[new_lang]['label']} ({new_lang})")
            threading.Thread(target=speak_text, args=(msg,), daemon=True).start()
        elif key in (ord("h"), ord("H")):
            show_help["on"] = not show_help["on"]
        elif key in (ord("b"), ord("B")):
            show_bg["on"] = not show_bg["on"]
        elif key in (ord("z"), ord("Z")):
            on = not power_save_active()
            msg = _apply_power_save(on)
            chat_log.add("jarvis", msg)
            log("INFO", msg)
        elif key in (ord("p"), ord("P")):
            show_perf["on"] = not show_perf["on"]
            if show_perf["on"]:
                chat_log.add("system", "⚙ Performance Profile — 1 Eco  2 Balanced  3 High Quality")
        elif show_perf["on"] and key in (ord("1"), ord("2"), ord("3")):
            msg = handle_performance_key(key, True)
            if msg:
                chat_log.add("jarvis", msg)
                log("INFO", msg)
        elif key==ord("q") or key==27:
            tts_pipeline.stop()
            if player.is_playing: player.stop()
            break
        elif key==ord("c"):
            history.clear(); last_transcript=""; last_answer=""
            overlay.clear(); chat_log.clear()
            chat_log.add("system", "History cleared")
        elif key==ord("d"): overlay.clear()
        elif key==ord("s"):
            # S키: 웹캠 차트 캡처 → Gemini 비전 기술적 분석 (대기 상태에서만)
            if power_save_active():
                chat_log.add("system", "Power save mode — Webcam is OFF (press Z to disable)")
            elif state=="IDLE" and not player.is_playing and not tts_pipeline.is_playing:
                state="PROCESSING"
                wake.pause()  # 분석/브리핑 중 웨이크워드 비활성화
                chat_log.add("system", "📸 Capturing chart → Starting technical vision analysis")
                log("INFO", "Key S: Chart vision analysis triggered")
                threading.Thread(target=process_chart_async,
                                 args=(clean_frame.copy(),), daemon=True).start()
            else:
                log("WARN", "Chart analysis is only available while IDLE")
        elif key==ord("t"):
            # T키: 한글 가능한 별도 팝업 입력창(Tkinter)을 띄운다 — 음성과 동일 LLM 파이프라인.
            # macOS에서 Tkinter는 메인 스레드를 요구해 OpenCV 루프와 충돌하므로 '별도 프로세스'로 실행.
            # 팝업이 한 줄씩 stdout으로 보내면 리더 스레드가 _text_cmd_q에 적재 → 루프가 IDLE에서 처리.
            if typing_input["active"]:
                log("INFO", "Typing dialog is already open")
            else:
                _dialog_path=str(Path(__file__).resolve().parent / "text_input_dialog.py")
                try:
                    _proc=subprocess.Popen([sys.executable, _dialog_path],
                                           stdout=subprocess.PIPE, text=True)
                except Exception as e:
                    _proc=None; log("ERR", f"Failed to launch typing dialog: {e}")
                if _proc and _proc.stdout:
                    typing_input["active"]=True; typing_input["proc"]=_proc
                    chat_log.add("system", "⌨ Typing dialog opened — Type your command and press Enter")
                    log("INFO", "Key T: Typing dialog opened")
                    def _dialog_reader(p=_proc):
                        try:
                            for line in p.stdout:
                                msg=line.strip()
                                if msg: _text_cmd_q.put(msg)
                        except Exception:
                            pass
                        finally:
                            typing_input["active"]=False
                            log("INFO", "Typing dialog closed")
                    threading.Thread(target=_dialog_reader, daemon=True).start()
                else:
                    chat_log.add("system", "Could not open typing dialog (check tkinter installation)")
        elif key==ord("l"): chat_log.enabled=not chat_log.enabled  # L: 로그 토글
        elif key==ord("["): chat_log.scroll_up()     # [: 스크롤 위
        elif key==ord("]"): chat_log.scroll_down()   # ]: 스크롤 아래
        elif key==ord("g"):
            if not _hands_on:
                chat_log.add("system", "Hand tracking OFF — set JARVIS_HANDS_ENABLED=1 and restart")
            else:
                gesture_ctrl.enabled=not gesture_ctrl.enabled
                status="ON" if gesture_ctrl.enabled else "OFF"
                chat_log.add("system", f"Gesture mouse {status}")
                log("INFO", f"Gesture control: {status}")
        elif key==ord("w"):
            wake.enabled=not wake.enabled
            if wake.enabled: wake.resume(); chat_log.add("system","Wake word ON")
            else: wake.pause(); chat_log.add("system","Wake word OFF")
        elif key==ord("m"):
            # M키: 외출(모바일) 모드 수동 토글 — 위험 발생 시 모바일 푸시 활성/비활성
            away_state["manual"]=not away_state["away"]
            away_state["away"]=not away_state["away"]
            away_state["last_seen"]=time.time()
            link.set_away(away_state["away"])
            _st="AWAY" if away_state["away"] else "HOME"
            chat_log.add("system", f"📱 {_st} mode — Mobile push {'enabled' if away_state['away'] else 'disabled'}")
            log("INFO", f"Key M: Switched to {_st} mode")
        elif key==ord("r"):
            if not _vision_on:
                chat_log.add("system", "Low-memory/no-vision mode — Face registration disabled")
            else:
                res=face_rec.register_face(frame,"Owner")
                last_answer=res["message"]; log("FACE",res["message"])
                chat_log.add("system", res["message"])
                if res["success"]:
                    threading.Thread(target=speak_text,
                        args=(tr("face_registered_msg", user=USER_DISPLAY_NAME),),daemon=True).start()
        elif key==ord("v"):
            # V키: 음성 샘플 수집 (등록 기기·학습 완료 시 자동 스킵)
            vp=wake.voice_profile
            if not vp.collecting:
                res=vp.start_collection(5)
                chat_log.add("system", res["message"])
                last_answer=res["message"]
                if res.get("skipped"):
                    if res.get("trained"):
                        log("OK", "Known device — using saved voice profile (skipping 5-sample enrollment)")
                else:
                    threading.Thread(target=speak_text,
                        args=(tr("voice_enroll_start"),),daemon=True).start()
            if vp.collecting:
                def collect_samples():
                    for i in range(vp._collect_target - vp._collect_count):
                        time.sleep(2)  # 2초 간격
                        if not vp.collecting: break
                        r=vp.collect_sample(recorder.pa)
                        if r:
                            chat_log.add("system", r["message"])
                            if r.get("trained"):
                                threading.Thread(target=speak_text,
                                    args=(tr("voice_enroll_done"),),
                                    daemon=True).start()
                threading.Thread(target=collect_samples,daemon=True).start()
        elif key==ord("e"):  # E키: 마지막 JARVIS 응답 터미널 편집
            target=chat_log.get_last_jarvis()
            if target:
                def _edit_thread(msg=target):
                    SEP="─"*55
                    preview=msg.text if len(msg.text)<=120 else msg.text[:120]+"..."
                    print(f"\n{SEP}\n📝 [Edit JARVIS Response]")
                    print(f"Current:\n  {preview}\n{SEP}")
                    print("Enter new text (Enter to confirm / empty to cancel):")
                    try:
                        new=input("→ ").strip()
                        if new: _edit_q.put((msg,new))
                        else: print("Edit canceled")
                    except (EOFError,KeyboardInterrupt): pass
                chat_log.add("system","✏ Enter updated response in Terminal...")
                threading.Thread(target=_edit_thread,daemon=True).start()
            else:
                chat_log.add("system","No JARVIS response to edit.")
        elif key==32:  # 스페이스바
            if power_save_active():
                chat_log.add("system", "Power save mode — Voice recording disabled (use Enter, /, or T)")
            elif state=="RECORDING":
                # 녹음 중 → 수동 종료
                state="PROCESSING"; wav=recorder.stop()
                if wav: dispatch_processing(wav)
                else: state="IDLE"
            elif state=="IDLE" and not wake.voice_profile.trained and not voice_auth_required():
                # 미학습 상태에서만 스페이스로 녹음 시작 허용 (최대 보안+미등록 시 제외)
                state="RECORDING"
                _begin_recording()
            elif state=="IDLE" and voice_auth_required() and voice_setup_required(wake.voice_profile):
                chat_log.add("system", "🔒 Maximum Security — Press V to enroll voice first")
            elif state=="SPEAKING":
                tts_pipeline.stop()
                player.stop()

    # ── 즉시 종료 시퀀스 (macOS '응답 없음' / Beachball 원천 차단) ──
    _shutdown_evt.set()

    # 0) 백그라운드 C 확장/스레드가 종료를 붙잡을 경우를 대비한 하드 워치독 (1.5초)
    def _exit_watchdog():
        time.sleep(1.5)
        try:
            clear_pid()
        except Exception:
            pass
        os._exit(0)

    threading.Thread(target=_exit_watchdog, daemon=True, name="exit-watchdog").start()

    # 1) 모든 음성 출력·TTS 합성 서브프로세스(say, jarvis_tts, afplay) 즉시 중단
    try:
        shutdown_speech_gate()
    except Exception:
        pass
    try:
        stop_all_local_tts()
    except Exception:
        pass
    try:
        tts_pipeline.stop()
    except Exception:
        pass
    try:
        player.stop()
    except Exception:
        pass

    # 2) OpenCV 창을 가장 먼저 닫고 Cocoa 이벤트 루프를 즉시 플러시 (화면에서 창 즉시 제거)
    try:
        cv2.destroyAllWindows()
        for _ in range(6):
            cv2.waitKey(1)
    except Exception:
        pass

    # 3) 타이핑 팝업·PID·SQLite DB 즉시 정리
    _tp = typing_input.get("proc")
    if _tp and _tp.poll() is None:
        try:
            _tp.terminate()
        except Exception:
            pass
    clear_pid()
    try:
        from memory_store import close_db
        close_db()
    except Exception:
        pass

    # 4) 카메라/오디오/백그라운드 워커/서브프로세스 병렬 논블로킹 정리 (최대 0.45초 대기)
    def _safe_call(fn):
        try:
            fn()
        except Exception:
            pass

    def _stop_media():
        if media_rt:
            media_rt.stop()
            set_media_runtime(None)

    def _stop_risk_executor():
        if "risk_engine" in sys.modules:
            from risk_engine import shutdown_executor
            shutdown_executor()

    _teardown_fns = [
        lambda: frame_reader.stop(timeout=0.2) if hasattr(frame_reader, "stop") else None,
        face_worker.stop,
        lambda: recorder.stop(save=False, timeout=0.2) if recorder._recording else None,
        lambda: wake.stop(timeout=0.25),
        stop_prefetch,
        bg_monitor.stop,
        news_analyzer.stop,
        price_alert.stop,
        whale_tracker.stop,
        social_detector.stop,
        ir_interpreter.stop,
        _stop_media,
        stock_monitor.stop,
        campus_agent.stop,
        macro_guard.stop,
        link.stop,
        lambda: stop_supply_chain_backend(timeout=0.4),
        shutdown_process_pool,
        _stop_risk_executor,
        lambda: _resource_sampler.stop() if _resource_sampler is not None else None,
    ]
    _teardown_threads = [
        threading.Thread(target=_safe_call, args=(fn,), daemon=True)
        for fn in _teardown_fns
    ]
    for _t in _teardown_threads:
        _t.start()

    _td_deadline = time.monotonic() + 0.45
    for _t in _teardown_threads:
        _rem = max(0.0, _td_deadline - time.monotonic())
        if _rem <= 0:
            break
        _t.join(timeout=_rem)

    # FrameReader 스레드가 완전히 멈춘 경우에만 cap.release() 호출 (AVFoundation 경합 방지)
    if cap is not None and not (hasattr(frame_reader, "is_thread_alive") and frame_reader.is_thread_alive()):
        try:
            cap.release()
        except Exception:
            pass

    log("OK", "v3.2 shutdown complete.")

def _run_telegram_selftest() -> int:
    """--test-telegram: 무거운 부팅(카메라·오디오·무결성 체인) 없이 텔레그램 발송만 즉시 검증."""
    import os as _os
    token = _os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat  = _os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        print("❌ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 .env 에 설정되지 않았습니다.")
        return 1
    msg = "🤖 [자비스 인프라 검증] 텔레그램 알림 브리지가 성공적으로 연결되었습니다!"
    link = init_link(logger=lambda lvl, m: None)
    ok = link._post_telegram(msg)
    if ok:
        print(f"✅ 텔레그램 발송 성공 — chat_id={chat} 으로 검증 메시지를 보냈습니다. 폰/아이패드를 확인하세요.")
        return 0
    print("❌ 텔레그램 발송 실패 — 토큰/CHAT_ID 또는 네트워크를 확인하세요 "
          "(봇과 먼저 /start 대화를 시작했는지도 점검).")
    return 1


def _show_fatal_crash_dialog(exc: BaseException) -> None:
    """출시 앱(.app) 크래시 시 사용자 친화적인 알림 대화상자 표시 및 로그 안내."""
    import traceback
    tb = traceback.format_exc()
    sys.stderr.write(f"\n[FATAL] J.A.R.V.I.S crashed: {exc}\n{tb}\n")
    sys.stderr.flush()
    try:
        clear_pid()
    except Exception:
        pass
    if _is_app_launch() and sys.platform == "darwin":
        data_dir = os.getenv("JARVIS_DATA_DIR") or str(Path.home() / "Library/Application Support/JARVIS")
        log_path = f"{data_dir}/.logs/jarvis_app.log"
        err_short = str(exc).replace('"', "'").replace("\n", " ")[:160]
        msg = (
            f"J.A.R.V.I.S encountered an unexpected error and closed.\\n\\n"
            f"Reason: {err_short}\\n\\n"
            f"Log file: {log_path}"
        )
        try:
            subprocess.run(
                ["/usr/bin/osascript", "-e", f'display alert "J.A.R.V.I.S Error" message "{msg}" as warning'],
                timeout=10,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


if __name__ == "__main__":
    import sys as _sys
    if "--test-telegram" in _sys.argv:
        raise SystemExit(_run_telegram_selftest())
    try:
        # ── 보안 정책 최우선 적용 (환경변수·속도 제한·SSRF·requests 패치) ──
        _boot_level = apply_environment_hardening()
        reconfigure_security()
        # ── 보안 블랙박스 무결성 게이트 ──
        if not verify_audit_log_integrity():
            if _is_app_launch():
                # 출시 앱에서는 손상된 감사 로그로 인해 첫 실행이 막히지 않도록 자동 복구
                _arch = reinitialize_audit_log(reason="app_launch_auto_recover")
                log("WARN", f"🔒 Audit log auto-recovered on app launch → {_arch}")
            else:
                print("🚨 Security audit log integrity check failed!")
                print("   Run 'python3 security.py --reinit' to reinitialize the audit chain.")
                try:
                    write_audit_log("AUDIT_INTEGRITY_HALT", {"action": "self_destruct_exit"})
                except Exception:
                    pass
                exit(1)
        write_audit_log("BOOT", {"event": "integrity_ok", "version": "v3.2"})
        main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        try:
            clear_pid()
        except Exception:
            pass
    except BaseException as _fatal_exc:
        _show_fatal_crash_dialog(_fatal_exc)
        os._exit(1)
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(0)
