#!/usr/bin/env bash
# J.A.R.V.I.S — Mac 앱 번들 런처
# 1) 출시 모드: Resources/app + ~/Library/Application Support/JARVIS
# 2) 개발 모드: Resources/project_root → 소스 트리 .venv
set -uo pipefail

_resolve_bundle_resources() {
  local bundle="${JARVIS_APP_BUNDLE:-}"
  if [[ -n "$bundle" && -d "$bundle/Contents/Resources" ]]; then
    printf '%s' "$bundle/Contents/Resources"
    return 0
  fi
  if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
    local here
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    printf '%s' "$here"
    return 0
  fi
  printf '%s' "$(cd "$(dirname "$0")" && pwd)"
}

RESOURCES="$(_resolve_bundle_resources)"
BUNDLE="${JARVIS_APP_BUNDLE:-}"
RELEASE_MARKER="$RESOURCES/release_mode"
BUNDLED_APP="$RESOURCES/app"

# ── 모드 판별 ──
RELEASE_MODE=0
if [[ -f "$RELEASE_MARKER" ]] || [[ -f "$BUNDLED_APP/jarvis_v2.py" ]]; then
  RELEASE_MODE=1
fi

if [[ "$RELEASE_MODE" == "1" ]]; then
  CODE_ROOT="$BUNDLED_APP"
  DATA_ROOT="${JARVIS_DATA_DIR:-$HOME/Library/Application Support/JARVIS}"
  mkdir -p "$DATA_ROOT"
  ROOT="$CODE_ROOT"
else
  _resolve_dev_root() {
    if [[ -n "${JARVIS_PROJECT_ROOT:-}" ]]; then
      printf '%s' "$JARVIS_PROJECT_ROOT"
      return 0
    fi
    if [[ -f "$RESOURCES/project_root" ]]; then
      tr -d '\n\r' < "$RESOURCES/project_root"
      return 0
    fi
    if [[ -f "$RESOURCES/../scripts/jarvis_launcher.sh" ]]; then
      cd "$RESOURCES/.." && pwd
      return 0
    fi
    cd "$(dirname "$0")/.." && pwd
  }
  ROOT="$(_resolve_dev_root)"
  CODE_ROOT="$ROOT"
  DATA_ROOT="$ROOT"
fi

cd "$CODE_ROOT" || exit 1

PIDFILE="$DATA_ROOT/.jarvis.pid"
LOGDIR="$DATA_ROOT/.logs"
CACHE_DIR="$DATA_ROOT/.cache"
mkdir -p "$LOGDIR" "$CACHE_DIR" "$DATA_ROOT/.matplotlib" "$DATA_ROOT/audio_cache" "$DATA_ROOT/data"
LOGFILE="$LOGDIR/jarvis_app.log"
PY="$DATA_ROOT/.venv/bin/python"
MAIN="$CODE_ROOT/jarvis_v2.py"

export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/local/sbin:/usr/bin:/bin:/usr/sbin:${PATH:-}"
export JARVIS_APP_LAUNCH=1
export JARVIS_APP_BUNDLE="${BUNDLE}"
export JARVIS_PROJECT_ROOT="$CODE_ROOT"
export JARVIS_DATA_DIR="$DATA_ROOT"
export JARVIS_SKIP_WARMUP=1
export JARVIS_SKIP_BOOT_AUDIT=1
export JARVIS_SKIP_OPENCLAW_BOOT=1
export JARVIS_FAST_BOOT=1
export JARVIS_SKIP_AI_VERIFY=1
export JARVIS_LOCATION_TIMEOUT=2.5
export PYTHONUNBUFFERED=1
export MPLCONFIGDIR="${MPLCONFIGDIR:-$DATA_ROOT/.matplotlib}"
export HF_HOME="${HF_HOME:-$CACHE_DIR/huggingface}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$CACHE_DIR}"
export AUDIO_SAVE_DIR="${AUDIO_SAVE_DIR:-$DATA_ROOT/audio_cache}"
export CFBundleIdentifier="${CFBundleIdentifier:-com.jarvis.assistant}"
export __CFBundleIdentifier="${CFBundleIdentifier:-com.jarvis.assistant}"
# 디버그·타이밍 로그는 환경변수로만 켠다. 릴리스 기본값은 꺼짐.
export JARVIS_DEBUG="${JARVIS_DEBUG:-0}"
export JARVIS_TIMING_LOG="${JARVIS_TIMING_LOG:-0}"
export GLOG_minloglevel="${GLOG_minloglevel:-2}"
export ABSL_MIN_LOG_LEVEL="${ABSL_MIN_LOG_LEVEL:-2}"
export TF_CPP_MIN_LOG_LEVEL="${TF_CPP_MIN_LOG_LEVEL:-3}"

