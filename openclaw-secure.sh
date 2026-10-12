#!/usr/bin/env bash
# OpenClaw CLI 래퍼 — Keychain 토큰을 service-env + 현재 셸에 주입 후 openclaw 실행
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="${ROOT}/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then PY=python3; fi
"$PY" "${ROOT}/openclaw_guard.py" --sync-env >/dev/null
# service-env 에서 토큰 로드 (0600, 로컬 전용)
ENV_FILE="${HOME}/.openclaw/service-env/ai.openclaw.gateway.env"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi
exec openclaw "$@"
