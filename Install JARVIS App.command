#!/bin/bash
# JARVIS Mac 앱 설치 — 더블클릭하면 바탕화면 + 응용 프로그램에 JARVIS.app 생성
set -euo pipefail
cd "$(dirname "$0")"

echo ""
echo "  J.A.R.V.I.S Mac 앱 설치"
echo "  ────────────────────────"

if [[ ! -d ".venv" ]]; then
  echo ""
  echo "  ⚠ .venv 없음 — 먼저 Install JARVIS.command 로 Python 환경을 설치하세요."
  osascript -e 'display alert "JARVIS" message "먼저 Install JARVIS.command 로 가상환경을 설치하세요."' 2>/dev/null || true
  read -r -p "Enter 키로 종료..." _
  exit 1
fi

chmod +x scripts/build_jarvis_app.sh scripts/jarvis_launcher.sh 2>/dev/null || true
./scripts/build_jarvis_app.sh --all

echo ""
read -r -p "Enter 키로 종료..." _
