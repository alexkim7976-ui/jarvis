#!/usr/bin/env bash
# macOS 더블클릭 런처 .app 생성
#   ./scripts/make_app_launcher.sh --name "Chrome" --target "Google Chrome"
#   ./scripts/make_app_launcher.sh --name "Safari" --target-path "/Applications/Safari.app"
#   ./scripts/make_app_launcher.sh --jarvis   # JARVIS.app → 바탕화면+~/Applications
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME=""
TARGET=""
TARGET_PATH=""
OUTPUT=""
JARVIS_MODE=0
ICON=""

usage() {
  cat <<'EOF'
사용법:
  make_app_launcher.sh --name "표시이름" --target "앱 이름" [--output 경로.app]
  make_app_launcher.sh --name "표시이름" --target-path "/Applications/Foo.app"
  make_app_launcher.sh --jarvis [--output 경로.app]

  --target      open -a "앱이름" (예: Google Chrome, Safari, JARVIS)
  --target-path .app 번들 경로 직접 지정
  --jarvis      이 프로젝트 JARVIS.app 빌드 후 바탕화면·응용프로그램 설치
  --icon        .icns 아이콘 (선택)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --target) TARGET="$2"; shift 2 ;;
    --target-path) TARGET_PATH="$2"; shift 2 ;;
    --output) OUTPUT="$2"; shift 2 ;;
    --icon) ICON="$2"; shift 2 ;;
    --jarvis) JARVIS_MODE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "알 수 없는 옵션: $1"; usage; exit 1 ;;
  esac
done

if [[ "$JARVIS_MODE" == "1" ]]; then
  exec "$ROOT/scripts/build_jarvis_app.sh" --all
fi

if [[ -z "$NAME" ]]; then
  echo "✗ --name 필요 (또는 --jarvis)" >&2
  exit 1
fi
if [[ -z "$TARGET" && -z "$TARGET_PATH" ]]; then
  echo "✗ --target 또는 --target-path 필요" >&2
  exit 1
fi

if [[ -z "$OUTPUT" ]]; then
  OUTPUT="$HOME/Desktop/${NAME}.app"
fi

APP_DIR="$OUTPUT"
CONTENTS="$APP_DIR/Contents"
MACOS="$CONTENTS/MacOS"
RES="$CONTENTS/Resources"
BUNDLE_ID="com.jarvis.launcher.$(echo "$NAME" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-')"

rm -rf "$APP_DIR"
mkdir -p "$MACOS" "$RES"

# 실행 대상 기록
if [[ -n "$TARGET_PATH" ]]; then
  printf '%s' "$TARGET_PATH" > "$RES/target_path"
else
  printf '%s' "$TARGET" > "$RES/target_name"
fi

cat > "$RES/launch.sh" <<'LAUNCH'
#!/usr/bin/env bash
set -euo pipefail
RES="$(cd "$(dirname "$0")" && pwd)"
NAME_FILE="$RES/target_name"
PATH_FILE="$RES/target_path"

if [[ -f "$PATH_FILE" ]]; then
  T="$(tr -d '\n\r' < "$PATH_FILE")"
  if [[ -d "$T" ]]; then
    open "$T"
    exit 0
  fi
  osascript -e "display alert \"런처\" message \"앱을 찾을 수 없습니다: $T\"" 2>/dev/null || true
  exit 1
fi

T="$(tr -d '\n\r' < "$NAME_FILE" 2>/dev/null || true)"
if [[ -z "$T" ]]; then
  exit 1
fi

# JARVIS — 프로젝트 번들 우선
if [[ "$T" == "JARVIS" || "$T" == "J.A.R.V.I.S" ]]; then
  EXTRA=""
  [[ -f "$RES/jarvis_app_path" ]] && EXTRA="$(tr -d '\n\r' < "$RES/jarvis_app_path")"
  for CAND in \
    "$EXTRA" \
    "$HOME/Desktop/JARVIS.app" \
    "$HOME/Applications/JARVIS.app"; do
    [[ -z "$CAND" ]] && continue
    if [[ -d "$CAND" ]]; then
      open "$CAND"
      exit 0
    fi
  done
fi

open -a "$T" 2>/dev/null || open -a "$T.app" 2>/dev/null || {
  osascript -e "display alert \"런처\" message \"앱을 실행하지 못했습니다: $T\"" 2>/dev/null || true
  exit 1
}
LAUNCH
chmod +x "$RES/launch.sh"

if [[ -d "$ROOT/JARVIS.app" ]]; then
  printf '%s' "$ROOT/JARVIS.app" > "$RES/jarvis_app_path"
fi

if command -v clang >/dev/null 2>&1; then
  clang -O2 -o "$MACOS/launcher" "$ROOT/scripts/app_launcher.c"
else
  cc -O2 -o "$MACOS/launcher" "$ROOT/scripts/app_launcher.c"
fi
chmod +x "$MACOS/launcher"

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
  <string>launcher</string>
  <key>CFBundleIdentifier</key>
  <string>${BUNDLE_ID}</string>
  <key>CFBundleName</key>
  <string>${NAME}</string>
  <key>CFBundleDisplayName</key>
  <string>${NAME}</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>LSUIElement</key>
  <false/>
</dict>
</plist>
PLIST

if [[ -n "$ICON" && -f "$ICON" ]]; then
  cp "$ICON" "$RES/AppIcon.icns"
  /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string AppIcon" "$CONTENTS/Info.plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Set :CFBundleIconFile AppIcon" "$CONTENTS/Info.plist"
elif [[ -f "$ROOT/assets/AppIcon.icns" ]]; then
  cp "$ROOT/assets/AppIcon.icns" "$RES/AppIcon.icns"
  /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string AppIcon" "$CONTENTS/Info.plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Set :CFBundleIconFile AppIcon" "$CONTENTS/Info.plist"
fi

xattr -cr "$APP_DIR" 2>/dev/null || true
codesign --force --deep --sign - "$APP_DIR" 2>/dev/null || true

echo "✓ 런처 생성: $APP_DIR"
if [[ -n "$TARGET_PATH" ]]; then
  echo "  → open \"$TARGET_PATH\""
else
  echo "  → open -a \"$TARGET\""
fi