# 출시 모드: 코드 트리에 쓰기 가능한 경로 심볼릭 링크
if [[ "$RELEASE_MODE" == "1" ]]; then
  for link_name in audio_cache .logs .cache .matplotlib data faces voice_profile; do
    target="$DATA_ROOT/$link_name"
    link_path="$CODE_ROOT/$link_name"
    mkdir -p "$target"
    if [[ -L "$link_path" ]]; then
      :
    elif [[ -e "$link_path" && ! -L "$link_path" ]]; then
      :
    else
      ln -sfn "$target" "$link_path" 2>/dev/null || true
    fi
  done
  # .env 는 데이터 폴더에만 두고 코드 쪽에 링크
  if [[ -f "$DATA_ROOT/.env" ]]; then
    ln -sfn "$DATA_ROOT/.env" "$CODE_ROOT/.env" 2>/dev/null || true
  fi
fi

log_line() {
  # 상세 런처 로그는 JARVIS_DEBUG=1 일 때만 파일에 남긴다.
  [[ "${JARVIS_DEBUG:-0}" == "1" ]] || return 0
  echo "$1" >> "$LOGFILE"
}

alert() {
  log_line "ALERT: $1 — $2"
  /usr/bin/osascript -e "display alert \"${1//\"/\\\"}\" message \"${2//\"/\\\"}\"" 2>/dev/null \
    || notify "$1" "$2"
}

notify() {
  /usr/bin/osascript -e "display notification \"${2//\"/\\\"}\" with title \"${1//\"/\\\"}\"" 2>/dev/null || true
}

ensure_ollama() {
  local max_wait="${1:-10}"
  if curl -sf --max-time 2 "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1; then
    return 0
  fi
  if [[ -d "/Applications/Ollama.app" ]]; then
    open -g -a Ollama 2>/dev/null || true
    local i=0
    while (( i < max_wait )); do
      sleep 1
      i=$((i + 1))
      curl -sf --max-time 2 "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1 && return 0
    done
  fi
  return 1
}

open_privacy_settings() {
  open "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera" 2>/dev/null || true
}

open_location_settings() {
  open "x-apple.systempreferences:com.apple.preference.security?Privacy_LocationServices" 2>/dev/null || true
}

_run_with_timeout() {
  local secs="$1"
  shift
  "$@" 2>/dev/null &
  local pid=$!
  (
    sleep "$secs"
    kill "$pid" 2>/dev/null
  ) &
  wait "$pid" 2>/dev/null
  return 0
}

request_app_permissions() {
  [[ -n "$BUNDLE" ]] || return 0
  local macos="$BUNDLE/Contents/MacOS"
  if [[ -x "$macos/jarvis-camcheck" ]]; then
    _run_with_timeout 2 "$macos/jarvis-camcheck" || open_privacy_settings &
  fi
  if [[ -x "$macos/jarvis-miccheck" ]]; then
    _run_with_timeout 2 "$macos/jarvis-miccheck" &
  fi
  if [[ -x "$macos/jarvis-loccheck" ]]; then
    # 위치 권한 팝업은 HUD 기동 후에도 가능 — 실행 경로에서 길게 막지 않음
    _run_with_timeout 2 "$macos/jarvis-loccheck" || open_location_settings &
  fi
}

venv_healthy() {
  [[ -x "$PY" ]] || return 1
  local stamp="$DATA_ROOT/.venv/.jarvis_venv_ok"
  local req="$CODE_ROOT/requirements.txt"
  if [[ -f "$stamp" ]]; then
    if [[ ! -f "$req" ]] || [[ "$stamp" -nt "$req" ]]; then
      return 0
    fi
  fi
  if "$PY" -c "import cv2, tkinter" >/dev/null 2>&1; then
    touch "$stamp" 2>/dev/null || true
    return 0
  fi
  rm -f "$stamp" 2>/dev/null || true
  return 1
}

