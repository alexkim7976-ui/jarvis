#!/usr/bin/env bash
# J.A.R.V.I.S 실행 (Supply Chain 백엔드 자동 기동 포함)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ -d "/opt/homebrew/bin" ]]; then
  export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
elif [[ -d "/usr/local/bin" ]]; then
  export PATH="/usr/local/bin:/usr/local/sbin:$PATH"
fi

if [[ ! -d ".venv" ]]; then
  echo "가상환경 없음. 먼저 ./install.sh 를 실행하세요."
  exit 1
fi

if [[ ! -f ".env" ]]; then
  echo ".env 없음. cp .env.example .env 후 API 키를 입력하세요."
  exit 1
fi

# Homebrew Java/Maven PATH (macOS)
if [[ -d "/opt/homebrew/opt/openjdk@21/bin" ]]; then
  export PATH="/opt/homebrew/opt/openjdk@21/bin:/opt/homebrew/bin:$PATH"
  export JAVA_HOME="/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home"
elif [[ -d "/usr/local/opt/openjdk@21/bin" ]]; then
  export PATH="/usr/local/opt/openjdk@21/bin:/usr/local/bin:$PATH"
  export JAVA_HOME="/usr/local/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home"
fi

# shellcheck disable=SC1091
source ".venv/bin/activate"
exec python jarvis_v2.py
