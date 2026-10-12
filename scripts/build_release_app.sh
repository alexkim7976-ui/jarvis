#!/usr/bin/env bash
# J.A.R.V.I.S — 출시용 macOS 앱 + DMG 빌드
#
# 사용:
#   ./scripts/build_release_app.sh              # 풀 패키지(.venv 포함) + DMG + 설치
#   ./scripts/build_release_app.sh --lite       # 소스만 (첫 실행 시 의존성 설치)
#   ./scripts/build_release_app.sh --no-dmg     # DMG 생략
#   ./scripts/build_release_app.sh --no-install # Desktop/Applications 복사 생략
#
# 결과:
#   dist/JARVIS.app
#   dist/JARVIS-3.2.0-mac.dmg   (기본)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="JARVIS"
VERSION="3.2.0"
BUNDLE_ID="com.jarvis.assistant"
DIST="$ROOT/dist"
APP_DIR="$DIST/${APP_NAME}.app"
CONTENTS="$APP_DIR/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"
APP_PAYLOAD="$RESOURCES/app"

INCLUDE_VENV=1
MAKE_DMG=1
INSTALL_COPIES=1

for arg in "$@"; do
  case "$arg" in
    --lite) INCLUDE_VENV=0 ;;
    --full) INCLUDE_VENV=1 ;;
    --no-dmg) MAKE_DMG=0 ;;
    --no-install) INSTALL_COPIES=0 ;;
    --help|-h)
      sed -n '2,14p' "$0"
      exit 0
      ;;
  esac
done

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  J.A.R.V.I.S 출시 앱 빌드  v${VERSION}"
echo "  모드: $([[ "$INCLUDE_VENV" == "1" ]] && echo 'FULL (.venv 포함)' || echo 'LITE (첫 실행 설치)')"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [[ -f "$ROOT/scripts/generate_jarvis_icon.py" ]]; then
  python3 "$ROOT/scripts/generate_jarvis_icon.py" 2>/dev/null || true
fi

rm -rf "$APP_DIR"
mkdir -p "$MACOS" "$APP_PAYLOAD" "$DIST"

# ── 네이티브 런처 ──
if command -v clang >/dev/null 2>&1; then
  clang -O2 -o "$MACOS/jarvis" "$ROOT/scripts/jarvis_launcher.c"
elif command -v cc >/dev/null 2>&1; then
  cc -O2 -o "$MACOS/jarvis" "$ROOT/scripts/jarvis_launcher.c"
else
  echo "✗ clang/cc 필요 (xcode-select --install)" >&2
  exit 1
fi
chmod +x "$MACOS/jarvis"

# ── 런처·마법사 ──
cp "$ROOT/scripts/jarvis_launcher.sh" "$RESOURCES/jarvis_launcher.sh"
cp "$ROOT/scripts/app_setup_wizard.py" "$RESOURCES/app_setup_wizard.py"
chmod +x "$RESOURCES/jarvis_launcher.sh"
printf '1\n' > "$RESOURCES/release_mode"

# ── TCC 헬퍼 ──
if command -v swiftc >/dev/null 2>&1; then
  swiftc -o "$MACOS/jarvis-camcheck" "$ROOT/scripts/jarvis_camera_prompt.swift" -framework AVFoundation 2>/dev/null && chmod +x "$MACOS/jarvis-camcheck" || true
  swiftc -o "$MACOS/jarvis-miccheck" "$ROOT/scripts/jarvis_mic_prompt.swift" -framework AVFoundation 2>/dev/null && chmod +x "$MACOS/jarvis-miccheck" || true
  swiftc -o "$MACOS/jarvis-loccheck" "$ROOT/scripts/jarvis_location_prompt.swift" -framework CoreLocation 2>/dev/null && chmod +x "$MACOS/jarvis-loccheck" || true
  if [[ -f "$ROOT/scripts/jarvis_tts.swift" ]]; then
    mkdir -p "$APP_PAYLOAD/bin"
    swiftc -O -o "$APP_PAYLOAD/bin/jarvis_tts" "$ROOT/scripts/jarvis_tts.swift" 2>/dev/null && chmod +x "$APP_PAYLOAD/bin/jarvis_tts" || true
  fi
fi

if [[ -f "$ROOT/assets/AppIcon.icns" ]]; then
  cp "$ROOT/assets/AppIcon.icns" "$RESOURCES/AppIcon.icns"
fi

cat > "$CONTENTS/PkgInfo" <<'PKG'
APPL????
PKG

cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>ko</string>
  <key>CFBundleExecutable</key>
  <string>jarvis</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundleIdentifier</key>
  <string>${BUNDLE_ID}</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>JARVIS</string>
  <key>CFBundleDisplayName</key>
  <string>J.A.R.V.I.S</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>3.2</string>
  <key>CFBundleVersion</key>
  <string>${VERSION}</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>LSUIElement</key>
  <false/>
  <key>LSApplicationCategoryType</key>
  <string>public.app-category.productivity</string>
  <key>LSMultipleInstancesProhibited</key>
  <true/>
  <key>NSSupportsSuddenTermination</key>
  <false/>
  <key>NSMicrophoneUsageDescription</key>
  <string>JARVIS가 음성 명령과 웨이크워드를 인식하려면 마이크 접근이 필요합니다.</string>
  <key>NSCameraUsageDescription</key>
  <string>JARVIS HUD와 차트 분석을 위해 웹캠이 필요합니다.</string>
  <key>NSLocationUsageDescription</key>
  <string>JARVIS가 현재 위치와 현지 시각·날씨를 안내하려면 위치 접근이 필요합니다.</string>
  <key>NSLocationWhenInUseUsageDescription</key>
  <string>JARVIS가 위치 기반 안내를 하려면 사용 중 위치 접근이 필요합니다.</string>
  <key>NSAppleEventsUsageDescription</key>
  <string>실행 중 JARVIS 창을 앞으로 가져오기 위해 자동화 권한이 필요합니다.</string>
  <key>LSEnvironment</key>
  <dict>
    <key>JARVIS_APP_LAUNCH</key>
    <string>1</string>
  </dict>
</dict>
</plist>
PLIST

# ── 소스 패키징 (비밀·캐시 제외) ──
echo "▶ 소스 복사 → Contents/Resources/app"
RSYNC_EXCLUDES=(
  --exclude '.git/'
  --exclude '.venv/'
  --exclude '__pycache__/'
  --exclude '*.pyc'
  --exclude '.env'
  --exclude '.credentials/'
  --exclude '.certs/'
  --exclude '.logs/'
  --exclude 'audio_cache/'
  --exclude 'faces/'
  --exclude 'voice_profile/'
  --exclude '.idea/'
  --exclude '.vscode/'
  --exclude '.DS_Store'
  --exclude '.pytest_cache/'
  --exclude '.mplcache/'
  --exclude '.matplotlib/'
  --exclude '.cache/'
  --exclude '.tts_cache/'
  --exclude 'dist/'
  --exclude 'JARVIS.app/'
  --exclude 'supply-chain-agent/target/'
  --exclude 'portfolio.json'
  --exclude '.jarvis.pid'
  --exclude '.jarvis_link_token'
  --exclude '.claude/'
)

if command -v rsync >/dev/null 2>&1; then
  rsync -a "${RSYNC_EXCLUDES[@]}" "$ROOT/" "$APP_PAYLOAD/"
