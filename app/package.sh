#!/bin/sh
# Build a release Mac app, release `arity` CLI, and a distributable macOS DMG.
#   app/package.sh             build release Arity.app + CLI + DMG in app/build/
#   app/package.sh --install   also install /Applications/Arity.app & ~/.local/bin/arity and launch Arity
set -eu
cd "$(dirname "$0")/.."

INSTALL=0
for arg in "$@"; do
  case "$arg" in
    --install) INSTALL=1 ;;
    *) echo "usage: app/package.sh [--install]" >&2; exit 2 ;;
  esac
done

VERSION=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' app/Info.plist)
ARCH=$(uname -m)
OUT=app/build
APP="$OUT/Arity.app"
DMG="$OUT/Arity-$VERSION-$ARCH.dmg"

echo "==> Building release Arity.app ($VERSION, $ARCH)..."
PROFILE=release app/build.sh --no-open

echo "==> Building release arity CLI..."
cargo build --release -q -p arity-cli
CLI_BIN="target/release/arity"

# Bundle the CLI inside Arity.app/Contents/Resources/arity too so anyone who
# drags Arity.app to /Applications has the matching binary on hand.
cp "$CLI_BIN" "$APP/Contents/Resources/arity"
chmod +x "$APP/Contents/Resources/arity"
codesign --force --sign - "$APP" 2>/dev/null

echo "==> Packaging $DMG..."
STAGE=$(mktemp -d "$PWD/$OUT/dmg-stage.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT

cp -R "$APP" "$STAGE/Arity.app"
ln -s /Applications "$STAGE/Applications"
mkdir -p "$STAGE/CLI"
cp "$CLI_BIN" "$STAGE/CLI/arity"
chmod +x "$STAGE/CLI/arity"

cat > "$STAGE/Install CLI.txt" <<'EOF'
Arity for macOS
===============

1. Drag Arity.app into Applications.
2. (Optional) Install the matching `arity` CLI on your PATH:

     mkdir -p ~/.local/bin
     cp /Applications/Arity.app/Contents/Resources/arity ~/.local/bin/arity

   Both the Mac app and the CLI share the same data folder:
     ~/Library/Application Support/Arity

Note: This build is ad-hoc signed. If macOS Gatekeeper asks on first launch,
right-click Arity.app -> Open (or run `xattr -dr com.apple.quarantine /Applications/Arity.app`).
EOF

rm -f "$DMG"
hdiutil create \
  -volname "Arity $VERSION" \
  -srcfolder "$STAGE" \
  -ov \
  -format UDZO \
  "$DMG" >/dev/null

echo "packaged $DMG ($(du -h "$DMG" | awk '{print $1}'))"

if [ "$INSTALL" = 1 ]; then
  echo "==> Installing /Applications/Arity.app and ~/.local/bin/arity..."
  pkill -f "Arity.app/Contents/MacOS/Arity" 2>/dev/null && sleep 0.5 || true
  rm -rf /Applications/Arity.app
  cp -R "$APP" /Applications/Arity.app
  mkdir -p "$HOME/.local/bin"
  cp "$CLI_BIN" "$HOME/.local/bin/arity"
  chmod +x "$HOME/.local/bin/arity"
  open /Applications/Arity.app
  echo "installed /Applications/Arity.app and $HOME/.local/bin/arity"
fi
