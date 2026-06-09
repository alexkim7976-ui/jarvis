#!/bin/bash
# macOS 더블클릭 실행 — Finder에서 이 파일을 더블클릭
cd "$(dirname "$0")"
if [[ ! -d ".venv" ]]; then
  echo "먼저 'Install JARVIS.command' 를 실행하세요."
  read -r -p "Enter…"
  exit 1
fi
./start.sh
code=$?
if [[ $code -ne 0 ]]; then
  echo ""
  echo "종료 코드: $code"
  read -r -p "Enter 키를 누르면 창이 닫힙니다…"
fi
exit $code