ensure_default_env() {
  [[ -f "$DATA_ROOT/.env" ]] && return 0
  local template=""
  for candidate in \
    "$CODE_ROOT/scripts/release.env.example" \
    "$RESOURCES/release.env.example" \
    "$CODE_ROOT/.env.example"; do
    if [[ -f "$candidate" ]]; then
      template="$candidate"
      break
    fi
  done
  if [[ -z "$template" ]]; then
    return 1
  fi
  cp "$template" "$DATA_ROOT/.env"
  chmod 600 "$DATA_ROOT/.env" 2>/dev/null || true
  log_line "seed .env ← $template"
  return 0
}

run_setup_wizard() {
  local helper_py=""
  if [[ -x "$PY" ]]; then
    helper_py="$PY"
  elif [[ -x "/usr/bin/python3" ]]; then
    helper_py="/usr/bin/python3"
  elif command -v python3 >/dev/null 2>&1; then
    helper_py="$(command -v python3)"
  fi
  local wizard=""
  if [[ -f "$CODE_ROOT/scripts/app_setup_wizard.py" ]]; then
    wizard="$CODE_ROOT/scripts/app_setup_wizard.py"
  elif [[ -f "$RESOURCES/app_setup_wizard.py" ]]; then
    wizard="$RESOURCES/app_setup_wizard.py"
  fi
  if [[ -z "$helper_py" || -z "$wizard" ]]; then
    alert "JARVIS 설정 필요" "설정 마법사를 실행할 수 없습니다.\n데이터: $DATA_ROOT"
    return 1
  fi
  notify "J.A.R.V.I.S" "첫 실행 설정을 엽니다…"
  local skip=()
  [[ -x "$PY" ]] && skip=(--skip-deps)
  log_line "setup wizard: $helper_py $wizard ${skip[*]:-}"
  "$helper_py" "$wizard" --data-dir "$DATA_ROOT" --code-dir "$CODE_ROOT" "${skip[@]}" >> "$LOGFILE" 2>&1
  return $?
}

clear_stale_pid() {
  [[ -f "$PIDFILE" ]] || return 0
  local pid
  pid="$(cat "$PIDFILE" 2>/dev/null)" || { rm -f "$PIDFILE"; return 0; }
  if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$PIDFILE"
    return 0
  fi
  if ! ps -p "$pid" -o command= 2>/dev/null | grep -q "jarvis_v2.py"; then
    rm -f "$PIDFILE"
  fi
}

is_jarvis_alive() {
  clear_stale_pid
  [[ -f "$PIDFILE" ]] || return 1
  local pid
  pid="$(cat "$PIDFILE" 2>/dev/null)" || return 1
  kill -0 "$pid" 2>/dev/null && ps -p "$pid" -o command= 2>/dev/null | grep -q "jarvis_v2.py"
}

focus_jarvis_window() {
  /usr/bin/osascript <<'APPLESCRIPT' 2>/dev/null || true
tell application "System Events"
  repeat with p in (every process whose background only is false)
    try
      set pn to name of p
      if pn contains "Python" or pn contains "JARVIS" or pn contains "jarvis" or pn contains "J.A.R.V.I.S" then
        repeat with w in (windows of p)
          try
            if name of w contains "J.A.R.V.I.S" or name of w contains "JARVIS" then
              set frontmost of p to true
              perform action "AXRaise" of w
              return
            end if
          end try
        end repeat
        set frontmost of p to true
        return
      end if
    end try
  end repeat
end tell
APPLESCRIPT
}

bootstrap_bundled_venv() {
  # FULL 패키지: Resources/venv → Application Support/.venv 1회 복사
  [[ "$RELEASE_MODE" == "1" ]] || return 0
  if venv_healthy; then
    return 0
  fi
  log_line "venv missing or broken — refresh from bundled Resources/venv"
  local boot="$RESOURCES/bootstrap_venv.sh"
  if [[ -x "$boot" ]]; then
    "$boot" "$DATA_ROOT" "$RESOURCES" >> "$LOGFILE" 2>&1 || true
    return 0
  fi
  if [[ -f "$RESOURCES/bundled_venv" && -d "$RESOURCES/venv" ]]; then
    log_line "copy bundled venv → $DATA_ROOT/.venv"
    rm -rf "$DATA_ROOT/.venv"
    ditto "$RESOURCES/venv" "$DATA_ROOT/.venv" >> "$LOGFILE" 2>&1 || true
  fi
}

