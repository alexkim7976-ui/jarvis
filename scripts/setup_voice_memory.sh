#!/usr/bin/env bash
# JARVIS 2~5번 모듈 원클릭 설정 — 웨이크워드·제스처·SQLite 메모리
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VENV="${ROOT}/.venv"
PY="${VENV}/bin/python"
PIP="${VENV}/bin/pip"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " JARVIS 귀·손·기억 자동 설정"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# venv
if [[ ! -x "$PY" ]]; then
  echo "✗ .venv 없음 — 먼저 install.sh 또는 python -m venv .venv 실행 필요"
  exit 1
fi

# 패키지
echo "◈ openWakeWord 설치 확인..."
"$PIP" install -q 'openwakeword>=0.6.0' onnxruntime 2>/dev/null || "$PIP" install -q 'openwakeword>=0.6.0'

# openWakeWord 모델 사전 다운로드
echo "◈ openWakeWord 모델 다운로드..."
"$PY" - <<'PY'
from openwakeword.utils import download_models
download_models()
from openwakeword.model import Model
m = Model(wakeword_models=["hey_jarvis"])
print("✓ openWakeWord 모델 준비:", list(m.models.keys()))
PY

# data 디렉터리 + SQLite 초기화
mkdir -p data
echo "◈ SQLite 메모리 DB 초기화..."
"$PY" - <<'PY'
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(".env"))
from memory_store import init_db, stats, close_db
init_db()
st = stats()
print(f"✓ 메모리 DB: {st.get('db')} (messages={st.get('messages',0)})")
close_db()
PY

# 검증
echo "◈ 모듈 검증..."
"$PY" - <<'PY'
from wake_engine import wake_engine_mode, oww_enabled, matches_wake_word, get_oww_frontend
from voice_gesture import voice_gestures_enabled
import os

assert wake_engine_mode() == os.getenv("JARVIS_WAKE_ENGINE", "hybrid")
assert matches_wake_word("자비스야")
assert voice_gestures_enabled()
oww = get_oww_frontend()
assert oww.load(), "openWakeWord 로드 실패"
print("✓ wake_engine / voice_gesture / memory_store OK")
PY

echo "◈ pytest..."
"$PY" -m pytest test_wake_engine.py test_memory_store.py test_voice_gesture.py -q

echo ""
echo "✓ 설정 완료 — JARVIS 재시작만 하면 적용됩니다."
echo "  python3 jarvis_v2.py  또는  JARVIS.app 더블클릭"
