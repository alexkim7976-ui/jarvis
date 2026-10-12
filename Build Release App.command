#!/bin/bash
# 더블클릭 → 출시용 JARVIS.app + DMG 빌드 후 바탕화면/Applications 설치
set -euo pipefail
cd "$(dirname "$0")"

echo ""
echo "  J.A.R.V.I.S 출시 앱 빌드"
echo "  ────────────────────────"
echo "  FULL 패키지(.venv 포함)로 DMG까지 만듭니다."
echo "  (가벼운 배포: ./scripts/build_release_app.sh --lite)"
echo ""

chmod +x scripts/build_release_app.sh scripts/jarvis_launcher.sh 2>/dev/null || true
./scripts/build_release_app.sh --full

echo ""
echo "  완료:"
echo "    · 바탕화면 / 응용 프로그램의 JARVIS.app"
echo "    · dist/JARVIS-3.2.0-mac.dmg  ← 다른 사람에게 배포"
echo ""
read -r -p "Enter 키로 종료..." _
