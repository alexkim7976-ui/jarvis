#!/usr/bin/env bash
# JARVIS 생태계 원클릭 설정 — 수동 설정을 자동화
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"
[[ -x "$PY" ]] || PY=python3

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo " JARVIS 생태계 자동 설정"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# 1) campus_schedule.json
if [[ ! -f data/campus_schedule.json ]]; then
  cp data/campus_schedule.json.example data/campus_schedule.json
  echo "✓ campus_schedule.json 생성 (예시)"
fi

# 2) 캘린더에서 시간표 동기화 시도
"$PY" scripts/sync_campus_schedule.py || true

# 3) .env 필수값 보장
touch .env
grep -q '^JARVIS_ECOSYSTEM_ROUTER=' .env || echo 'JARVIS_ECOSYSTEM_ROUTER=1' >> .env
grep -q '^JARVIS_MAC_CALENDAR=' .env || echo 'JARVIS_MAC_CALENDAR=1' >> .env
grep -q '^JARVIS_LLM_BRAIN=' .env || echo 'JARVIS_LLM_BRAIN=1' >> .env
grep -q '^CAMPUS_SCHEDULE_FILE=' .env || echo 'CAMPUS_SCHEDULE_FILE=data/campus_schedule.json' >> .env

# 4) ZFoldMacBridge Termux 스크립트 생성
"$PY" scripts/zfold_bridge_notify.py --write-termux

# 5) Calendar 자동화 권한 (macOS) — 실패해도 계속
if [[ "$(uname)" == "Darwin" ]]; then
  osascript -e 'tell application "Calendar" to return name of calendar 1' >/dev/null 2>&1 \
    && echo "✓ Calendar.app 접근 OK" \
    || echo "⚠ Calendar.app 권한 필요 — 시스템 설정 → 개인정보 → 자동화 → Terminal/JARVIS → Calendar 허용"
fi

# 6) Ollama 확인
if curl -sf http://localhost:11434/api/tags >/dev/null; then
  echo "✓ Ollama 실행 중"
else
  echo "⚠ Ollama 미실행 — ollama serve 실행 필요"
fi

# 7) 스모크 테스트
echo ""
echo "▶ 생태계 스모크 테스트..."
"$PY" -m pytest test_ecosystem.py test_mac_control.py -q --tb=no 2>/dev/null \
  && echo "✓ 테스트 통과" || echo "⚠ 일부 테스트 실패 (무시하고 계속)"

# 8) Link 토큰 안내
if [[ -f .jarvis_link_token ]]; then
  echo ""
  echo "📱 ZFoldMacBridge 토큰: $(cat .jarvis_link_token)"
  echo "   폴드 Termux: zfold_bridge/termux_notify.sh \"앱\" \"내용\""
fi

echo ""
echo "✅ 설정 완료 — JARVIS 재시작: python3 jarvis_v2.py"
echo "   또는 JARVIS.app 실행"
