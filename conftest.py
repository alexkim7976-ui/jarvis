"""
pytest 공통 설정 — 테스트 간 os.environ 오염 방지.

여러 테스트 모듈이 같은 프로세스에서 환경변수를 변경(JARVIS_FREE_ONLY,
LOCAL_INTEL_*, HYBRID_* 등)하면 실행 순서에 따라 다른 모듈의 테스트가
깨질 수 있다. 각 테스트 전후로 os.environ 을 스냅샷·복원해 순서 독립성을 보장한다.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_environ():
    """각 테스트가 변경/추가/삭제한 환경변수를 테스트 종료 시 원상복구."""
    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)
