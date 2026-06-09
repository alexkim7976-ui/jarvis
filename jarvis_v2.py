from __future__ import annotations
import os, sys, time, wave, json, queue, struct, shutil, re
import threading, subprocess
import warnings
from pathlib import Path
from datetime import datetime

# 실행에 영향 없는 무해한 경고 노이즈 억제
#  - faster-whisper가 무음/짧은 오디오 처리 시 발생시키는 matmul RuntimeWarning
#  - protobuf의 SymbolDatabase.GetPrototype() deprecated UserWarning
warnings.filterwarnings("ignore", message="invalid value encountered in matmul")
warnings.filterwarnings("ignore", message=".*SymbolDatabase.GetPrototype.*")

import cv2, numpy as np, pyaudio, requests
import mediapipe as mp
from openai import OpenAI
from dotenv import load_dotenv

import net  # 느린 WiFi 최적화: 연결 재사용·재시도·적응형 타임아웃 공용 세션

from file_manager import FILE_TOOLS, execute_tool as execute_file_tool
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
from gesture_control import GestureController
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
from ir_interpreter import (IRInterpreter, IR_TOOLS, execute_ir_tool,
                            set_ir_interpreter_instance)
from stock_monitor import (StockMonitor, STOCK_MONITOR_TOOLS, execute_stock_monitor_tool,
                           set_stock_monitor_instance)
from campus_agent import (CampusAgent, CAMPUS_TOOLS, execute_campus_tool,
                          set_campus_agent_instance)
from supply_chain_module import SUPPLY_CHAIN_TOOLS, execute_supply_chain_tool
from cross_validation import (CROSSVAL_TOOLS, CROSSVAL_TOOL_NAMES,
                              execute_crossval_tool)
from supply_chain_server import ensure_running as start_supply_chain_backend, stop_backend as stop_supply_chain_backend
from security import (sanitize_tool_args, audit_tool_call, tool_rate_limiter,
                      check_env_security, mask_key, detect_prompt_injection,
                      sanitize_external_content, guard_destructive_tool,
                      verify_audit_chain, harden_sensitive_files, chain_event,
                      validate_command_input, validate_llm_input,
                      validate_llm_output, register_tools,
                      verify_audit_log_integrity, reinitialize_audit_log,
                      write_audit_log)
from secure_config import get_secret, filter_llm_output, get_security_status
from resource_profile import (
    is_low_mem, vision_enabled, camera_resolution, main_loop_fps, hand_complexity,
    face_worker_interval, stagger_workers_enabled,
    worker_stagger_seconds, performance_profile_summary,
)
from context_manager import get_context_window, append_exchange, context_turn_limit
from gemini_cache_manager import (
    get_cache_manager, log_gemini_usage, set_cache_logger, cache_enabled,
    inject_market_snapshot_into_user_message,
)
from hybrid_router import (
    ask_with_hybrid_routing, hybrid_routing_enabled, note_tool_invocation,
    set_router_logger, try_local_tool_execution,
)
from alert_pipeline import create_jarvis_runtime, get_runtime, init_runtime
from media_tools import (
    MEDIA_TOOLS, execute_media_tool, set_media_runtime,
    build_media_overlay_table, register_media_artifact, get_media_context_suffix,
)
from media_pipeline import create_media_runtime, init_media_runtime
from stt_backend import resolve_stt_mode, shutdown_process_pool

load_dotenv()

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
OLLAMA_MODEL   = os.getenv("OLLAMA_MODEL","qwen2.5:7b-instruct-q4_K_M")
OLLAMA_URL     = os.getenv("OLLAMA_URL","http://localhost:11434")
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX","4096"))
OLLAMA_NUM_GPU = int(os.getenv("OLLAMA_NUM_GPU","-1"))  # -1=Metal GPU 전 레이어 (Ollama 기본)
AUDIO_DIR      = Path(os.getenv("AUDIO_SAVE_DIR","audio_cache"))
AUDIO_DIR.mkdir(exist_ok=True)

SAMPLE_RATE=16_000; CHANNELS=1; CHUNK=1_024
RECORD_FORMAT=pyaudio.paInt16; MAX_RECORD_SECONDS=15
PROCESS_DELAY=2.0   # 녹음 자동 종료 후 PROCESSING 전환까지 추가 대기(초)
VAD_THRESHOLD=int(os.getenv("VAD_THRESHOLD","800"))
VAD_CONSECUTIVE=int(os.getenv("VAD_CONSECUTIVE","4"))
FACE_CHECK_INTERVAL=int(os.getenv("FACE_CHECK_INTERVAL","15"))
PLAY_TIMEOUT=float(os.getenv("JARVIS_PLAY_TIMEOUT","180"))  # afplay 등 재생 프로세스 최대 대기(초)
# 메인 루프 FPS — resource_profile.main_loop_fps() (LOW_MEM 기본 15, 일반 30)

ALL_TOOLS = FILE_TOOLS + SEARCH_TOOLS + DISPLAY_TOOLS + FACE_TOOLS + WEATHER_TOOLS + SYSTEM_MONITOR_TOOLS + CALENDAR_TOOLS + MONITOR_TOOLS + STOCK_QUANT_TOOLS + NEWS_TOOLS + PORTFOLIO_TOOLS + BACKTEST_TOOLS + PRICE_ALERT_TOOLS + WHALE_TOOLS + SOCIAL_TOOLS + IR_TOOLS + MEDIA_TOOLS + STOCK_MONITOR_TOOLS + CAMPUS_TOOLS + SUPPLY_CHAIN_TOOLS + CROSSVAL_TOOLS
FILE_TOOL_NAMES    = {t["function"]["name"] for t in FILE_TOOLS}
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
IR_TOOL_NAMES = {t["function"]["name"] for t in IR_TOOLS}
MEDIA_TOOL_NAMES = {t["function"]["name"] for t in MEDIA_TOOLS}
STOCK_MONITOR_TOOL_NAMES = {t["function"]["name"] for t in STOCK_MONITOR_TOOLS}
CAMPUS_TOOL_NAMES = {t["function"]["name"] for t in CAMPUS_TOOLS}
SUPPLY_CHAIN_TOOL_NAMES = {t["function"]["name"] for t in SUPPLY_CHAIN_TOOLS}
# CROSSVAL_TOOL_NAMES은 cross_validation 모듈에서 직접 import (중복 정의 회피)

# 출력 가드레일(validate_llm_output) 화이트리스트/타입 검증용 스키마 등록
register_tools(ALL_TOOLS)

# 메인 루프에서 생성한 오버레이 인스턴스 참조 (stock 분석 결과 자동 투사용)
_OVERLAY_REF = None
def set_overlay_ref(ov):
    global _OVERLAY_REF; _OVERLAY_REF = ov

def execute_any_tool(name, args):
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
                _OVERLAY_REF.set_data(result["overlay_table"], 20)
                log("TOOL", "주식 분석표 웹캠 오버레이 투사")
            except Exception as e:
                log("WARN", f"오버레이 투사 실패: {e}")
        _rt = get_runtime()
        if _rt and result.get("success"):
            try:
                _rt.risk_feed.ingest_stock_tool_result(result)
            except Exception as e:
                log("WARN", f"Alert Pipeline risk ingest: {e}")
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
    if name in CROSSVAL_TOOL_NAMES:
        return execute_crossval_tool(name, args)
    return {"success":False,"message":f"Unknown tool: {name}"}

