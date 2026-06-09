#!/usr/bin/env bash
# 배포용 ZIP 생성 (비밀·캐시·venv 제외)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
NAME="jarvis-v3.2-$(date +%Y%m%d)"
OUT="/tmp/${NAME}.zip"

echo "배포 ZIP 생성: $OUT"

zip -r "$OUT" . \
  -x "*.git*" \
  -x "*/.venv/*" \
  -x "*/__pycache__/*" \
  -x "*/.env" \
  -x "*/.credentials/*" \
  -x "*/.logs/*" \
  -x "*/audio_cache/*" \
  -x "*/faces/*" \
  -x "*/voice_profile/*" \
  -x "*/.idea/*" \
  -x "*/.DS_Store" \
  -x "*/supply-chain-agent/target/*" \
  -x "*/.mplcache/*" \
  -x "*/portfolio.json" \
  -x "*/test.mp3"

echo "✓ $OUT"
echo ""
echo "배포 방법:"
echo "  1) ZIP을 사용자에게 전달"
echo "  2) 압축 해제 후:"
echo "       macOS:  'Install JARVIS.command' 더블클릭"
echo "       Windows: 'Install JARVIS.bat' 더블클릭"
echo "       Linux:  ./install.sh"
echo "  3) .env에 API 키 입력"
echo "  4) macOS: 'Start JARVIS.command' / Windows: 'Start JARVIS.bat' / Linux: ./start.sh"
