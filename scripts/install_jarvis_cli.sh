#!/usr/bin/env bash
# jarvis.run 을 PATH 어디서든 칠 수 있게 ~/bin 에 연결
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${JARVIS_CLI_BIN:-$HOME/bin}"
LINK="$BIN/jarvis.run"
SRC="$ROOT/jarvis.run"

chmod +x "$SRC" "$ROOT/scripts/jarvis_launcher.sh" 2>/dev/null || true
mkdir -p "$BIN"
ln -sfn "$SRC" "$LINK"

echo "$ROOT" > "$ROOT/.jarvis_path"

case ":${PATH}:" in
  *":$BIN:"*) ;;
  *)
    for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
      [[ -f "$rc" ]] || continue
      if grep -q 'JARVIS CLI' "$rc" 2>/dev/null; then
        continue
      fi
      cat >> "$rc" <<EOF

# JARVIS CLI — jarvis.run
export PATH="$BIN:\$PATH"
EOF
      echo "✓ PATH 추가: $rc"
    done
    ;;
esac

echo "✓ $LINK → $SRC"
echo "  새 터미널에서: jarvis.run"
