#!/usr/bin/env python3
"""
J.A.R.V.I.S — AI 스택 자동 검증 (재시작 전/후 실행)
"""
from __future__ import annotations

import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(msg: str) -> None:
    print(f"  ✗ {msg}")
    raise SystemExit(1)


def check_ollama() -> None:
    import requests
    url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    try:
        r = requests.get(f"{url}/api/tags", timeout=3)
        if r.status_code != 200:
            _fail(f"Ollama 응답 {r.status_code}")
    except Exception as e:
        _fail(f"Ollama 미실행 — 터미널: ollama serve ({e})")
    _ok("Ollama 서버 연결")


def check_models() -> None:
    from ollama_vision import vision_model_ready
    from ollama_agent import get_stock_model, OLLAMA_MODEL, model_supports_tools

    for label, name in (
        ("대화/도구", OLLAMA_MODEL),
        ("주식/뉴스", get_stock_model()),
    ):
        _ok(f"{label}: {name}")

    vision_name = os.getenv("OLLAMA_VISION_MODEL", "moondream")
    ready, msg = vision_model_ready(vision_name)
    if ready:
        _ok(f"차트비전: {msg}")
    else:
        print(f"  ⚠ 차트비전: {msg[:120]}")
        print("  · S키 차트 분석은 비활성 — 종목명 음성 분석은 정상")

    if not model_supports_tools():
        _fail(f"{OLLAMA_MODEL} — 도구 호출 미지원 (gemma4 권장)")
    _ok(f"{OLLAMA_MODEL} — 도구 호출 지원")


def check_free_mode() -> None:
    from free_mode import apply_free_mode_defaults, free_mode_summary
    apply_free_mode_defaults()
    s = free_mode_summary()
    if not s.get("enabled"):
        print("  · 무료 모드 OFF (JARVIS_FREE_ONLY=0)")
        return
    _ok(f"무료 모드 ON — {s.get('llm')} / {s.get('stock_llm')} / {s.get('vision_llm')}")


def warmup_models() -> None:
    from ollama_llm import ollama_chat
    from ollama_agent import OLLAMA_MODEL, get_stock_model
    t0 = time.time()
    ollama_chat(OLLAMA_MODEL, [{"role": "user", "content": "ping"}], timeout=30)
    _ok(f"워밍업 {OLLAMA_MODEL} ({time.time()-t0:.1f}s)")
    t0 = time.time()
    ollama_chat(get_stock_model(), [{"role": "user", "content": "ping"}], timeout=45)
    _ok(f"워밍업 {get_stock_model()} ({time.time()-t0:.1f}s)")


def check_stock() -> None:
    from 주식_입력받고_확인_py import verify_stock
    r = verify_stock("삼성전자")
    if not r.get("success"):
        _fail(f"지표 검증 실패: {r.get('message')}")
    _ok(f"삼성전자 지표 — RSI 등 {len(r.get('indicators', {}))}개")


def check_routing() -> None:
    from hybrid_router import try_local_tool_execution
    assert try_local_tool_execution("analyze_stock_multi_source", {"query": "NVDA"}) is None
    _ok("교차검증 도구 — local_intelligence hijack 없음")


def main() -> int:
    print("━━━━━━━━ JARVIS AI 스택 검증 ━━━━━━━━")
    quick = "--quick" in sys.argv or os.getenv("JARVIS_APP_LAUNCH", "").strip() == "1"
    check_ollama()
    check_models()
    check_free_mode()
    if not quick:
        check_routing()
        check_stock()
    else:
        print("  · 앱 실행 — 빠른 검증 (라우팅/주식/워밍업 스킵)")
    if not quick and os.getenv("JARVIS_SKIP_WARMUP", "").strip() not in ("1", "true", "yes"):
        print("\n모델 워밍업 중…")
        warmup_models()
    elif quick:
        print("  · 워밍업 스킵 — Jarvis 시작 후 백그라운드 로드")
    print("\n✅ AI 스택 준비 완료 — Jarvis 시작 가능")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
