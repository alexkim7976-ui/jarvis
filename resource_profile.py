"""
J.A.R.V.I.S — 리소스 프로파일 (저사양 / 8GB RAM / Apple Silicon / Windows·Linux 공통)

환경변수 요약:
  JARVIS_LOW_MEM=1          비전(얼굴·손·제스처) OFF, 640×480, 15 FPS, STT→API 우선
  JARVIS_VISION_ENABLED   0/1 — LOW_MEM과 무관하게 비전 강제 OFF/ON
  JARVIS_MAIN_LOOP_FPS    메인 루프 상한 FPS (LOW_MEM 기본 15, 일반 30)
  JARVIS_CAM_WIDTH/HEIGHT 카메라 해상도 (LOW_MEM 기본 640×480)
  JARVIS_STT_BACKEND      auto | local | api
  JARVIS_STT_PROCESS      1 — 로컬 Whisper를 별도 프로세스에서 실행(GIL 회피)
  JARVIS_STAGGER_WORKERS  1 — 부팅 시 백그라운드 워커를 순차 기동(CPU 스파이크 완화)
  JARVIS_WORKER_STAGGER_SEC  워커 간 지연(초, Apple Silicon 기본 4)
"""
from __future__ import annotations

import os
import platform


def is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def is_low_mem() -> bool:
    return os.getenv("JARVIS_LOW_MEM", "").strip().lower() in ("1", "true", "yes", "on")


def vision_enabled() -> bool:
    """얼굴·MediaPipe 손·제스처 마우스 등 비전 파이프라인 사용 여부."""
    if is_low_mem():
        return False
    return os.getenv("JARVIS_VISION_ENABLED", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def camera_resolution() -> tuple[int, int]:
    low = is_low_mem()
    w = int(os.getenv("JARVIS_CAM_WIDTH", "640" if low else "1280"))
    h = int(os.getenv("JARVIS_CAM_HEIGHT", "480" if low else "720"))
    return w, h


def main_loop_fps() -> float:
    default = "15" if is_low_mem() else "30"
    return max(5.0, float(os.getenv("JARVIS_MAIN_LOOP_FPS", default)))


def hand_complexity() -> int:
    if not vision_enabled():
        return 0
    low = is_low_mem()
    return int(os.getenv("JARVIS_HAND_COMPLEXITY", "0" if low else "1"))


def face_worker_interval() -> float:
    return max(0.3, float(os.getenv("JARVIS_FACE_INTERVAL", "0.5")))


def stt_process_enabled() -> bool:
    """로컬 Whisper를 별도 프로세스에서 실행 — Apple Silicon 기본 ON."""
    raw = os.getenv("JARVIS_STT_PROCESS", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return is_apple_silicon()


def stagger_workers_enabled() -> bool:
    """부팅 시 백그라운드 워커 순차 기동 — Apple Silicon 기본 ON."""
    raw = os.getenv("JARVIS_STAGGER_WORKERS", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return is_apple_silicon()


def worker_stagger_seconds() -> float:
    return max(1.0, float(os.getenv("JARVIS_WORKER_STAGGER_SEC", "4" if is_apple_silicon() else "2")))


def performance_profile_summary() -> dict:
    """부팅 로그용 성능 프로파일 요약."""
    return {
        "apple_silicon": is_apple_silicon(),
        "low_mem": is_low_mem(),
        "vision": vision_enabled(),
        "camera": camera_resolution(),
        "main_loop_fps": main_loop_fps(),
        "stt_process": stt_process_enabled(),
        "stagger_workers": stagger_workers_enabled(),
        "worker_stagger_sec": worker_stagger_seconds(),
    }
