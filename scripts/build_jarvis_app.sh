#!/usr/bin/env bash
# JARVIS.app — macOS 더블클릭 → jarvis_v2.py
# 사용:
#   ./scripts/build_jarvis_app.sh              # 프로젝트 내 JARVIS.app 빌드
#   ./scripts/build_jarvis_app.sh --all        # 바탕화면 + ~/Applications 설치
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="JARVIS"
APP_DIR="$ROOT/${APP_NAME}.app"
CONTENTS="$APP_DIR/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"
VERSION="3.2.0"
BUNDLE_ID="com.jarvis.assistant"

echo "▶ JARVIS Mac 앱 빌드"
echo "   프로젝트: $ROOT"

if [[ -f "$ROOT/scripts/generate_jarvis_icon.py" ]]; then
  python3 "$ROOT/scripts/generate_jarvis_icon.py" 2>/dev/null || true
fi

rm -rf "$APP_DIR"
mkdir -p "$MACOS" "$RESOURCES"

# 앱이 어디에 복사돼도 jarvis_v2.py 경로 유지
printf '%s' "$ROOT" > "$RESOURCES/project_root"
chmod 644 "$RESOURCES/project_root"

# 런처 스크립트를 앱 번들에 내장 (프로젝트 scripts 경로에 의존하지 않음)
cp "$ROOT/scripts/jarvis_launcher.sh" "$RESOURCES/jarvis_launcher.sh"
chmod +x "$RESOURCES/jarvis_launcher.sh"

# 메인 실행 파일 — 반드시 Mach-O 바이너리여야 한다.
# (최신 macOS launchd 는 셸 스크립트 main executable spawn 을 거부: errno 162)
if command -v clang >/dev/null 2>&1; then
  clang -O2 -o "$MACOS/jarvis" "$ROOT/scripts/jarvis_launcher.c"
  echo "✓ 네이티브 런처 바이너리 빌드 (clang)"
elif command -v cc >/dev/null 2>&1; then
  cc -O2 -o "$MACOS/jarvis" "$ROOT/scripts/jarvis_launcher.c"
  echo "✓ 네이티브 런처 바이너리 빌드 (cc)"
else
  echo "✗ clang/cc 가 없어 네이티브 런처를 빌드할 수 없습니다. Xcode Command Line Tools 설치 필요: xcode-select --install" >&2
  exit 1
fi
chmod +x "$MACOS/jarvis"

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
  <string>JARVIS가 현재 위치(도시·지역)와 현지 시각·날씨를 안내하려면 위치 접근이 필요합니다.</string>
  <key>NSLocationWhenInUseUsageDescription</key>
  <string>JARVIS가 "내 위치 어디야" 등 위치 기반 안내를 하려면 사용 중 위치 접근이 필요합니다.</string>
  <key>NSAppleEventsUsageDescription</key>
  <string>실행 중 JARVIS 창을 앞으로 가져오기 위해 자동화 권한이 필요합니다.</string>
  <key>LSEnvironment</key>
  <dict>
    <key>MPLCONFIGDIR</key>
    <string>REPLACE_MPLCONFIGDIR</string>
    <key>JARVIS_APP_LAUNCH</key>
    <string>1</string>
  </dict>
</dict>
</plist>
PLIST

# Info.plist 경로 치환 (공백 포함 프로젝트 경로)
/usr/bin/sed -i '' "s|REPLACE_MPLCONFIGDIR|${ROOT}/.matplotlib|g" "$CONTENTS/Info.plist" 2>/dev/null || \
  sed -i "s|REPLACE_MPLCONFIGDIR|${ROOT}/.matplotlib|g" "$CONTENTS/Info.plist"

chmod +x "$ROOT/scripts/jarvis_launcher.sh"

# 카메라/마이크 권한 요청 헬퍼 (앱 번들 내 — TCC가 JARVIS.app 으로 인식)
if command -v swiftc >/dev/null 2>&1; then
  if swiftc -o "$MACOS/jarvis-camcheck" "$ROOT/scripts/jarvis_camera_prompt.swift" -framework AVFoundation 2>/dev/null; then
    chmod +x "$MACOS/jarvis-camcheck"
    echo "✓ 카메라 권한 헬퍼 빌드"
  fi
  if swiftc -o "$MACOS/jarvis-miccheck" "$ROOT/scripts/jarvis_mic_prompt.swift" -framework AVFoundation 2>/dev/null; then
    chmod +x "$MACOS/jarvis-miccheck"
    echo "✓ 마이크 권한 헬퍼 빌드"
  fi
  if swiftc -o "$MACOS/jarvis-loccheck" "$ROOT/scripts/jarvis_location_prompt.swift" -framework CoreLocation 2>/dev/null; then
    chmod +x "$MACOS/jarvis-loccheck"
    echo "✓ 위치 권한 헬퍼 빌드"
  fi
  # 자연스러운(Siri급) TTS 헬퍼 — 첫 실행 시 컴파일 지연을 없애기 위해 미리 빌드
  mkdir -p "$ROOT/bin"
  if swiftc -O -o "$ROOT/bin/jarvis_tts" "$ROOT/scripts/jarvis_tts.swift" 2>/dev/null; then
    chmod +x "$ROOT/bin/jarvis_tts"
    echo "✓ 자연스러운 음성(AVSpeech) 헬퍼 빌드"
  fi
fi

if [[ -f "$ROOT/assets/AppIcon.icns" ]]; then
  cp "$ROOT/assets/AppIcon.icns" "$RESOURCES/AppIcon.icns"
fi

# Gatekeeper 격리 완화 + ad-hoc 서명
if command -v codesign >/dev/null 2>&1; then
  codesign --force --deep --sign - "$APP_DIR" 2>/dev/null || true
fi
xattr -cr "$APP_DIR" 2>/dev/null || true

echo "✓ 빌드 완료: $APP_DIR"

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

case "${1:---all}" in
  --desktop)   install_copy "$HOME/Desktop/${APP_NAME}.app" "바탕화면" ;;
  --applications) install_copy "$HOME/Applications/${APP_NAME}.app" "응용 프로그램" ;;
  --all)
    install_copy "$HOME/Desktop/${APP_NAME}.app" "바탕화면"
    install_copy "$HOME/Applications/${APP_NAME}.app" "응용 프로그램"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  JARVIS.app 설치 완료"
    echo "  바탕화면 또는 Launchpad에서 더블클릭"
    echo "  → jarvis_v2.py (IRON MAN HUD) 실행"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    ;;
  --project-only)
    echo "  (프로젝트 폴더에만 생성 — --all 로 바탕화면 설치)"
    ;;
  *)
    echo "옵션: --all | --desktop | --applications | --project-only"
    exit 1
    ;;
esac
