#!/usr/bin/env bash
# JARVIS 재시작 (기존 프로세스 종료 후 백그라운드 기동)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"

# 기존 jarvis 종료 (PID 파일 우선)
PIDFILE="${ROOT}/.jarvis.pid"
if [[ -f "$PIDFILE" ]]; then
  OLD=$(cat "$PIDFILE" 2>/dev/null || true)
  if [[ -n "${OLD:-}" ]] && kill -0 "$OLD" 2>/dev/null; then
    echo "◈ 기존 JARVIS(PID $OLD) 종료..."
    kill "$OLD" 2>/dev/null || true
    sleep 2
    kill -9 "$OLD" 2>/dev/null || true
  fi
fi
pkill -f "jarvis_v2.py" 2>/dev/null || true
pkill -f "JARVIS.app/Contents/MacOS/jarvis" 2>/dev/null || true
sleep 1

echo "◈ JARVIS 시작..."
nohup "$PY" jarvis_v2.py >> "${ROOT}/jarvis.log" 2>&1 &
echo $! > "$PIDFILE"
echo "✓ JARVIS PID $(cat "$PIDFILE") — 로그: jarvis.log"
