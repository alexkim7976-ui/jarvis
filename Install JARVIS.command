#!/bin/bash
# macOS 더블클릭 설치 — Finder에서 이 파일을 더블클릭
cd "$(dirname "$0")"
./install.sh
echo ""
read -r -p "Enter 키를 누르면 창이 닫힙니다…"
