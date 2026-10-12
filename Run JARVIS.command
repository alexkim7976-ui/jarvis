#!/bin/bash
# JARVIS 직접 실행 (앱 대신 터미널에서 jarvis_v2.py)
cd "$(dirname "$0")"
export JARVIS_PROJECT_ROOT="$PWD"
exec /bin/bash "$PWD/scripts/jarvis_launcher.sh"