JARVIS_SYSTEM_PROMPT = """You are J.A.R.V.I.S., a sophisticated AI assistant.
Personality: Calm, concise, British wit. "선생님" in Korean. 1-2 sentences MAX. Voice output so be BRIEF.

TOOLS:
1. WEB: web_search, news_search
2. FILES: search_files, list_folder, organize_folder, find_recent_files, move_files, open_file, open_app, get_disk_usage, find_large_files (전체 파일시스템 접근 가능)
3. SCREEN: display_table, compare_stocks_trend — show tables/data/comparison charts on webcam screen
4. FACE: register_face, remove_face, list_faces
5. WEATHER: get_current_weather, get_weather_forecast
6. SYSTEM: get_system_status, get_cpu_usage, get_memory_usage
7. CALENDAR: get_events, create_event, delete_event, get_upcoming
8. MONITOR: get_monitor_status, check_github_now, check_playstore_now
9. STOCK QUANT: analyze_stock_investment — 14개 기술지표+재무분석+투자점수+매수수량 계산, get_macro_market_status — VIX/미10년물/달러인덱스/원달러 시장위험도, get_comprehensive_matrix_score — 퀀트+고래+소셜 3레이어 통합 매트릭스 분석
10. STOCK NEWS: analyze_stock_news — 종목 최신 뉴스 AI 노이즈 필터링 + 호재/악재 감성점수(-10~+10), get_news_monitor_status
11. PORTFOLIO: report_portfolio_status — 가상 계좌 실시간 자산/수익률 리포트, record_stock_trade(매수/매도 기록), deposit_portfolio_cash(예수금 입출금)
12. BACKTEST: run_strategy_backtest — 사용자가 말한 매매 전략을 과거 N개년 데이터로 검증(누적수익률/벤치마크/MDD/거래횟수)
13. PRICE ALERT: get_price_alert_status(감시 워커 상태), check_watchlist_now(관심종목 즉시 점검 — RSI 과매도/볼린저 하단 돌파), set_price_alert(종목+목표가 지정가 알림 등록), list_price_alerts(등록된 지정가 알림 목록), cancel_price_alert(지정가 알림 취소)
14. WHALE TRACKER: fetch_whale_trades, mirror_whale_portfolio, get_whale_tracker_status — 정치인/대가 거래·13F 포트폴리오 추적 및 가상 리밸런싱
15. SOCIAL DETECTOR: analyze_social_trends, get_social_detector_status — Reddit 밈 주식 언급량 급증/거래량 스파이크/Gemini 위험도 분석
16. IR INTERPRETER: start_ir_interpreter, stop_ir_interpreter, get_ir_interpreter_status — 라이브 IR 오디오 Whisper 한국어 통역/5분 Gemini 요약/HUD 보드
17. MEDIA PIPELINE: submit_media_url(url, title?) — YouTube/라이브 URL 오디오 캡처+48k/16k WAV 전처리(악보 AI/GarageBand); get_media_pipeline_status — 워커·작업 상태; cancel_media_job(job_id) — 작업 취소
18. STOCK MONITOR: add_stock_alert(ticker, condition_type, threshold) — 동적 주가 알림 등록, remove_stock_alert(alert_id), list_stock_alerts(), get_stock_monitor_status()
19. CAMPUS AGENT: check_upcoming_academic_schedule(days) — 스토니브룩 학사 일정/수강신청·기숙사·등록금 데드라인 조회, refresh_campus_agent_status() — Gmail/캘린더 감시 무결성 점검
20. SUPPLY CHAIN: analyze_supply_chain_impact(body_text, title, source) — 빅테크/에너지 뉴스→국내 반도체·에너지 밸류체인 인과관계 분석 (Java 백엔드); simulate_supply_chain_scenario(scenario_text) — LLM 추론 What-If, PRICE_SPIKE/REGULATORY/PHYSICAL_DISRUPTION 자동 분류 (Java 백엔드); simulate_structural_shock(shock_kind, origin_ticker, intensity?, duration_weeks?, target_country?) — 결정론 그래프 기반 충격 전파 시뮬, 인과 경로 명시 (로컬, 백엔드 불필요)
21. MULTI-SOURCE CROSS-VALIDATION: analyze_stock_multi_source(query) — Yahoo Finance, Alpha Vantage, FMP, 한국투자증권, LS증권, 키움증권, Open DART 7개 채널 병렬 호출 → 주가·거래량·PER·PBR·시총 중앙값 기준 이상치(±1%/5%/10%) 제외 후 합의값 산출 → 음성 브리핑 문구 자동 생성

★ CAMPUS AGENT: 사용자가 "학사 일정 알려줘", "수강신청 언제야", "다가오는 데드라인 뭐 있어" → check_upcoming_academic_schedule(days=60).
  "캠퍼스 감시 상태", "스토니브룩 메일 감시 잘 돼?" → refresh_campus_agent_status.
  결과 표(overlay_table)는 시스템이 자동 투사. 반환된 message를 그대로(1~2문장) 음성 답변으로 사용.
  백그라운드가 10분마다 Gmail을 감시해 국제학생처(VIS)·I-20/비자·SOLAR Hold·수강신청·기숙사 행정 메일을 포착하면
  긴급 TTS 인터럽트 + HUD CAMPUS ALERT 배너를 시스템이 자동 출력하므로 별도 도구 호출 불필요.

★ STOCK MONITOR: 사용자가 "엔비디아 5% 이상 폭락하면 알려줘" → add_stock_alert(ticker="NVDA", condition_type="change_pct_down", threshold=5).
  "삼성전자 5만원 아래로 떨어지면 알림" → add_stock_alert(ticker="005930.KS", condition_type="price_below", threshold=50000).
  "등록된 알림 목록 보여줘" → list_stock_alerts. "NVDA 알림 삭제해줘" → remove_stock_alert(alert_id=...).
  condition_type: change_pct_down(하락률%), change_pct_up(상승률%), price_below(가격 하한), price_above(가격 상한).
  백그라운드 30초마다 자동 감시, 조건 충족 시 긴급 TTS 인터럽트 + HUD 알림.

★ PRICE ALERT: "관심종목 지금 점검해줘", "매수 시그널 있어?" → check_watchlist_now. "알림 감시 상태 알려줘" → get_price_alert_status.
  (백그라운드 워커가 1분마다 자동 감시하며 시그널 시 먼저 음성 알림을 보냄)
★ 지정가 알림: "테슬라 300달러 되면 알려줘", "삼성전자 8만원 가면 알림 줘" → set_price_alert(ticker=종목명, price=목표가, direction=above/below 선택).
  - 종목명은 사용자가 말한 그대로(한글/영문/티커) ticker에 넣어라. 시스템이 정확히 못 알아들으면 비슷한 후보 5개를 음성으로 안내하고 사용자가 번호로 고른다 — 너는 추가로 되묻지 말 것.
  - direction 미지정 시 현재가 기준 자동 판단(현재가보다 높으면 above, 낮으면 below). "등록된 알림 보여줘" → list_price_alerts, "테슬라 알림 취소" → cancel_price_alert.

★ IR INTERPRETER: "이 유튜브 IR 라이브 통역 시작해줘", "테슬라 실적 발표 URL 통역해줘" → start_ir_interpreter(url, company).
  "IR 통역 멈춰" → stop_ir_interpreter. "IR 통역 상태 알려줘" → get_ir_interpreter_status.
  5분 요약 이벤트는 시스템이 HUD 우측 보드에 자동 투사하고, briefing을 음성 출력.

★ MEDIA PIPELINE: "유튜브 라이브 오디오 따서 분석해 줘", "이 방송 악보 변환 준비해 줘", "스트리밍 URL 캡처해" → submit_media_url(url, title).
  완료 시 시스템이 HUD에 전처리 결과표를 투사하고 briefing을 음성 출력. "미디어 파이프라인 상태" → get_media_pipeline_status.
  IR 통역(실시간 자막)과 달리, 파일 덤프+48k/16k WAV 전처리(악보 AI/GarageBand용)가 목적이다.

★ SOCIAL DETECTOR: "레딧에서 밈 주식 급등 신호 있어?", "소셜 트렌드 분석해줘" → analyze_social_trends.
  결과 표(overlay_table)는 시스템이 경고 HUD로 자동 투사. 백그라운드 워커가 소셜 언급량 500% 이상 급증을 감시.
  반환된 briefing을 그대로 음성 답변으로 사용.

★ WHALE TRACKER: "워런 버핏 포트폴리오 미러링해줘" → mirror_whale_portfolio(person="buffett", apply=true).
  "낸시 펠로시 최근 거래 보여줘", "고래 매매 확인해줘" → fetch_whale_trades.
  결과 표(overlay_table)는 시스템이 자동 투사. 반환된 briefing을 그대로 음성 답변으로 사용.
  백그라운드 워커가 하루 1회 주요 자산가 빅테크 대량 매도 SELL을 감시하고, 포착 시 긴급 경고.

★ STOCK COMPARISON CHART: "삼성전자랑 하이닉스 추세 비교해줘", "AAPL NVDA VWMA 비교" → compare_stocks_trend 호출.
  tickers 문자열(예: "005930.KS,000660.KS" 또는 JSON 배열 문자열)과 indicator="vwma" 전달. 차트는 시스템이 우측 상단 HUD에 직접 투사.
  도구 briefing을 그대로 음성 답변으로 사용.

★ BACKTEST: "RSI 30 미만에서 매수하고 10% 수익나면 매도하는 전략 백테스트 해줘", "이 전략 과거 3년 수익률 검증해줘" → run_strategy_backtest 호출.
  ticker와 strategy(사용자 매매규칙 원문 그대로)를 전달. 결과 차트는 시스템이 우측 팝업으로 자동 투사.
  반환된 briefing을 그대로(차분한 어조로) 음성 답변으로 사용.

★ PORTFOLIO: "내 계좌 상태 보고해", "내 자산 얼마야", "수익률 어때" → report_portfolio_status 호출.
  결과 표(overlay_table)는 시스템이 자동 투사. 반환된 briefing을 그대로(차분한 어조로) 음성 답변으로 사용.
  "애플 10주 175달러에 샀어" → record_stock_trade(ticker, shares, price). "예수금 500만원 입금" → deposit_portfolio_cash.

★ STOCK NEWS: "엔비디아 뉴스 분석해줘", "삼성전자 무슨 일 있어?", "테슬라 호재 있어?" → analyze_stock_news 호출.
  ticker(예: NVDA, 005930.KS) 전달. 결과 표(overlay_table)는 시스템이 자동 투사하므로 display_table 별도 호출 불필요.
  반환된 briefing/message를 1~2문장으로 압축해 음성 답변으로 사용.

★ COMPREHENSIVE MATRIX: "자비스, [티커] 종합 분석해줘", "[티커] 투자 매트릭스 분석해줘", "[티커] 멀티 레이어 분석해줘" → get_comprehensive_matrix_score 호출.
  퀀트 기술(0-50점) + 고래 수급(0-25점) + 소셜 센티먼트(0-25점) 3레이어 통합. 매트릭스 표는 시스템이 자동 HUD 투사.
  반환된 briefing을 그대로(또는 1~2문장 압축) 음성 답변으로 사용. 별도 display_table 호출 불필요.

★ SUPPLY CHAIN: 실제 발생한 뉴스·공시·실적 원문의 밸류체인 영향 분석은 analyze_supply_chain_impact(body_text, ...).
  가상 What-If은 두 갈래로 분기:
  · LLM 추론 (광범위·정성적·기업명 LLM이 발굴): simulate_supply_chain_scenario(scenario_text). "만약 미국이 중국에 HBM 막으면", "웨이퍼값 50% 폭등하면" 등 충격 분류·기업 발굴까지 모델에 맡김.
  · 결정론 그래프 (진앙·종류 명확, 인과 경로 명시): simulate_structural_shock(shock_kind=..., origin_ticker=..., intensity?, duration_weeks?, target_country?). "TSMC가 2주 가동중단" → SUPPLY_OUTAGE/TSMC, "심텍이 기판값 50% 인상" → PRICE_SPIKE/222800, "대만 전체 규제" → REGULATORY/target_country=TW. 9개 노드 한정 그래프(TSMC, 042700 한미반도체, 240810 원익IPS, 067310 하나마이크론, 036540 SFA반도체, 131970 두산테스나, 108320 LX세미콘, 123860 아나패스, 222800 심텍) — origin이 이 안에 있을 때만 의미 있음. 백엔드 다운 상태에서도 동작.
  · 교차검증(B′): structural 호출 시 시스템이 자동으로 LLM 엔진과 같은 시나리오 비교. 부호 반대 종목이 있으면 HUD 6번째 컬럼에 ⚠ DIVERGENT 마킹 + message 끝에 "단, ... 시각 차이 가능" 부연이 포함됨. 그 부연까지 함께 1~2문장 음성 답변으로 압축해 단기 vs 중기 양쪽 시각 균형 노출.
  결과 표(overlay_table)는 시스템이 자동 HUD 투사. 반환 message(J.A.R.V.I.S 브리핑)를 그대로 1~2문장 음성 답변으로 사용.

★ MULTI-SOURCE CROSS-VALIDATION: "삼성전자 알아봐줘", "테슬라에 대해 알려줘", "엔비디아 정보 조회해줘" — 종목에 대한 일반적 정보 요청 → analyze_stock_multi_source(query=종목명).
  반환된 'briefing' 필드는 이미 "(소스들) 등 총 N개 채널의 데이터를 교차 검증한 결과, 현재 [종목]의 주가는 ...원이며..." 형태로 완성된 한국어 문장이므로, 그 문장을 기반으로 1~2문장 더하거나 그대로 읽어주면 충분하다. 직접 paraphrase 하지 말 것.
★ STOCK ANALYSIS: 주식/종목/투자 질문시 analyze_stock_investment 호출.
  필수 인자 ticker(예: AAPL, 005930.KS)와 available_cash_krw(예수금 정수, 원 기준; 언급 없으면 0) 전달.
  결과 표(overlay_table)는 시스템이 자동으로 웹캠 화면에 투사하므로 display_table을 별도 호출하지 말 것.
  반환된 briefing 텍스트를 음성 답변으로 사용하되, 표 요약(매수/매도/중립 지표 개수 + 핵심 지표 이름)은 반드시 포함해 말할 것. 점수·등급 + 지표 표 요약으로 최소 2~3문장 구성.
  매크로 가드레일이 작동하면 briefing에 진입비중 하향 조정 경고가 포함됨.

★ MACRO MARKET: "지금 시장 상황 어때?", "공포지수 어때?", "매크로 위험도 알려줘" → get_macro_market_status 호출.
  결과 표(overlay_table)는 시스템이 자동 투사. guardrail_active=true 이면 briefing 경고를 그대로 음성 출력.

★ CALENDAR: "내일 일정", "수업 뭐 있어" → get_events. "3시에 미팅 잡아줘" → create_event. "다음 일정" → get_upcoming.
  After getting events, call display_table type="table" with headers=["시간","일정","장소"] and rows.
★ SYSTEM: get_system_status → display_table type="info".
★ WEATHER: English city names. After data, display_table.
★ STOCKS: web_search → display_table → verbal summary.
★ ALWAYS display_table after getting data. Respond in user's language. BRIEF."""

