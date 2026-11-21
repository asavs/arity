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
mkdir -p $APP/Contents/MacOS
cp "$(swift build --package-path app -c $PROFILE --show-bin-path)/Arity" $APP/Contents/MacOS/Arity
cp app/Info.plist $APP/Contents/Info.plist
codesign --force --sign - $APP 2>/dev/null

if [ "${1:-}" != "--no-open" ]; then
  pkill -f "$PWD/$APP/Contents/MacOS/Arity" 2>/dev/null && sleep 0.5 || true
  open $APP
fi
echo "built $APP"
