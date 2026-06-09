#!/usr/bin/env bash
# Supply Chain Agent 로컬 실행 (Firestore 미설정 시 in-memory 자동 폴백)
set -euo pipefail
cd "$(dirname "$0")"

load_env_key() {
  local key="$1"
  local file="$2"
  if [[ -f "$file" ]]; then
    local line
    line="$(grep -E "^${key}=" "$file" | tail -1 || true)"
    if [[ -n "$line" ]]; then
      export "${key}=${line#*=}"
    fi
  fi
}

for f in "../.env" ".env"; do
  load_env_key GEMINI_API_KEY "$f"
  load_env_key FIRESTORE_PROJECT_ID "$f"
  load_env_key GEMINI_MODEL "$f"
done

export SUPPLY_CHAIN_STORAGE="${SUPPLY_CHAIN_STORAGE:-memory}"
export SUPPLY_CHAIN_PORT="${SUPPLY_CHAIN_PORT:-8080}"

if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  echo "ERROR: GEMINI_API_KEY required (set in ../.env or environment)"
  exit 1
fi

echo "Starting Supply Chain Agent on :${SUPPLY_CHAIN_PORT} (storage=${SUPPLY_CHAIN_STORAGE})"
exec mvn spring-boot:run -Dspring-boot.run.arguments="--server.port=${SUPPLY_CHAIN_PORT}"