else
  # rsync 없을 때 최소 복사
  cp -R "$ROOT"/*.py "$APP_PAYLOAD/" 2>/dev/null || true
  for d in scripts assets alert_pipeline auth_idp device_trust fds local_intelligence \
           local_rule_engine media_pipeline supply-chain-agent data bin; do
    [[ -e "$ROOT/$d" ]] && cp -R "$ROOT/$d" "$APP_PAYLOAD/"
  done
  cp "$ROOT/requirements.txt" "$APP_PAYLOAD/" 2>/dev/null || true
  cp "$ROOT/.env.example" "$APP_PAYLOAD/" 2>/dev/null || true
fi

# 마법사·런처가 app/scripts 안에도 있도록 보장
mkdir -p "$APP_PAYLOAD/scripts"
cp "$ROOT/scripts/app_setup_wizard.py" "$APP_PAYLOAD/scripts/"
cp "$ROOT/scripts/jarvis_launcher.sh" "$APP_PAYLOAD/scripts/" 2>/dev/null || true

# FULL: 개발자 머신 .venv 동봉 (즉시 실행 가능)
if [[ "$INCLUDE_VENV" == "1" ]]; then
  if [[ -d "$ROOT/.venv" ]]; then
    echo "▶ .venv 동봉 (용량 큼 — 출시 FULL 패키지)"
    mkdir -p "$APP_PAYLOAD/.venv_bundle"
    # Application Support 로 옮기기 쉽게 payload 옆에 두고, 첫 실행 시 복사
    # 앱 번들 안 Resources/venv 로 넣음
    if command -v rsync >/dev/null 2>&1; then
      rsync -a --delete \
        --exclude '__pycache__/' \
        --exclude '*.pyc' \
        --exclude '*.py[co]' \
        "$ROOT/.venv/" "$RESOURCES/venv/"
    else
      cp -R "$ROOT/.venv" "$RESOURCES/venv"
    fi
    # 런처가 DATA에 venv 없으면 Resources/venv 를 복사하도록 마커
    printf '1\n' > "$RESOURCES/bundled_venv"
    echo "✓ bundled venv → Resources/venv"
  else
    echo "⚠ .venv 없음 — LITE 모드로 진행 (첫 실행 시 pip 설치)"
    INCLUDE_VENV=0
  fi
fi

# 출시 README
cat > "$RESOURCES/README_RELEASE.txt" <<EOF
J.A.R.V.I.S ${VERSION} — macOS 앱

설치
  1) JARVIS.app 을 응용 프로그램 폴더로 드래그
  2) 더블클릭으로 실행
  3) 첫 실행 시 API 키 설정 창이 열립니다 (Gemini 권장)
  4) 설정·로그: ~/Library/Application Support/JARVIS/

Gatekeeper
  "확인되지 않은 개발자" 경고 시:
    시스템 설정 → 개인 정보 보호 및 보안 → 그래도 열기
  또는 터미널:
    xattr -cr "/Applications/JARVIS.app"

필수
  - macOS 12+
  - 웹캠·마이크 권한 허용
  - (선택) Ollama.app — 로컬 LLM
  - (FULL 패키지) 동봉된 Python 환경
  - (LITE 패키지) 첫 실행 시 인터넷으로 의존성 설치
EOF

cat > "$RESOURCES/bootstrap_venv.sh" <<'BOOT'
#!/usr/bin/env bash
# FULL 패키지: Resources/venv → Application Support/.venv 1회 복사
set -euo pipefail
DATA="${1:?}"
RES="${2:?}"
if [[ -x "$DATA/.venv/bin/python" ]]; then
  exit 0
fi
if [[ -f "$RES/bundled_venv" && -d "$RES/venv" ]]; then
  echo "bundled venv → $DATA/.venv"
  mkdir -p "$DATA"
  rm -rf "$DATA/.venv"
  ditto "$RES/venv" "$DATA/.venv"
fi
BOOT
chmod +x "$RESOURCES/bootstrap_venv.sh"

# app 쪽 런처 동기화
cp "$RESOURCES/jarvis_launcher.sh" "$APP_PAYLOAD/scripts/jarvis_launcher.sh" 2>/dev/null || true

# ── 서명·격리 해제 ──
if command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "$APP_DIR" 2>/dev/null || true
fi
xattr -cr "$APP_DIR" 2>/dev/null || true

APP_SIZE="$(du -sh "$APP_DIR" | awk '{print $1}')"
echo "✓ 앱 빌드: $APP_DIR  ($APP_SIZE)"

# ── DMG ──
DMG_PATH="$DIST/${APP_NAME}-${VERSION}-mac.dmg"
if [[ "$MAKE_DMG" == "1" ]]; then
  echo "▶ DMG 생성…"
  rm -f "$DMG_PATH"
  STAGE="$DIST/dmg_stage"
  rm -rf "$STAGE"
  mkdir -p "$STAGE"
  ditto "$APP_DIR" "$STAGE/${APP_NAME}.app"
  ln -s /Applications "$STAGE/Applications"
  cp "$RESOURCES/README_RELEASE.txt" "$STAGE/README.txt"
  hdiutil create -volname "J.A.R.V.I.S ${VERSION}" \
    -srcfolder "$STAGE" \
    -ov -format UDZO \
    "$DMG_PATH" >/dev/null
  rm -rf "$STAGE"
  xattr -cr "$DMG_PATH" 2>/dev/null || true
  echo "✓ DMG: $DMG_PATH  ($(du -sh "$DMG_PATH" | awk '{print $1}'))"
fi

install_copy() {
  local dest="$1"
  local label="$2"
  rm -rf "$dest"
  ditto "$APP_DIR" "$dest"
  xattr -cr "$dest" 2>/dev/null || true
  if command -v codesign >/dev/null 2>&1; then
    codesign --force --deep --sign - "$dest" 2>/dev/null || true
  fi
  echo "✓ ${label}: ${dest}"
}

if [[ "$INSTALL_COPIES" == "1" ]]; then
  install_copy "$HOME/Desktop/${APP_NAME}.app" "바탕화면"
  mkdir -p "$HOME/Applications"
  install_copy "$HOME/Applications/${APP_NAME}.app" "응용 프로그램(~/Applications)"
  # /Applications 는 권한 필요할 수 있어 선택
  if [[ -w "/Applications" ]]; then
    install_copy "/Applications/${APP_NAME}.app" "응용 프로그램(/Applications)"
  fi
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  출시 앱 준비 완료"
echo "  · 앱:  $APP_DIR"
[[ "$MAKE_DMG" == "1" ]] && echo "  · DMG: $DMG_PATH"
echo "  · 더블클릭 → 첫 실행 시 API 키 설정"
echo "  · 사용자 데이터: ~/Library/Application Support/JARVIS/"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