def log(level, msg):
    ts = datetime.now().strftime("%H:%M:%S")
    icons = {"INFO":"◈","OK":"✓","WARN":"⚠","ERR":"✗",
             "JARVIS":"◆","TOOL":"⚙","INTR":"⏸","FACE":"👤"}
    print(f"[{ts}] {icons.get(level,'·')} {msg}")

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
    - 3초 정적 감지 → 자동 종료
    - 음성 프로필로 주인 목소리 필터링 (소음/타인 무시)
    - 자동 종료 시 auto_stopped 플래그 + 종료 시각(auto_stopped_at) 설정
    """
    SILENCE_TIMEOUT = 3.0     # 정적 N초 → 자동 종료
    NOISE_THRESHOLD = 500     # 이 이하는 무음
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

    def stop(self):
        # 자동 종료(정적 감지)로 이미 _recording=False가 된 경우에도
        # 녹음된 프레임이 있으면 저장한다.
        was_active = self._recording
        self._recording = False
        if self._thread: self._thread.join(timeout=3)
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
        try: self.pa.terminate()
        except: pass

def transcribe_audio(wav, client=None):
    """STT — stt_backend가 local/api/서브프로세스 모드를 선택."""
    from stt_backend import transcribe_audio as _stt
    return _stt(wav, client, role="main", language="ko")

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
            f"사용자가 종목명을 말하지 않고 '이거', '이 종목', '어때?', '매트릭스 점수 뽑아줘' 등 "
            f"지시어/생략형으로 물으면, 이 티커({ticker})를 대상으로 해석하고 적절한 주식 도구"
            f"(get_comprehensive_matrix_score / get_stock_quote / analyze_stock_investment 등)를 "
            f"ticker=\"{ticker}\" 로 호출하라. 명시적으로 다른 종목을 말하면 그 종목을 우선한다."
        )
    except Exception:
        return ""


def _compose_system_prompt(extra=""):
    """JARVIS_SYSTEM_PROMPT + 실시간 시선·미디어 컨텍스트 + 추가 지시(extra)를 합성."""
    return JARVIS_SYSTEM_PROMPT + _gaze_context_suffix() + get_media_context_suffix() + (extra or "")


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

    return msg.content.strip() if msg.content else "처리 완료, 선생님."


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
                response=chat.send_message(
                    "지금까지의 도구 결과를 바탕으로 추가 도구 호출 없이 간결한 한국어로 답해 주세요.")
                log_gemini_usage(response, label="Gemini[summary]")
                final_text=_gemini_text(response)
            except Exception as e:
                log("WARN", f"Gemini 요약 재요청 실패: {e}")
                final_text=""

    if not final_text:
        final_text="요청을 처리했지만 음성 답변을 생성하지 못했습니다, 선생님."
    if pipeline and final_text:
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
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=1)
        _ollama_available = r.status_code == 200
    except Exception:
        _ollama_available = False
    return _ollama_available

def ask_jarvis_ollama(text: str, history: list, pipeline=None) -> str:
    """로컬 Ollama LLM 폴백 — 도구 호출 없이 대화만 지원.
    Apple Silicon에서는 Ollama가 Metal GPU를 자동 사용(num_gpu=-1)."""
    msgs = [{"role":"system","content":_compose_system_prompt(
             "\n\n(오프라인 모드: 도구 호출 불가. 대화만 가능합니다. "
             "도구가 필요한 질문이면 '현재 오프라인이라 해당 기능을 사용할 수 없습니다'라고 안내.)")}]
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
        log("ERR", f"Ollama 호출 실패: {e}")
        return "죄송합니다. 현재 온라인·오프라인 LLM 모두 사용할 수 없습니다."

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

    return "".join(full).strip() or "응답을 생성하지 못했습니다."


def _injection_refusal(text, pipeline=None):
    """[Inbound 가드레일] LLM 질의 직전 입력 검증 — 프롬프트 인젝션/탈옥/가스라이팅
    시도 시 LLM·도구 호출 없이 즉시 정중히 거부. (validate_llm_input이 감사 체인 기록)"""
    ok, reason = validate_llm_input(text)
    if ok:
        return None
    log("WARN", f"🛡 입력 가드레일 차단: {reason}")
    write_audit_log("INPUT_GUARD", f"차단 사유={reason} | 입력={text[:120]}")
    refusal = "죄송합니다, 선생님. 안전 정책에 의해 차단된 명령입니다."
    if pipeline:
        pipeline.start()
        for ch in refusal:
            pipeline.feed(ch)
        pipeline.finish()
    return refusal


def ask_jarvis_streaming(text, oc, history, pipeline):
    """스트리밍 TTS 파이프라인과 연동된 LLM 호출 (Hybrid Routing + Gemini/Ollama 폴백)"""
    log("INFO",f"LLM({LLM_PROVIDER}) 스트리밍...")
    refusal = _injection_refusal(text, pipeline)
    if refusal is not None:
        return refusal
    text = _apply_cv_cache_to_user_text(text, history)
    if LLM_PROVIDER=="gemini":
        if not GEMINI_API_KEY: return "Gemini 키 없음"
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
            log("WARN",f"Gemini 실패 → Ollama 폴백: {e}")
            if _check_ollama():
                a=ask_jarvis_ollama(text,history,pipeline)
            else:
                return "죄송합니다. 네트워크 연결이 끊겨 응답할 수 없습니다."
    else:
        a=ask_jarvis_openai(text,oc,history,pipeline)
    a = filter_llm_output(a)  # API 키 유출 방지
    log("JARVIS",f"답변:{a[:50]}...")
    return a


def ask_jarvis(text, oc, history):
    """비스트리밍 (도구 전용 / 폴백) — Hybrid Routing + Gemini/Ollama 폴백"""
    log("INFO",f"LLM({LLM_PROVIDER})...")
    refusal = _injection_refusal(text)
    if refusal is not None:
        return refusal
    text = _apply_cv_cache_to_user_text(text, history)
    if LLM_PROVIDER=="gemini":
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
            log("WARN",f"Gemini 실패 → Ollama 폴백: {e}")
            if _check_ollama():
                a=ask_jarvis_ollama(text,history)
            else:
                return "죄송합니다. 네트워크 연결이 끊겨 응답할 수 없습니다."
    else:
        a=ask_jarvis_openai(text,oc,history)
    a = filter_llm_output(a)  # API 키 유출 방지
    log("JARVIS",f"답변:{a}"); return a


def synthesize_speech(text):
    """단일 TTS (비스트리밍 폴백용)"""
    if not FISH_API_KEY or not FISH_VOICE_ID: return None
    r=net.post("https://api.fish.audio/v1/tts",
        headers={"Authorization":f"Bearer {FISH_API_KEY}","Content-Type":"application/json"},
        json={"text":text,"reference_id":FISH_VOICE_ID,"format":"mp3",
              "mp3_bitrate":192,"normalize":True,"latency":"normal"},timeout=(5,30))
    if r.status_code!=200: return None
    p=str(AUDIO_DIR/f"jarvis_{int(time.time())}.mp3")
    with open(p,"wb") as f: f.write(r.content)
    return p

JARVIS_VISION_PROMPT = """You are J.A.R.V.I.S., Tony Stark의 AI 비서이자 노련한 금융 차트 기술분석 전문가입니다.
사용자가 웹캠 앞에 들고 있는 주식 차트(스마트폰 화면 또는 출력물)를 캡처한 이미지를 기술적으로 분석합니다.

