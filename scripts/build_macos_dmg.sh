#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
XCODE_PROJECT="$PROJECT_ROOT/native/ForgeInboxLite.xcodeproj"
SCHEME="ForgeInboxLite"
CONFIGURATION="Release"
DERIVED_DATA="$PROJECT_ROOT/build/native-macos"
APP_NAME="ForgeCommunicator"
APP_PATH="$DERIVED_DATA/Build/Products/$CONFIGURATION/${APP_NAME}.app"
STAGING_DIR="$PROJECT_ROOT/build/macos-installer"
DMG_NAME="Forge-macOS.dmg"
DMG_PATH="$PROJECT_ROOT/dist/$DMG_NAME"

# Ensure icon assets are synced into the native asset catalog before build.
ICON_SRC_DIR="$PROJECT_ROOT/app/static/icons"
ICON_DST_DIR="$PROJECT_ROOT/native/Sources/ForgeInboxLite/Assets.xcassets/AppIcon.appiconset"
LOGO_DST_DIR="$PROJECT_ROOT/native/Sources/ForgeInboxLite/Assets.xcassets/Logo.imageset"

mkdir -p "$ICON_DST_DIR" "$LOGO_DST_DIR"

copy_icon_if_present() {
  local source_name="$1"
  local target_name="$2"
  if [[ -f "$ICON_SRC_DIR/$source_name" ]]; then
    cp "$ICON_SRC_DIR/$source_name" "$ICON_DST_DIR/$target_name"
  fi
}

copy_icon_if_present "icon-16x16.png" "icon-16.png"
copy_icon_if_present "icon-32x32.png" "icon-32.png"
copy_icon_if_present "icon-64x64.png" "icon-64.png"
copy_icon_if_present "icon-128x128.png" "icon-128.png"
copy_icon_if_present "icon-256x256.png" "icon-256.png"
copy_icon_if_present "icon-512x512.png" "icon-512.png"
copy_icon_if_present "icon-1024x1024.png" "icon-1024.png"

if [[ -f "$ICON_SRC_DIR/icon-512x512.png" ]]; then
  cp "$ICON_SRC_DIR/icon-512x512.png" "$LOGO_DST_DIR/logo-512.png"
fi

mkdir -p "$PROJECT_ROOT/dist"
rm -rf "$DERIVED_DATA" "$STAGING_DIR"

xcodebuild \
  -project "$XCODE_PROJECT" \
  -scheme "$SCHEME" \
  -configuration "$CONFIGURATION" \
  -destination 'generic/platform=macOS' \
  -derivedDataPath "$DERIVED_DATA" \
  CODE_SIGNING_ALLOWED=NO \
  build

if [[ ! -d "$APP_PATH" ]]; then
  echo "Expected app bundle not found at: $APP_PATH" >&2
  exit 1
fi

# Strip local symbol/debug metadata from the app binary in local release builds
# to reduce accidental embedding of developer-specific path strings.
APP_BIN="$APP_PATH/Contents/MacOS/$APP_NAME"
if [[ -f "$APP_BIN" ]]; then
  strip -x "$APP_BIN" || true
fi

mkdir -p "$STAGING_DIR"
cp -R "$APP_PATH" "$STAGING_DIR/"
ln -s /Applications "$STAGING_DIR/Applications"
rm -f "$DMG_PATH"

hdiutil create \
  -volname "ForgeCommunicator" \
  -srcfolder "$STAGING_DIR" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

echo "Created installer: $DMG_PATH"
