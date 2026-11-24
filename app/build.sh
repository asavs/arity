#!/bin/sh
# Build and open the Mac app: Rust core + CLI worker -> Swift bindings -> SwiftUI app -> Arity.app.
#   app/build.sh             debug build, then (re)open the app
#   app/build.sh --no-open   build only
#   PROFILE=release app/build.sh
set -eu
cd "$(dirname "$0")/.."
PROFILE=${PROFILE:-debug}
FLAG=""; [ "$PROFILE" = release ] && FLAG=--release
OUT=app/build
TARGET_DIR="${CARGO_TARGET_DIR:-target}"
LIB="$TARGET_DIR/$PROFILE"

# 1. Build both the core library (linked into Arity) and the detached CLI worker (arity).
cargo build -q --offline -p arity-core -p arity-cli $FLAG

# 2. Generate Swift UniFFI bindings and package the static library as an XCFramework.
rm -rf "$OUT/gen" "$OUT/headers" "$OUT/arity_coreFFI.xcframework"
cargo run -q --offline -p arity-core --features bindgen --bin uniffi-bindgen -- \
  generate --library "$LIB/libarity_core.dylib" --language swift --out-dir "$OUT/gen"
mkdir -p "$OUT/headers"
cp "$OUT/gen/arity_coreFFI.h" "$OUT/headers/"
cp "$OUT/gen/arity_coreFFI.modulemap" "$OUT/headers/module.modulemap"
xcodebuild -create-xcframework -library "$LIB/libarity_core.a" -headers "$OUT/headers" \
  -output "$OUT/arity_coreFFI.xcframework" >/dev/null
mkdir -p app/Sources/ArityCore
cp "$OUT/gen/arity_core.swift" app/Sources/ArityCore/arity_core.swift

# 3. Build the SwiftUI app and wrap it in Arity.app alongside the detached `arity` worker.
swift build -q --package-path app -c "$PROFILE"
APP="$OUT/Arity.app"
BIN_DIR=$(swift build --package-path app -c "$PROFILE" --show-bin-path)
ICON_NAME="Arity Adaptive"
# Absolute paths: actool hands them to a shared daemon, which may have been
# started in another checkout and would read relative ones from there.
ICON_OUT=$(mktemp -d "$PWD/$OUT/icon.XXXXXX")
trap 'rm -rf "$ICON_OUT"' EXIT
MACOS_MINIMUM=$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' app/Info.plist)
xcrun actool --compile "$ICON_OUT" --platform macosx \
  --minimum-deployment-target "$MACOS_MINIMUM" --app-icon "$ICON_NAME" \
  --output-format human-readable-text --output-partial-info-plist "$ICON_OUT/Info.plist" \
  "$PWD/app/Design/$ICON_NAME.icon" >/dev/null
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN_DIR/Arity" "$APP/Contents/MacOS/Arity"
# Named `arity-worker` so case-insensitive APFS never collides with `Contents/MacOS/Arity`.
cp "$LIB/arity" "$APP/Contents/MacOS/arity-worker"
chmod +x "$APP/Contents/MacOS/Arity" "$APP/Contents/MacOS/arity-worker"
cp "$ICON_OUT/Assets.car" "$ICON_OUT/$ICON_NAME.icns" "$APP/Contents/Resources/"
cp app/Info.plist "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Merge $ICON_OUT/Info.plist" "$APP/Contents/Info.plist"
codesign --force --sign - "$APP/Contents/MacOS/arity-worker" 2>/dev/null
codesign --force --sign - "$APP" 2>/dev/null

if [ "${1:-}" != "--no-open" ]; then
  pkill -f "$PWD/$APP/Contents/MacOS/Arity" 2>/dev/null && sleep 0.5 || true
  open "$APP"
fi
echo "built $APP"
