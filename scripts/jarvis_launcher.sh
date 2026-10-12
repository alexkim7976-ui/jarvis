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
export PYTHONUNBUFFERED=1
export MPLCONFIGDIR="${MPLCONFIGDIR:-$DATA_ROOT/.matplotlib}"
export HF_HOME="${HF_HOME:-$CACHE_DIR/huggingface}"
export XDG_CACHE_HOME="${XDG_CACHE_HOME:-$CACHE_DIR}"
export AUDIO_SAVE_DIR="${AUDIO_SAVE_DIR:-$DATA_ROOT/audio_cache}"
export CFBundleIdentifier="${CFBundleIdentifier:-com.jarvis.assistant}"
export __CFBundleIdentifier="${CFBundleIdentifier:-com.jarvis.assistant}"

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
  if curl -sf --max-time 2 "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1; then
    return 0
  fi
  if [[ -d "/Applications/Ollama.app" ]]; then
    open -g -a Ollama 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      sleep 1
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

request_app_permissions() {
  [[ -n "$BUNDLE" ]] || return 0
  local macos="$BUNDLE/Contents/MacOS"
  if [[ -x "$macos/jarvis-camcheck" ]]; then
    "$macos/jarvis-camcheck" 2>/dev/null || open_privacy_settings
  fi
  if [[ -x "$macos/jarvis-miccheck" ]]; then
    "$macos/jarvis-miccheck" 2>/dev/null || true
  fi
  if [[ -x "$macos/jarvis-loccheck" ]]; then
    "$macos/jarvis-loccheck" 2>/dev/null || open_location_settings
  fi
}

run_setup_wizard() {
  local helper_py=""
  if [[ -x "/usr/bin/python3" ]]; then
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
  [[ -x "$PY" ]] && return 0
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

  # 출시 모드: .env / venv 없으면 설정 마법사
  if [[ "$RELEASE_MODE" == "1" ]]; then
    bootstrap_bundled_venv
    if [[ ! -f "$DATA_ROOT/.env" || ! -x "$PY" ]]; then
      if ! run_setup_wizard; then
        exit 1
      fi
    fi
    bootstrap_bundled_venv
    # 마법사 후에도 없으면 중단
    if [[ ! -f "$DATA_ROOT/.env" ]]; then
      alert "JARVIS 설정 필요" ".env 가 없습니다.\n\n$DATA_ROOT"
      exit 1
    fi
    if [[ ! -x "$PY" ]]; then
      alert "JARVIS 설치 필요" "가상환경(.venv)이 없습니다.\n설정 마법사에서 설치를 완료하세요.\n\n$DATA_ROOT"
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

# 출시 모드에서도 Ollama는 선택
if ! ensure_ollama; then
  notify "J.A.R.V.I.S" "Ollama 미실행 — 클라우드/로컬 폴백으로 계속"
  log_line "WARN: Ollama not ready — continuing"
fi

notify "J.A.R.V.I.S" "시작 중…"
log_line "──── $(date '+%Y-%m-%d %H:%M:%S') JARVIS.app → jarvis_v2.py ────"
log_line "PY=$PY MAIN=$MAIN BUNDLE=$BUNDLE DATA=$DATA_ROOT"

# shellcheck disable=SC1091
source "$DATA_ROOT/.venv/bin/activate"

if [[ -f "$CODE_ROOT/verify_ai_stack.py" ]]; then
  if ! "$PY" "$CODE_ROOT/verify_ai_stack.py" --quick >> "$LOGFILE" 2>&1; then
    notify "J.A.R.V.I.S" "AI 검증 경고 — 시작은 계속합니다"
    log_line "WARN: verify_ai_stack failed — continuing"
  fi
fi

log_line "exec: $PY $MAIN"
cd "$CODE_ROOT" || exit 1
exec -a "J.A.R.V.I.S" "$PY" "$MAIN" >> "$LOGFILE" 2>&1
