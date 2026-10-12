#!/bin/bash
# JARVIS.app 진입점 — 번들 Resources 의 런처로 jarvis_v2.py 실행
set -uo pipefail

BUNDLE="$(cd "$(dirname "$0")/../.." && pwd)"
RES="$BUNDLE/Contents/Resources"

export JARVIS_APP_BUNDLE="$BUNDLE"
export CFBundleIdentifier="com.jarvis.assistant"
export __CFBundleIdentifier="com.jarvis.assistant"
export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin:/usr/bin:/bin:/usr/sbin:${PATH:-}"

LAUNCHER="$RES/jarvis_launcher.sh"
if [[ ! -f "$LAUNCHER" ]]; then
  /usr/bin/osascript -e 'display notification "런처 파일 없음 — Install JARVIS App.command 실행" with title "JARVIS"' 2>/dev/null || true
  exit 1
fi

exec /bin/bash "$LAUNCHER"