preflight() {
  log_line "preflight CODE=$CODE_ROOT DATA=$DATA_ROOT release=$RELEASE_MODE"
  if [[ ! -f "$MAIN" ]]; then
    alert "JARVIS 파일 없음" "jarvis_v2.py 없음\n\n$MAIN"
    exit 1
  fi

  # 출시 모드: 기본 .env 시드 → venv → (선택) 설정 마법사
  if [[ "$RELEASE_MODE" == "1" ]]; then
    bootstrap_bundled_venv
    ensure_default_env || true
    bootstrap_bundled_venv
    if ! venv_healthy; then
      run_setup_wizard || true
      bootstrap_bundled_venv
    fi
    if [[ ! -f "$DATA_ROOT/.setup_done" && -f "$DATA_ROOT/.env" ]]; then
      run_setup_wizard || true
    fi
    ensure_default_env || true
    if [[ ! -f "$DATA_ROOT/.env" ]]; then
      alert "JARVIS 설정 필요" ".env 가 없습니다.\n\n$DATA_ROOT"
      exit 1
    fi
    if ! venv_healthy; then
      alert "JARVIS 설치 필요" "가상환경(.venv)이 없거나 손상되었습니다.\n터미널: rm -rf \"$DATA_ROOT/.venv\" 후 앱 재실행\n\n$DATA_ROOT"
      exit 1
    fi
    ln -sfn "$DATA_ROOT/.env" "$CODE_ROOT/.env" 2>/dev/null || true
    return 0
  fi

  # 개발 모드
  if [[ ! -d "$DATA_ROOT/.venv" ]]; then
    alert "JARVIS 설치 필요" ".venv 없음. Install JARVIS.command 실행\n\n$DATA_ROOT"
    exit 1
  fi
  if [[ ! -x "$PY" ]]; then
    alert "JARVIS Python 없음" ".venv Python 없음\n\n$DATA_ROOT"
    exit 1
  fi
  if [[ ! -f "$DATA_ROOT/.env" ]]; then
    alert "JARVIS 설정 필요" ".env 파일 없음\n\n$DATA_ROOT"
    exit 1
  fi
  if ! ensure_ollama; then
    notify "J.A.R.V.I.S" "Ollama 준비 중 — Jarvis는 계속 시작합니다"
    log_line "WARN: Ollama not ready — continuing"
  fi
}

# ── 이미 실행 중 → 깨우기 ──
if is_jarvis_alive; then
  touch "$DATA_ROOT/.jarvis.wake"
  focus_jarvis_window
  notify "J.A.R.V.I.S" "이미 실행 중 — 창을 앞으로 가져옵니다"
  exit 0
fi

preflight
request_app_permissions

# 앱 클릭 직후 Python까지 — Ollama는 짧게만 대기(백그라운드에서 계속 기동)
if ! ensure_ollama 2; then
  ( ensure_ollama 15 >> "$LOGFILE" 2>&1 ) &
  notify "J.A.R.V.I.S" "Ollama 준비 중 — Jarvis는 먼저 시작합니다"
  log_line "WARN: Ollama not ready — continuing (background wait)"
fi

notify "J.A.R.V.I.S" "시작 중…"
log_line "──── $(date '+%Y-%m-%d %H:%M:%S') JARVIS.app → jarvis_v2.py ────"
log_line "PY=$PY MAIN=$MAIN BUNDLE=$BUNDLE DATA=$DATA_ROOT"

# shellcheck disable=SC1091
source "$DATA_ROOT/.venv/bin/activate"

if [[ "${JARVIS_SKIP_AI_VERIFY:-0}" != "1" ]] && [[ -f "$CODE_ROOT/verify_ai_stack.py" ]]; then
  if ! "$PY" "$CODE_ROOT/verify_ai_stack.py" --quick >> "$LOGFILE" 2>&1; then
    notify "J.A.R.V.I.S" "AI 검증 경고 — 시작은 계속합니다"
    log_line "WARN: verify_ai_stack failed — continuing"
  fi
fi

log_line "exec: $PY $MAIN"
cd "$CODE_ROOT" || exit 1
# 릴리스 기본: stdout 디버그 로그는 남기지 않는다. JARVIS_DEBUG=1 일 때만 전체 로그.
if [[ "${JARVIS_DEBUG:-0}" == "1" ]]; then
  exec -a "J.A.R.V.I.S" "$PY" "$MAIN" >> "$LOGFILE" 2>&1
fi
exec -a "J.A.R.V.I.S" "$PY" "$MAIN" 2>> "$LOGFILE" >/dev/null