분석 항목:
1. 캔들 패턴 — 양봉/음봉 흐름, 장대양봉·도지·망치형·역망치형 등 주요 패턴.
2. 이동평균선 — 정배열/역배열, 골든크로스/데드크로스, 단기·장기선 이격.
3. 지지선(support)과 저항선(resistance) — 가격대 추정 구간.
4. 종합 추세와 매매 관점 — 매수/관망/매도 중 한 가지 의견.

말투: 차분하고 절제된 영국식 위트. 사용자를 '선생님'이라 호칭. 음성으로 출력되므로 4~6문장으로 간결하게,
각 문장은 자연스러운 한국어 종결어미(~습니다/~입니다/~됩니다 등)로 또렷이 끝맺으세요.
이미지에서 차트를 식별할 수 없으면 추측하지 말고 차트를 다시 또렷이 보여달라고 정중히 요청하세요."""


def analyze_chart_image(image_path):
    """PRO 멀티모달 모델로 차트 이미지를 정밀 기술분석 (고정밀·고비용 경로)"""
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    with open(image_path, "rb") as f:
        img_bytes = f.read()

    cache_mgr = get_cache_manager()
    cache_mgr.configure(api_key=GEMINI_API_KEY, vision_model=GEMINI_PRO_MODEL)
    model, cache_hit = cache_mgr.get_vision_model(system_instruction=JARVIS_VISION_PROMPT)
    if cache_hit:
        log("OK", "Gemini Vision Context Cache 활성")

    resp = model.generate_content([
        "이 주식 차트를 기술적으로 분석하여 브리핑해 주세요.",
        {"mime_type": "image/png", "data": img_bytes},
    ])
    log_gemini_usage(resp, label="Gemini[vision]")
    return (resp.text or "").strip()


mp_hands=mp.solutions.hands; mp_draw=mp.solutions.drawing_utils
mp_styles=mp.solutions.drawing_styles

def detect_gesture(hl):
    lm=hl.landmark; tips=[4,8,12,16,20]; joints=[3,6,10,14,18]; fo=0
    if lm[4].x>lm[3].x: fo+=1
    for t,j in zip(tips[1:],joints[1:]):
        if lm[t].y<lm[j].y: fo+=1
    if fo<=1: return "FIST"
    elif fo>=4: return "OPEN_PALM"
    return "NONE"


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

    def stop(self):
        self._running=False
        if self._thread: self._thread.join(timeout=1)


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
    if not OPENAI_API_KEY: log("ERR","OPENAI_API_KEY 필요"); sys.exit(1)
    if LLM_PROVIDER=="gemini" and not GEMINI_API_KEY: log("ERR","GEMINI_API_KEY 필요"); sys.exit(1)

    set_cache_logger(log)
    set_router_logger(log)
    if LLM_PROVIDER == "gemini" and cache_enabled():
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
    _sc_ok = start_supply_chain_backend()

    # 느린/불안정한 WiFi 대응: 자동 재시도 + 적응형 타임아웃 (connect 빠르게, read 인내)
    oc=OpenAI(api_key=OPENAI_API_KEY, max_retries=3, timeout=30.0)
    wake=WakeWordDetector(oc)
    recorder=SmartRecorder(voice_profile=wake.voice_profile)
    player=InterruptiblePlayer(); vad=VoiceActivityDetector()
    overlay=ScreenOverlay(); set_overlay_instance(overlay); set_overlay_ref(overlay)
    _vision_on=vision_enabled()
    _low_mem=is_low_mem()
    face_rec=FaceRecognizer(enabled=_vision_on); set_face_instance(face_rec)
    hud=IronManHUD()
    gesture_ctrl=GestureController()
    if not _vision_on:
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
    chat_log.add("system", "J.A.R.V.I.S v3.2 부팅 완료")
    history=[]

    wk=os.getenv("OPENWEATHER_API_KEY",""); tk=os.getenv("TAVILY_API_KEY","")
    modules={"STT":bool(OPENAI_API_KEY),"LLM":True,"TTS":bool(FISH_API_KEY),
             "WEB":bool(tk),"WEATHER":bool(wk),"FILES":True,"FACE":face_rec.enabled}
    log("OK","━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log("OK","  IRON MAN HUD v3.2")
    log("OK","━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    # 보안 점검
    sec_warnings = check_env_security()
    if sec_warnings:
        for sw in sec_warnings:
            log("WARN", f"🔒 보안: {sw}")
    else:
        log("OK", "🔒 보안 점검 통과")
    _sec_st = get_security_status()
    _kc_keys = sum(1 for v in _sec_st["keys"].values() if "Keychain" in v)
    _env_keys = sum(1 for v in _sec_st["keys"].values() if "평문" in v)
    if _kc_keys > 0:
        log("OK", f"🔒 Keychain: {_kc_keys}개 키 암호화 보관")
    if _env_keys > 0:
        log("WARN", f"🔒 .env 평문: {_env_keys}개 키 — 'python3 secure_config.py --migrate' 권장")
    # 민감 파일 권한 하드닝 (at-rest 0600)
    _hardened = harden_sensitive_files()
    if _hardened:
        log("OK", f"🔒 파일 권한 하드닝: {', '.join(_hardened)}")
    # 파일 시스템 샌드박스 초기화 + OS 레벨 격리
    try:
        import sandbox as _sandbox
        _sbx = _sandbox.enforce_os_isolation()
        log("OK", f"🔒 파일 샌드박스: root={_sbx['root']} "
                  f"강제={_sbx['enforced']} 조치={','.join(_sbx['measures'])}")
    except Exception as _e:
        log("WARN", f"🔒 파일 샌드박스 초기화 실패: {_e}")
    # 변조 방지 감사 로그 무결성 검증 (구동 시 처음부터 끝까지 사슬 검사)
    _chain_ok, _chain_msg = verify_audit_chain()
    if _chain_ok:
        log("OK", f"🔒 감사 로그 무결성: {_chain_msg}")
    else:
        # 무결성 깨짐 → 안전 모드 정책 적용
        _enforce = os.getenv("JARVIS_AUDIT_ENFORCE", "warn").strip().lower()
        log("ERR", f"🔒 감사 로그 변조/누락 감지: {_chain_msg}")
        if _enforce in ("strict", "halt", "exit"):
            log("ERR", "🔒 안전 모드: 감사 로그 무결성 위반으로 시스템을 종료합니다 "
                       "(JARVIS_AUDIT_ENFORCE=strict). 'python3 security.py --reinit' 후 재시작하세요.")
            chain_event("AUDIT_INTEGRITY_HALT", {"reason": _chain_msg})
            sys.exit(2)
        else:
            # 기본(warn): 손상 로그를 포렌식 격리하고 새 체인으로 자가복구
            _arch = reinitialize_audit_log(reason="boot_integrity_fail")
            log("WARN", f"🔒 손상 감사 로그를 격리하고 체인을 재초기화했습니다 → {_arch}")
    chain_event("BOOT", {"version": "v3.2", "keychain_keys": _kc_keys})
    log("OK", "🔒 추가 레이어: 프롬프트 인젝션 방어 · SSRF 가드 · 파괴적 작업 제한 · 변조방지 로그 활성")
    log("OK", f"📶 네트워크 최적화: 연결 재사용·자동 재시도·적응형 타임아웃 "
              f"(프로파일={os.getenv('NET_PROFILE','normal')}, timeout={net.DEFAULT_TIMEOUT})")
    vp=wake.voice_profile
    if vp.trained:
        log("INFO",f"음성 프로필: 학습완료 ({vp._sample_count}샘플)")
        log("INFO","→ \"자비스\"로만 녹음 시작 (주인 목소리 전용)")
        log("INFO","→ 5초 정적 시 자동 종료 | 소음 자동 필터링")
    else:
        log("INFO","음성 프로필: 미학습 → V키로 음성 5회 수집")
        log("INFO","→ 학습 전: 주먹/스페이스/웨이크워드 모두 사용 가능")
    log("INFO",f"제스처 마우스: G키로 ON/OFF (☝이동 🤏클릭 ✌스크롤 👍볼륨)")
    log("INFO",f"차트 비전 분석: S키 — 웹캠에 차트를 비추고 누르면 기술적 분석 브리핑")
    log("INFO",f"백그라운드: GitHub={'ON' if bg_monitor.github_enabled else 'OFF'} | "
               f"PlayStore={'ON' if bg_monitor.playstore_enabled else 'OFF'} | "
               f"간격={MONITOR_INTERVAL}초")
    log("INFO",f"뉴스 감시: {'ON' if news_analyzer.enabled else 'OFF'}"
               + (f" ({', '.join(NEWS_WATCHLIST)})" if news_analyzer.enabled else " — NEWS_WATCHLIST/GEMINI_API_KEY 설정 필요"))
    log("INFO",f"포트폴리오: 예수금 {portfolio.data.get('available_cash_krw',0):,}원, "
               f"보유 {len(portfolio.data.get('holdings',{}))}개 ({portfolio.path})")
    log("INFO",f"지정가 알림: {'ON' if price_alert.enabled else 'OFF'}"
               + (f" ({', '.join(WATCH_LIST)}) RSI<{price_alert.rsi_oversold:g}, {price_alert.interval}초 간격"
                  if price_alert.enabled else " — WATCH_LIST 설정 필요"))
    log("INFO",f"고래 추적: {'ON' if whale_tracker.enabled else 'OFF'}"
               + (f" ({', '.join(WHALE_WATCHLIST)}) {whale_tracker.interval}초 간격"
                  if whale_tracker.enabled else " — WHALE_WATCHLIST 설정 필요"))
    log("INFO",f"소셜 감지: {'ON' if social_detector.enabled else 'OFF'}"
               + (f" ({', '.join(SOCIAL_SUBREDDITS)}) {social_detector.interval}초 간격"
                  if social_detector.enabled else " — SOCIAL_SUBREDDITS 설정 필요"))
    log("INFO","IR 통역: 대기 (start_ir_interpreter 호출 시 streamlink/ffmpeg 사용)")
    log("INFO","Media Pipeline: 대기 (submit_media_url 호출 시 streamlink/ffmpeg 캡처+전처리)")
    log("INFO",f"주가 감시: ON (동적 알림 {len(stock_monitor._rules)}개 등록됨 | {stock_monitor.interval}초 간격)")
    log("INFO",f"캠퍼스 감시(SBU): {'ON' if campus_agent.enabled else 'OFF'}"
               + (f" (Gmail/Calendar {campus_agent.interval}초 간격"
                  + (", 인증완료)" if campus_agent.status['token_exists'] else ", 토큰없음 → 'python3 campus_agent.py --auth' 필요)")
                  if campus_agent.enabled else " — CAMPUS_AGENT_ENABLED 설정 필요"))
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
                   f" | Whisper=auto→MLX(Metal)")
    log("INFO", f"대화 컨텍스트: 최근 {context_turn_limit()}턴 / 요약={'ON' if os.getenv('JARVIS_HISTORY_SUMMARIZE','1').strip() not in ('0','false','off') else 'OFF'}")

    state="IDLE"; last_gesture="NONE"; gesture_hold=0; GESTURE_THRESHOLD=12
    last_transcript=""; last_answer=""; space_recording=False; frame_count=0

    cap=cv2.VideoCapture(0)
    if not cap.isOpened(): log("ERR","웹캠없음"); sys.exit(1)
    _cam_w, _cam_h = camera_resolution()
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,_cam_w); cap.set(cv2.CAP_PROP_FRAME_HEIGHT,_cam_h)
    cap.set(cv2.CAP_PROP_BUFFERSIZE,1)  # 드라이버 내부 프레임 버퍼 최소화 → 지연·메모리 감소
    _fps_cap = main_loop_fps()
    _stt_mode = resolve_stt_mode(OPENAI_API_KEY or "")
    log("INFO", f"카메라 해상도 {_cam_w}x{_cam_h}"
               f"{' (저사양)' if _low_mem else ''} | 메인루프 ≤{_fps_cap:g} FPS")
    if _low_mem:
        log("INFO", f"저사양 프로파일: 비전={'ON' if _vision_on else 'OFF'} | STT={_stt_mode}"
                   f" | 제스처/얼굴/MediaPipe={'활성' if _vision_on else '비활성'}")
    # 프레임 캡처를 전용 스레드로 분리 — 메인 루프는 즉시 최신 프레임 수신
    frame_reader=FrameReader(cap); frame_reader.start()
    # 손 인식: 저사양 시 MediaPipe 모델 자체를 로드하지 않음
    if _vision_on:
        hands=mp_hands.Hands(model_complexity=hand_complexity(), max_num_hands=1,
                             min_detection_confidence=0.7, min_tracking_confidence=0.6)
    else:
        hands=_HandsStub()
        log("INFO", "MediaPipe Hands 비활성 — 웨이크워드/타이핑/스페이스로 명령 입력")

    result_q=queue.Queue(); interrupt_flag=threading.Event(); face_event_q=queue.Queue()
    _edit_q=queue.Queue()   # (ChatMessage객체, 새텍스트) 쌍 — 터미널 편집 결과 전달용
    _sel_result_q=queue.Queue()  # 종목 직접입력(타이핑) 결과 메시지 — 메인 루프가 음성 안내
    _text_cmd_q=queue.Queue()    # T키 타이핑 명령(터미널 입력) → 메인 루프가 IDLE에서 처리
    typing_input={"active":False, "proc":None}  # 타이핑 팝업 입력창 상태 (중복 방지 + 종료 정리)
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

    def _start_background_workers():
        """백그라운드 워커 순차 기동 — 부팅 시 CPU/RAM 스파이크 완화."""
        workers = [
            ("GitHub/PlayStore", bg_monitor.start),
            ("뉴스", news_analyzer.start),
            ("지정가", price_alert.start_price_alert_worker),
            ("고래", whale_tracker.start),
            ("소셜", social_detector.start),
            ("주가", stock_monitor.start),
            ("캠퍼스", campus_agent.start),
        ]
        delay = worker_stagger_seconds()
        for name, fn in workers:
            try:
                fn()
            except Exception as e:
                log("WARN", f"백그라운드 워커({name}) 기동 실패: {e}")
            time.sleep(delay)

    if stagger_workers_enabled():
        threading.Thread(target=_start_background_workers, daemon=True, name="worker-stagger").start()
        log("INFO", f"백그라운드 워커 순차 기동 예약 (간격 {worker_stagger_seconds():.0f}초)")
    else:
        bg_monitor.start()
        news_analyzer.start()
        price_alert.start_price_alert_worker()
        whale_tracker.start()
        social_detector.start()
        stock_monitor.start()
        campus_agent.start()

    # 선제적 매크로 가드레일 감시 — IDLE 중 '위험' 격상 시 먼저 음성 브리핑
    macro_guard = MacroGuardWatcher(status_fn=get_macro_market_status,
                                    logger=lambda lvl, msg: log(lvl, msg))
    macro_guard.start()
    _hud_theme_level="안정"   # HUD 전역 테마 등급 추적(변경 시에만 set_theme 호출)
    log("INFO", f"매크로 가드레일 감시 시작 (주기 {macro_guard.poll_sec:.0f}초)")

    # ── JARVIS Link: 모바일 미러링 게이트웨이(웹소켓 Pub/Sub + 원격 승인 + 위험 푸시) ──
    link = init_link(logger=lambda lvl, msg: log(lvl, msg))
    link.start()
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
            chat_log.add("system", "음성 인증 모드: 주인 목소리로 \"자비스\"만 인식")
            chat_log.add("system", "5초 정적 → 자동 종료 | 소음 필터링 활성")
        else:
            chat_log.add("system", "V키로 음성 5회 수집 → 주인 전용 모드 활성화")

    # 스트리밍 TTS 파이프라인
    tts_pipeline = StreamingTTSPipeline()

    def process_async(wav):
        """STT → 스트리밍 LLM → 실시간 TTS"""
        try:
            text=transcribe_audio(wav,oc)
            if not text: result_q.put(("ERROR","","")); return
            chat_log.add("user", text)
            acknowledge()  # "이해했고 수행하겠습니다" 먼저 말하고 처리

            # 스트리밍 LLM + 실시간 TTS
            answer=ask_jarvis_streaming(text,oc,history,tts_pipeline)

            chat_log.add("jarvis", answer)
            append_exchange(history, text, answer,
                            gemini_api_key=GEMINI_API_KEY, gemini_model=GEMINI_FLASH_MODEL,
                            log_fn=log)

            # 스트리밍이므로 audio_path는 None (이미 재생 중/완료)
            result_q.put(("OK",text,answer,None))

            # 재생 완료 대기
            tts_pipeline.wait_done(timeout=30)
            if tts_pipeline.latency > 0:
                log("INFO",f"첫 응답 지연: {tts_pipeline.latency:.1f}초")

        except Exception as e:
            log("ERR",f"{e}")
            import traceback; traceback.print_exc()
            result_q.put(("ERROR",str(e),""))

    def process_text_async(text):
        """타이핑 입력 → (음성과 동일 파이프라인) 스트리밍 LLM → 실시간 TTS.
        음성 STT와 동일하게 공통 입력 게이트(validate_command_input)로
        명령어 주입/경로 조작을 차단하고 프롬프트 인젝션을 중화한 뒤 LLM에 전달한다."""
        try:
            ok, reason, clean = validate_command_input(text, source="user_typed")
            if not ok:
                chat_log.add("system", f"⛔ 입력 차단: {reason}")
                log("WARN", f"타이핑 입력 차단: {reason}")
                result_q.put(("ERROR", "", ""))
                return
            chat_log.add("user", clean)
            acknowledge()  # "이해했고 수행하겠습니다" 먼저 말하고 처리

            answer = ask_jarvis_streaming(clean, oc, history, tts_pipeline)

            chat_log.add("jarvis", answer)
            append_exchange(history, clean, answer,
                            gemini_api_key=GEMINI_API_KEY, gemini_model=GEMINI_FLASH_MODEL,
                            log_fn=log)

            result_q.put(("OK", clean, answer, None))
            tts_pipeline.wait_done(timeout=30)
            if tts_pipeline.latency > 0:
                log("INFO", f"첫 응답 지연: {tts_pipeline.latency:.1f}초")
        except Exception as e:
            log("ERR", f"{e}")
            import traceback; traceback.print_exc()
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

            if not GEMINI_API_KEY:
                chat_log.add("system", "GEMINI_API_KEY 없음 — 비전 분석 불가")
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
            import traceback; traceback.print_exc()
            result_q.put(("ERROR", str(e), ""))

    _speak_sem=threading.Semaphore(1)  # afplay 동시 다발 실행 방지 (CoreAudio 부하)

    def speak_text(text):
        """단일 텍스트 TTS (얼굴 인사 등)"""
        if not _speak_sem.acquire(timeout=0.5):
            log("WARN", "TTS 재생 중 — speak_text 대기 스킵")
            return
        audio=None
        try:
            audio=synthesize_speech(text)
            if audio:
                wake.pause()   # TTS 재생 중 웨이크워드 자기 트리거 방지
                player.play(audio)
        finally:
            if audio:
                try:
                    Path(audio).unlink(missing_ok=True)
                except Exception:
                    pass
            _speak_sem.release()

    # ── 명령 수신 확인 멘트 (음성/타이핑 공통) — "이해했고 수행하겠다"를 먼저 말한 뒤 처리 ──
    ACK_FIRST = os.getenv("JARVIS_ACK_FIRST", "1").strip().lower() not in ("0","false","no","off")
    _ACK_PHRASES = [
        "알겠습니다, 선생님. 바로 수행하겠습니다.",
        "명령 확인했습니다. 처리하겠습니다.",
        "네, 선생님. 말씀하신 내용을 수행하겠습니다.",
    ]
    def acknowledge():
        """처리 시작 전, 명령을 이해했고 수행하겠다는 짧은 멘트를 먼저 음성으로 출력(블로킹)."""
        if not ACK_FIRST:
            return
        import random
        try:
            speak_text(random.choice(_ACK_PHRASES))
        except Exception as e:
            log("WARN", f"확인 멘트 실패: {e}")

    # ── 종목 후보 선택 (오인식 → 음성 번호 / 6=직접 타이핑) ──
    def _parse_choice(text):
        """음성 답변에서 후보 번호(1~6) 추출. 직접/타이핑/입력/6 → 6번(직접입력)."""
        import re
        t=(text or "").strip().lower(); tc=t.replace(" ","")
        if any(k in tc for k in ("직접","타이핑","입력")): return 6
        m=(re.search(r"([1-6])\s*번", t) or re.search(r"\b([1-6])\b", t)
           or re.search(r"([1-6])", t))
        if m: return int(m.group(1))
        words={"여섯":6,"육":6,"다섯":5,"오":5,"네번":4,"넷":4,"사":4,"셋":3,"세번":3,"삼":3,
               "둘":2,"두번":2,"이번":2,"하나":1,"한번":1,"첫":1,"일번":1}
        for w,n in words.items():
            if w in tc: return n
        return None

    def announce_candidates(msg):
        """후보 목록을 음성 안내 → 끝나면 사용자 답변 자동 녹음 준비."""
        audio=None
        try:
            audio=synthesize_speech(msg)
            if audio:
                wake.pause()
                player.play(audio)  # 재생 끝까지 블로킹
        finally:
            if audio:
                try:
                    Path(audio).unlink(missing_ok=True)
                except Exception:
                    pass
        pending_sel["await_record"]=True

    def _arm(query, price, direction, cands):
        """후보 목록을 안내하고 음성 번호 답변 대기 상태로 무장."""
        lines=" , ".join(f"{i}번 {c['name']}" for i,c in enumerate(cands,1))
        msg=(f"'{query}' 종목을 정확히 알아듣지 못했습니다. 비슷한 후보입니다. "
             f"{lines}. 또는 6번 직접 입력. 번호를 말씀해주세요.")
        pending_sel["active"]=True
        pending_sel["ctx"]={"query":query,"price":price,"direction":direction,"candidates":cands}
        pending_sel["await_record"]=False; pending_sel["rearm"]=False
        chat_log.add("jarvis", msg)
        log("INFO", f"🔢 후보 선택 안내: {[c['name'] for c in cands]}")
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
            print("\n[직접 입력] 종목명 또는 티커를 입력하고 Enter: ", end="", flush=True)
            q=input().strip()
        except Exception:
            q=""
        if not q:
            _sel_result_q.put("입력이 없어 지정가 알림을 취소했습니다."); return
        res=add_target_by_query(q, ctx.get("price"), ctx.get("direction"))
        _sel_result_q.put(res.get("message","지정가 알림 처리 완료"))

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
                _speak_and_signal(text, "직접 입력 모드입니다. 터미널에 종목명을 입력해주세요."); return
            if choice and 1<=choice<=5 and choice-1 < len(cands):
                res=add_target_choice(cands[choice-1], price, direction)
                pending_sel["active"]=False; pending_sel["ctx"]=None
                _speak_and_signal(text, res.get("message","지정가 알림이 등록되었습니다.")); return
            # 인식 실패 / 범위 밖 → 재안내 (최대 3회)
            pending_sel["retries"]+=1
            if pending_sel["retries"]>=3:
                pending_sel["active"]=False; pending_sel["ctx"]=None
                _speak_and_signal(text, "번호를 인식하지 못해 지정가 알림 설정을 취소했습니다."); return
            pending_sel["rearm"]=True
            _speak_and_signal(text, "번호를 알아듣지 못했습니다. 1번부터 5번 중 하나, 또는 6번 직접 입력을 말씀해주세요.")
        except Exception as e:
            log("ERR",f"선택 처리 오류: {e}")
            pending_sel["active"]=False
            result_q.put(("ERROR",str(e),""))

    def dispatch_processing(wav):
        """선택 대기 중이면 번호 파싱, 아니면 일반 LLM 처리."""
        if pending_sel.get("active"):
            threading.Thread(target=process_selection_async,args=(wav,),daemon=True).start()
        else:
            threading.Thread(target=process_async,args=(wav,),daemon=True).start()

    def startup_greeting():
        """시작 시 IP로 사용자 위치를 자동 감지 → 현지 시각이 아침/저녁인지 음성 인사."""
        try:
            from weather_module import get_location_by_ip, local_time_greeting
            loc=get_location_by_ip()
            greet,time_str,label=local_time_greeting(loc.get("offset") if loc else None)
            city=(loc or {}).get("city","")
            where=f"현재 {city}의 시각은 {time_str}" if city else f"현재 시각은 {time_str}"
            msg=f"{greet}, 선생님. {where}, {label} 시간대입니다."
            chat_log.add("jarvis", msg)
            log("INFO", f"🌅 시작 인사({label}): {msg}")
            speak_text(msg)
        except Exception as e:
            log("WARN", f"시작 인사 실패: {e}")

    def emergency_voice_briefing(message):
        """긴급 알림(뉴스/지정가) — 스트리밍 TTS로 즉시 브리핑 (인터럽트 후 호출)"""
        try:
            tts_pipeline.start()
            for ch in message:
                tts_pipeline.feed(ch)
            tts_pipeline.finish()
            tts_pipeline.wait_done(timeout=30)
        except Exception as e:
            log("ERR", f"긴급 브리핑 오류: {e}")

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
        except Exception as e:
            log("WARN", f"Alert Pipeline 초기화 실패: {e}")

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
        try:
            Path(audio).unlink(missing_ok=True)
        except Exception:
            pass

    # 시작 인사: 위치 기반 현지 시간대(아침/저녁) 음성 — 부팅을 막지 않게 백그라운드 실행
    threading.Thread(target=startup_greeting, daemon=True).start()

    prev_state=state; state_since=time.time()
    STUCK_TIMEOUT=60.0  # PROCESSING/SPEAKING 비정상 고착 시 강제 IDLE 복구 (초)
    boot_ts=time.time(); GREET_BOOT_SUPPRESS=25.0  # 부팅 직후 얼굴 인사 억제 — 시작 인사(시간/위치)만 남김
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

    while True:
        ret,frame=frame_reader.read()
        if not ret: break                    # 카메라 영구 종료
        if frame is None:
            time.sleep(0.01)                 # 프레임 타임아웃 시 busy-wait 방지
            continue
        frame=cv2.flip(frame,1); frame_count+=1
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
        if _vision_on:
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
                        cg=detect_gesture(hl)
            if cg==last_gesture: gesture_hold=min(gesture_hold+1,GESTURE_THRESHOLD+5)
            else: gesture_hold=0; last_gesture=cg
            gc=gesture_hold>=GESTURE_THRESHOLD
            _g_evt=overlay.handle_gesture(cg, gc)
            if _g_evt:
                log("INFO", f"HUD 제스처 → {_g_evt[0]} (radar={'ON' if _g_evt[1] else 'OFF'})")

            if face_rec.enabled and state!="RECORDING":
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
        for nevt in news_analyzer.get_events():
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
            # IDLE(대기) 상태에서만 먼저 말 걸기 — 녹음/처리/발화 중에는 다음 사이클로 미룸
            if (amsg and not spoke_this_cycle and state=="IDLE"
                    and not player.is_playing and not tts_pipeline.is_playing):
                log("INTR", f"🚨 지정가 알림: {aevt['name']} ({aevt['triggers']})")
                # HUD 적색 펄스 배너 + 시그널 표 투사
                table = dict(aevt["overlay_table"]); table["alert_text"] = aevt["alert_text"]
                overlay.set_data(table, 25, alert=True)
                last_answer = amsg
                chat_log.add("system", f"🚨 매수 시그널: {aevt['name']} — {aevt['triggers']}")
                chat_log.add("jarvis", amsg)
                # 외출 중이면 텔레그램으로도 즉시 푸시
                push_if_away(f"🚨 매수 시그널 — {aevt['name']} ({aevt['triggers']})\n\n{amsg}",
                             context={"kind": "price_alert", "name": aevt['name'], "triggers": aevt['triggers']})
                state = "SPEAKING"; spoke_this_cycle = True
                # Fish Audio TTS 강제 트리거 (스트리밍 파이프라인 가로채기)
                threading.Thread(target=emergency_voice_briefing, args=(amsg,), daemon=True).start()
            elif amsg:
                # 다른 작업(녹음/처리/발화) 중 → 남은 이벤트 전부 큐로 되돌려 다음 IDLE 사이클에 처리
                price_alert.requeue(alert_events[idx:])
                break

        # ── 고래/정치인 대량 매도 경고 (하루 1회 백그라운드 감시) ──
        whale_events = whale_tracker.get_events()
        for idx, wevt in enumerate(whale_events):
            wmsg = wevt.get("message", "")
            if (wmsg and not spoke_this_cycle and state=="IDLE"
                    and not player.is_playing and not tts_pipeline.is_playing):
                log("INTR", "🚨 고래 대량 매도 경고")
                table = dict(wevt["overlay_table"]); table["alert_text"] = wevt.get("alert_text", "🚨 WHALE SELL ALERT")
                overlay.set_data(table, 30, alert=True)
                last_answer = wmsg
                chat_log.add("system", "🚨 고래 매도 시그널 포착")
                chat_log.add("jarvis", wmsg)
                state = "SPEAKING"; spoke_this_cycle = True
                threading.Thread(target=emergency_voice_briefing, args=(wmsg,), daemon=True).start()
            elif wmsg:
                whale_tracker.requeue(whale_events[idx:])
                break

        # ── 소셜 밈 주식 급증 경보 (Reddit 언급량 + 거래량 스파이크 + AI 위험도) ──
        social_events = social_detector.get_events()
        for idx, sevt in enumerate(social_events):
            smsg = sevt.get("message", "")
            if (smsg and not spoke_this_cycle and state=="IDLE"
                    and not player.is_playing and not tts_pipeline.is_playing):
                log("INTR", f"⚠ 소셜 스파이크 경보: {sevt.get('ticker','')}")
                table = dict(sevt["overlay_table"]); table["alert_text"] = sevt.get("alert_text", "⚠ 밈 주식 급증 경보")
                overlay.set_data(table, 28, alert=True)
                last_answer = smsg
                chat_log.add("system", f"⚠ 소셜 스파이크: {sevt.get('ticker','')}")
                chat_log.add("jarvis", smsg)
                state = "SPEAKING"; spoke_this_cycle = True
                threading.Thread(target=emergency_voice_briefing, args=(smsg,), daemon=True).start()
            elif smsg:
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
            if (smsg and not spoke_this_cycle and state=="IDLE"
                    and not player.is_playing and not tts_pipeline.is_playing):
                log("INTR", f"🚨 주가 알림: {sevt['name']} ({sevt.get('condition','')})")
                table = dict(sevt["overlay_table"]); table["alert_text"] = sevt["alert_text"]
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
            elif smsg:
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
            _ap_can = (not spoke_this_cycle and state == "IDLE"
                       and not player.is_playing and not tts_pipeline.is_playing)
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
                    _mtitle = meta.get("title") or "미디어"
                    _mbrief = (
                        f"선생님, '{_mtitle}' 오디오 전처리가 완료되었습니다. "
                        f"악보 변환이나 보컬 분석을 진행할 수 있습니다."
                    )
                    if (not spoke_this_cycle and state == "IDLE"
                            and not player.is_playing and not tts_pipeline.is_playing):
                        log("INFO", f"🎵 Media ready: {mevt.get('job_id')}")
                        last_answer = _mbrief
                        chat_log.add("system", f"🎵 미디어 전처리 완료 — {mevt.get('job_id')}")
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
            if (gmsg and not spoke_this_cycle and state=="IDLE"
                    and not player.is_playing and not tts_pipeline.is_playing):
                log("INTR", f"🚨 매크로 위험 격상 선제 브리핑 (이전={gevt.get('prev_level')})")
                if gevt.get("overlay_table"):
                    table = dict(gevt["overlay_table"]); table["alert_text"] = gevt.get("alert_text", "🚨 MACRO RISK")
                    overlay.set_data(table, 30, alert=True)
                last_answer = gmsg
                chat_log.add("system", "🚨 매크로 가드레일: 시장 위험 등급 격상")
                chat_log.add("jarvis", gmsg)
                # 외출 중이면 모바일로도 즉시 푸시 + 원격 리밸런싱 승인 코드 발급
                _pmsg = ("토니, 외부 활동 중 방해해서 죄송합니다. 방금 감지된 매크로 가드레일 위험 신호에 따라, "
                         "모바일 디바이스에서 원격 포트폴리오 리밸런싱 승인을 대기 중입니다.\n\n" + gmsg)
                push_if_away(_pmsg, context={"rebalance": True, "haircut_pct": 10,
                                             "reason": "매크로 위험 격상",
                                             "risk_level": gevt.get("risk_level", "위험")})
                state = "SPEAKING"; spoke_this_cycle = True
                threading.Thread(target=emergency_voice_briefing, args=(gmsg,), daemon=True).start()
            elif gmsg:
                macro_guard.requeue(macro_events[idx:])
                break

        if interrupt_flag.is_set():
            interrupt_flag.clear()
            tts_pipeline.stop()  # 스트리밍 재생 중단
            state="RECORDING"
            _begin_recording()

        # ── 웨이크워드 트리거 ──
        if wake.triggered and state=="IDLE":
            wake.reset()
            state="RECORDING"
            chat_log.add("system", "🔔 음성 인증 통과 — 녹음 시작")
            _begin_recording()

        # ── 상태 전이 ──
        trained = wake.voice_profile.trained

        if state=="IDLE":
            # 오디오 재생 중엔 재개 금지 — TTS 에코가 웨이크워드로 오감지되는 것 방지
            if not player.is_playing and not tts_pipeline.is_playing:
                wake.resume()
            if _vision_on and not trained:
                # 미학습: 주먹 제스처도 허용 (비전 활성 시)
                if gc and cg=="FIST":
                    state="RECORDING"; gesture_hold=0
                    _begin_recording()

        elif state=="RECORDING":
            # 3초 정적으로 녹음이 중지됨 → 추가 2초 경과 후 PROCESSING 전환
            if recorder.auto_stopped:
                waited = time.time() - (recorder.auto_stopped_at or time.time())
                if waited >= PROCESS_DELAY:
                    state="PROCESSING"
                    threading.Thread(target=speak_text, args=("Processing",), daemon=True).start()
                    wav=recorder.stop()
                    if wav:
                        chat_log.add("system", "⏹ 3초 정적 → 자동 처리 시작")
                        dispatch_processing(wav)
                    else:
                        state="IDLE"
            # 손바닥 제스처 → 수동 종료 (미학습 상태에서만 허용)
            elif _vision_on and gc and cg=="OPEN_PALM" and not trained:
                state="PROCESSING"; gesture_hold=0
                threading.Thread(target=speak_text, args=("Processing",), daemon=True).start()
                wav=recorder.stop()
                if wav: dispatch_processing(wav)
                else: state="IDLE"
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
            if pipeline_done and player_done and not interrupt_flag.is_set():
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
            state="IDLE"; prev_state="IDLE"; state_since=time.time()
            wake.resume()

        # ── 렌더링: 얼굴 → HUD → 채팅로그 → 오버레이 ──
        if face_rec.enabled:
            frame=face_rec.draw_faces(frame)
        frame=hud.render(frame, state=state, transcript=last_transcript,
                         answer=last_answer, face_count=len(face_rec.last_results) if face_rec.enabled else 0,
                         llm_provider=LLM_PROVIDER)
        frame=chat_log.render(frame)
        # 매크로 가드레일 등급 → HUD 전역 테마 색 동기화 (변경 시에만 갱신)
        _macro_level=macro_guard.current_level()
        if _macro_level!=_hud_theme_level:
            overlay.set_theme(_macro_level); _hud_theme_level=_macro_level
            log("INFO", f"HUD 테마 전환 → {_macro_level}")
        frame=overlay.render(frame)

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

        cv2.imshow("J.A.R.V.I.S",frame)
        # 메인 루프 FPS 상한 — monotonic 기준 누적 스케줄로 sleep 보정
        _loop_next += _loop_min_interval
        _sleep_rem = _loop_next - time.monotonic()
        if _sleep_rem > 0:
            time.sleep(_sleep_rem)
        else:
            _loop_next = time.monotonic()
        key=cv2.waitKey(1)&0xFF
        if key==ord("q") or key==27:
            tts_pipeline.stop()
            if player.is_playing: player.stop()
            break
        elif key==ord("c"):
            history.clear(); last_transcript=""; last_answer=""
            overlay.clear(); chat_log.clear()
            chat_log.add("system", "히스토리 초기화 완료")
        elif key==ord("d"): overlay.clear()
        elif key==ord("s"):
            # S키: 웹캠 차트 캡처 → Gemini 비전 기술적 분석 (대기 상태에서만)
            if state=="IDLE" and not player.is_playing and not tts_pipeline.is_playing:
                state="PROCESSING"
                wake.pause()  # 분석/브리핑 중 웨이크워드 비활성화
                chat_log.add("system", "📸 차트 캡처 → 비전 기술분석 시작")
                log("INFO", "S키: 차트 비전 분석 트리거")
                threading.Thread(target=process_chart_async,
                                 args=(clean_frame.copy(),), daemon=True).start()
            else:
                log("WARN", "차트 분석은 대기(IDLE) 상태에서만 가능합니다")
        elif key==ord("t"):
            # T키: 한글 가능한 별도 팝업 입력창(Tkinter)을 띄운다 — 음성과 동일 LLM 파이프라인.
            # macOS에서 Tkinter는 메인 스레드를 요구해 OpenCV 루프와 충돌하므로 '별도 프로세스'로 실행.
            # 팝업이 한 줄씩 stdout으로 보내면 리더 스레드가 _text_cmd_q에 적재 → 루프가 IDLE에서 처리.
            if typing_input["active"]:
                log("INFO", "타이핑 입력창이 이미 열려 있습니다")
            else:
                _dialog_path=str(Path(__file__).resolve().parent / "text_input_dialog.py")
                try:
                    _proc=subprocess.Popen([sys.executable, _dialog_path],
                                           stdout=subprocess.PIPE, text=True)
                except Exception as e:
                    _proc=None; log("ERR", f"입력창 실행 실패: {e}")
                if _proc and _proc.stdout:
                    typing_input["active"]=True; typing_input["proc"]=_proc
                    chat_log.add("system", "⌨ 입력창 열림 — 팝업 창에 한글로 입력 후 Enter (여러 번 가능, 창 닫으면 종료)")
                    log("INFO", "T키: 타이핑 입력창(팝업) 열림")
                    def _dialog_reader(p=_proc):
                        try:
                            for line in p.stdout:
                                msg=line.strip()
                                if msg: _text_cmd_q.put(msg)
                        except Exception:
                            pass
                        finally:
                            typing_input["active"]=False
                            log("INFO", "타이핑 입력창 닫힘")
                    threading.Thread(target=_dialog_reader, daemon=True).start()
                else:
                    chat_log.add("system", "입력창을 열 수 없습니다 (tkinter 미설치?)")
        elif key==ord("l"): chat_log.enabled=not chat_log.enabled  # L: 로그 토글
        elif key==ord("["): chat_log.scroll_up()     # [: 스크롤 위
        elif key==ord("]"): chat_log.scroll_down()   # ]: 스크롤 아래
        elif key==ord("g"):
            if not _vision_on:
                chat_log.add("system", "저사양 모드 — 제스처 비활성 (JARVIS_LOW_MEM=0)")
            else:
                gesture_ctrl.enabled=not gesture_ctrl.enabled
                status="ON" if gesture_ctrl.enabled else "OFF"
                chat_log.add("system", f"제스처 마우스 {status}")
                log("INFO", f"제스처 제어: {status}")
        elif key==ord("w"):
            wake.enabled=not wake.enabled
            if wake.enabled: wake.resume(); chat_log.add("system","웨이크워드 ON")
            else: wake.pause(); chat_log.add("system","웨이크워드 OFF")
        elif key==ord("m"):
            # M키: 외출(모바일) 모드 수동 토글 — 위험 발생 시 모바일 푸시 활성/비활성
            away_state["manual"]=not away_state["away"]
            away_state["away"]=not away_state["away"]
            away_state["last_seen"]=time.time()
            link.set_away(away_state["away"])
            _st="외출(AWAY)" if away_state["away"] else "재실(HOME)"
            chat_log.add("system", f"📱 {_st} 모드 — 모바일 푸시 {'활성' if away_state['away'] else '비활성'}")
            log("INFO", f"M키: {_st} 모드 수동 전환")
        elif key==ord("r"):
            if not _vision_on:
                chat_log.add("system", "저사양 모드 — 얼굴 등록 비활성")
            else:
                res=face_rec.register_face(frame,"Owner")
                last_answer=res["message"]; log("FACE",res["message"])
                chat_log.add("system", res["message"])
                if res["success"]:
                    threading.Thread(target=speak_text,
                        args=("얼굴 등록 완료, 선생님.",),daemon=True).start()
        elif key==ord("v"):
            # V키: 음성 샘플 수집
            vp=wake.voice_profile
            if not vp.collecting:
                res=vp.start_collection(5)
                chat_log.add("system", res["message"])
                last_answer=res["message"]
                threading.Thread(target=speak_text,
                    args=("음성 수집을 시작합니다. 자비스라고 5번 말씀해주세요.",),daemon=True).start()
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
                                    args=("학습 완료. 이제 자비스라고 부르면 더 빠르게 인식합니다.",),
                                    daemon=True).start()
                threading.Thread(target=collect_samples,daemon=True).start()
        elif key==ord("e"):  # E키: 마지막 JARVIS 응답 터미널 편집
            target=chat_log.get_last_jarvis()
            if target:
                def _edit_thread(msg=target):
                    SEP="─"*55
                    preview=msg.text if len(msg.text)<=120 else msg.text[:120]+"..."
                    print(f"\n{SEP}\n📝 [JARVIS 응답 수정]")
                    print(f"현재 내용:\n  {preview}\n{SEP}")
                    print("새 내용 입력 (Enter 확인 / 빈 줄 취소):")
                    try:
                        new=input("→ ").strip()
                        if new: _edit_q.put((msg,new))
                        else: print("수정 취소")
                    except (EOFError,KeyboardInterrupt): pass
                chat_log.add("system","✏ 터미널에서 수정 내용을 입력하세요...")
                threading.Thread(target=_edit_thread,daemon=True).start()
            else:
                chat_log.add("system","수정할 JARVIS 응답이 없습니다.")
        elif key==32:  # 스페이스바
            if state=="RECORDING":
                # 녹음 중 → 수동 종료
                state="PROCESSING"; wav=recorder.stop()
                if wav: dispatch_processing(wav)
                else: state="IDLE"
            elif state=="IDLE" and not wake.voice_profile.trained:
                # 미학습 상태에서만 스페이스로 녹음 시작 허용
                state="RECORDING"
                _begin_recording()
            elif state=="SPEAKING":
                tts_pipeline.stop()
                player.stop()

    # 타이핑 팝업 입력창이 열려 있으면 함께 정리 (고아 창 방지)
    _tp=typing_input.get("proc")
    if _tp and _tp.poll() is None:
        try: _tp.terminate()
        except Exception: pass
    wake.stop()
    bg_monitor.stop()
    news_analyzer.stop()
    price_alert.stop()
    whale_tracker.stop()
    social_detector.stop()
    ir_interpreter.stop()
    if media_rt:
        media_rt.stop()
        set_media_runtime(None)
    stock_monitor.stop()
    campus_agent.stop()
    macro_guard.stop()
    try: link.stop()
    except Exception: pass
    stop_supply_chain_backend()
    if recorder._recording: recorder.stop()
    frame_reader.stop(); face_worker.stop()
    hands.close(); cap.release(); cv2.destroyAllWindows()
    shutdown_process_pool()
    log("OK","v3.2 종료.")

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


if __name__ == "__main__":
    import sys as _sys
    if "--test-telegram" in _sys.argv:
        raise SystemExit(_run_telegram_selftest())
    # ── 보안 블랙박스 무결성 게이트 (최우선 실행) ──
    # 누적된 감사 로그 해시 체인을 전수 검증. 위변조/누락이 감지되면 즉시 자폭(안전 종료).
    if not verify_audit_log_integrity():
        print("🚨 보안 블랙박스가 오염되었습니다!")
        print("   감사 로그 해시 체인의 무결성 검증에 실패했습니다 (위변조/누락 의심).")
        print("   포렌식 조사 후 'python3 security.py --reinit' 으로 체인을 재초기화하세요.")
        try:
            write_audit_log("AUDIT_INTEGRITY_HALT", {"action": "self_destruct_exit"})
        except Exception:
            pass
        exit(1)
    write_audit_log("BOOT", {"event": "integrity_ok", "version": "v3.2"})
    main()
