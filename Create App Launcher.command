#!/bin/bash
# 더블클릭 → JARVIS Mac 앱(클릭 실행) 바탕화면 설치
cd "$(dirname "$0")"
chmod +x scripts/make_app_launcher.sh scripts/build_jarvis_app.sh 2>/dev/null || true
./scripts/build_jarvis_app.sh --all
echo ""
echo "  바탕화면의 JARVIS.app 을 더블클릭하면 JARVIS가 실행됩니다."
read -r -p "Enter 키로 종료..." _
