#!/usr/bin/env bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  J.A.R.V.I.S — 원클릭 설치 (macOS / Linux / Windows는 install.bat)
#  사용: ./install.sh
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Homebrew PATH (더블클릭 .command 실행 시 PATH 누락 방지)
if [[ -d "/opt/homebrew/bin" ]]; then
  export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
elif [[ -d "/usr/local/bin" ]]; then
  export PATH="/usr/local/bin:/usr/local/sbin:$PATH"
fi

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
step()  { echo -e "${CYAN}▶ $*${NC}"; }
ok()    { echo -e "${GREEN}✓ $*${NC}"; }
warn()  { echo -e "${YELLOW}⚠ $*${NC}"; }
fail()  { echo -e "${RED}✗ $*${NC}"; exit 1; }

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   J.A.R.V.I.S  ·  자동 설치                      ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── 1. OS / Python ──
step "환경 확인"
OS="$(uname -s)"
if [[ "$OS" != "Darwin" && "$OS" != "Linux" ]]; then
  warn "macOS/Linux 외 OS — 일부 기능(웹캠/마이크/Keychain)이 제한될 수 있습니다."
fi

if ! command -v python3 &>/dev/null; then
  fail "python3 없음. https://www.python.org/downloads/ 에서 설치하세요."
fi

PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_MAJOR="$(echo "$PY_VER" | cut -d. -f1)"
PY_MINOR="$(echo "$PY_VER" | cut -d. -f2)"
if [[ "$PY_MAJOR" -lt 3 ]] || [[ "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 9 ]]; then
  fail "Python 3.9+ 필요 (현재: $PY_VER)"
fi
ok "Python $PY_VER"

# ── 2. Homebrew 시스템 패키지 (macOS) ──
install_brew_pkg() {
  local pkg="$1"
  if brew list "$pkg" &>/dev/null 2>&1; then
    ok "brew: $pkg (설치됨)"
  else
    step "brew install $pkg"
    brew install "$pkg"
    ok "brew: $pkg"
  fi
}

if [[ "$OS" == "Darwin" ]]; then
  step "macOS 시스템 의존성"
  if ! command -v brew &>/dev/null; then
    warn "Homebrew 없음 — portaudio/ffmpeg/maven/java 수동 설치 필요"
    warn "  /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
  else
    install_brew_pkg portaudio
    install_brew_pkg ffmpeg
    install_brew_pkg maven
    install_brew_pkg openjdk@21
    install_brew_pkg streamlink || warn "streamlink 선택 실패 (IR 통역 기능만 영향)"
    # Java PATH (현재 셸 + install 스크립트용)
    if [[ -d "/opt/homebrew/opt/openjdk@21" ]]; then
      export PATH="/opt/homebrew/opt/openjdk@21/bin:$PATH"
      export JAVA_HOME="/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home"
    elif [[ -d "/usr/local/opt/openjdk@21" ]]; then
      export PATH="/usr/local/opt/openjdk@21/bin:$PATH"
      export JAVA_HOME="/usr/local/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home"
    fi
    ok "Java: $(java -version 2>&1 | head -1)"
  fi
elif [[ "$OS" == "Linux" ]]; then
  step "Linux 시스템 의존성 (sudo 필요할 수 있음)"
  if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y portaudio19-dev python3-dev ffmpeg openjdk-21-jdk maven streamlink 2>/dev/null \
      || warn "일부 apt 패키지 설치 실패 — 수동 확인 필요"
  else
    warn "apt 없음 — portaudio, ffmpeg, java 21+, maven 수동 설치"
  fi
fi

# ── 3. Python 가상환경 ──
step "Python 가상환경 (.venv)"
if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
  ok "가상환경 생성"
else
  ok "가상환경 존재"
fi
# shellcheck disable=SC1091
source ".venv/bin/activate"
python -m pip install -U pip wheel setuptools -q
ok "pip 업그레이드"

step "Python 패키지 설치 (수 분 소요)"
pip install -r requirements.txt -q
ok "requirements.txt 설치 완료"

# ── 4. 디렉터리 / .env ──
step "데이터 디렉터리"
for d in audio_cache faces voice_profile .logs .credentials .mplcache; do
  mkdir -p "$d"
done
chmod 700 .logs .credentials 2>/dev/null || true
ok "디렉터리 준비"

if [[ ! -f ".env" ]]; then
  cp .env.example .env
  chmod 600 .env
  ok ".env 생성 (.env.example → .env) — API 키를 입력하세요!"
  warn "  nano .env  또는  open -e .env"
else
  ok ".env 이미 존재 (유지)"
fi

# ── 5. Java Supply Chain 백엔드 사전 빌드 ──
if [[ -d "supply-chain-agent" ]] && command -v mvn &>/dev/null; then
  step "Supply Chain Agent JAR 빌드 (첫 실행 속도 향상)"
  (cd supply-chain-agent && mvn -q package -DskipTests) && ok "JAR 빌드 완료" \
    || warn "JAR 빌드 실패 — 실행 시 Maven으로 자동 빌드됩니다"
else
  warn "Maven/supply-chain-agent 없음 — 밸류체인 분석 백엔드 스킵"
fi

# ── 6. 실행 스크립트 권한 ──
chmod +x start.sh install.sh 2>/dev/null || true
chmod +x supply-chain-agent/run.sh 2>/dev/null || true
[[ -f "Install JARVIS.command" ]] && chmod +x "Install JARVIS.command"
[[ -f "Start JARVIS.command" ]] && chmod +x "Start JARVIS.command"

# ── 7. macOS 더블클릭 런처 경로 기록 ──
if [[ "$OS" == "Darwin" ]]; then
  cat > ".jarvis_path" <<EOF
$ROOT
EOF
fi

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  설치 완료!${NC}"
echo ""
echo "  1) API 키 설정:  nano .env"
echo "  2) 실행:"
echo "       ./start.sh"
echo "     또는 macOS:  'Start JARVIS.command' 더블클릭"
echo ""
echo "  (선택) Keychain 마이그레이션:  python3 secure_config.py --migrate"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
