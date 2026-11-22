#!/bin/sh
# Build and open the Mac app: Rust core -> Swift bindings -> SwiftUI app -> Arity.app.
#   app/build.sh             debug build, then (re)open the app
#   app/build.sh --no-open   build only
#   PROFILE=release app/build.sh
set -eu
cd "$(dirname "$0")/.."
PROFILE=${PROFILE:-debug}
FLAG=""; [ "$PROFILE" = release ] && FLAG=--release
OUT=app/build
LIB=target/$PROFILE

# 1. The core, as a static library the app links in.
cargo build -q -p arity-core $FLAG

# 2. Swift bindings, read from the compiled library's interface.
rm -rf $OUT/gen $OUT/headers $OUT/arity_coreFFI.xcframework
cargo run -q -p arity-core --features bindgen --bin uniffi-bindgen -- \
  generate --library $LIB/libarity_core.dylib --language swift --out-dir $OUT/gen
mkdir -p $OUT/headers
cp $OUT/gen/arity_coreFFI.h $OUT/headers/
cp $OUT/gen/arity_coreFFI.modulemap $OUT/headers/module.modulemap
xcodebuild -create-xcframework -library $LIB/libarity_core.a -headers $OUT/headers \
  -output $OUT/arity_coreFFI.xcframework >/dev/null
cp $OUT/gen/arity_core.swift app/Sources/ArityCore/arity_core.swift

# 3. The SwiftUI app, wrapped in a minimal bundle so macOS treats it as a real app.
swift build -q --package-path app -c $PROFILE
APP=$OUT/Arity.app
BIN_DIR=$(swift build --package-path app -c $PROFILE --show-bin-path)
ICON_NAME="Arity Adaptive"
# Absolute paths: actool hands them to a shared daemon, which may have been
# started in another checkout and would read relative ones from there.
ICON_OUT=$(mktemp -d "$PWD/$OUT/icon.XXXXXX")
trap 'rm -rf "$ICON_OUT"' EXIT
MACOS_MINIMUM=$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' app/Info.plist)
xcrun actool --compile "$ICON_OUT" --platform macosx \
  --minimum-deployment-target "$MACOS_MINIMUM" --app-icon "$ICON_NAME" \
  --output-format human-readable-text --output-partial-info-plist "$ICON_OUT/Info.plist" \
  "$PWD/app/Design/$ICON_NAME.icon"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN_DIR/Arity" "$APP/Contents/MacOS/Arity"
cp "$ICON_OUT/Assets.car" "$ICON_OUT/$ICON_NAME.icns" "$APP/Contents/Resources/"
cp app/Info.plist "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Merge $ICON_OUT/Info.plist" "$APP/Contents/Info.plist"
codesign --force --sign - $APP 2>/dev/null

if [ "${1:-}" != "--no-open" ]; then
  pkill -f "$PWD/$APP/Contents/MacOS/Arity" 2>/dev/null && sleep 0.5 || true
  open $APP
fi
echo "built $APP"
