#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SOURCE_APP="${1:-$ROOT/tauri/src-tauri/target/release/bundle/macos/VoiceMeeting.app}"
OUTPUT_DIR="$ROOT/tauri/src-tauri/target/release/bundle/dmg"
DMG_PATH="$OUTPUT_DIR/VoiceMeeting_0.0.1_aarch64.dmg"
WORK_ROOT="${TMPDIR:-/tmp}/VoiceMeeting-signing"
WORK_APP="$WORK_ROOT/VoiceMeeting.app"
DMG_STAGING="$WORK_ROOT/dmg-root"
ENTITLEMENTS="$ROOT/tauri/src-tauri/entitlements.plist"

if [ ! -d "$SOURCE_APP" ]; then
  echo "App bundle not found: $SOURCE_APP" >&2
  exit 1
fi

if [ ! -f apple-sign.config ]; then
  echo "apple-sign.config not found." >&2
  exit 1
fi

set -a
source apple-sign.config
set +a

IDENTITY="${APPLE_SIGNING_IDENTITY:?APPLE_SIGNING_IDENTITY is required}"
APPLE_ID_VALUE="${APPLE_ID:?APPLE_ID is required}"
APPLE_PASSWORD_VALUE="${APPLE_APP_SPECIFIC_PASSWORD:?APPLE_APP_SPECIFIC_PASSWORD is required}"
APPLE_TEAM_ID_VALUE="${APPLE_TEAM_ID:?APPLE_TEAM_ID is required}"

sign_code() {
  local path="$1"
  local use_entitlements="${2:-}"
  local output
  local args=(
    --force
    --timestamp
    --options
    runtime
    --sign
    "$IDENTITY"
  )

  if [ "$use_entitlements" = "with-entitlements" ] && [ -f "$ENTITLEMENTS" ]; then
    args+=(--entitlements "$ENTITLEMENTS")
  fi

  if ! output=$(codesign "${args[@]}" "$path" 2>&1); then
    echo "$output" >&2
    exit 1
  fi
}

sign_container() {
  local path="$1"
  local output

  if ! output=$(codesign --force --timestamp --sign "$IDENTITY" "$path" 2>&1); then
    echo "$output" >&2
    exit 1
  fi
}

clear_attrs() {
  local path="$1"

  xattr -cr "$path" 2>/dev/null || true
  xattr -d com.apple.FinderInfo "$path" 2>/dev/null || true
  xattr -d com.apple.ResourceFork "$path" 2>/dev/null || true
  xattr -d com.apple.provenance "$path" 2>/dev/null || true
  xattr -d "com.apple.fileprovider.fpfs#P" "$path" 2>/dev/null || true
  xattr -dr com.apple.FinderInfo "$path" 2>/dev/null || true
  xattr -dr com.apple.ResourceFork "$path" 2>/dev/null || true
  xattr -dr com.apple.provenance "$path" 2>/dev/null || true
  xattr -dr "com.apple.fileprovider.fpfs#P" "$path" 2>/dev/null || true
}

echo "Preparing clean signing copy..."
rm -rf "$WORK_ROOT"
mkdir -p "$WORK_ROOT"
ditto --norsrc --noextattr "$SOURCE_APP" "$WORK_APP"

echo "Cleaning extended attributes..."
clear_attrs "$WORK_APP"
find "$WORK_APP" -name '._*' -delete

echo "Signing nested Mach-O files..."
while IFS= read -r -d '' file_path; do
  file_info="$(file "$file_path")"
  if grep -q 'Mach-O' <<<"$file_info"; then
    if grep -q 'executable' <<<"$file_info"; then
      sign_code "$file_path" "with-entitlements"
    else
      sign_code "$file_path"
    fi
  fi
done < <(find "$WORK_APP" -type f -print0 | sort -z)

echo "Signing embedded frameworks..."
while IFS= read -r -d '' framework_path; do
  clear_attrs "$framework_path"
  sign_code "$framework_path"
done < <(find "$WORK_APP" -type d -name '*.framework' -print0 | sort -z)

echo "Signing app bundle..."
clear_attrs "$WORK_APP"
sign_code "$WORK_APP" "with-entitlements"
codesign --verify --deep --strict --verbose=2 "$WORK_APP"

echo "Updating signed app bundle output..."
rm -rf "$SOURCE_APP"
ditto --norsrc --noextattr "$WORK_APP" "$SOURCE_APP"
clear_attrs "$SOURCE_APP"

mkdir -p "$OUTPUT_DIR"
rm -f "$DMG_PATH"
rm -rf "$DMG_STAGING"
mkdir -p "$DMG_STAGING"
ditto --norsrc --noextattr "$WORK_APP" "$DMG_STAGING/VoiceMeeting.app"
ln -s /Applications "$DMG_STAGING/Applications"
clear_attrs "$DMG_STAGING"
find "$DMG_STAGING" -name '._*' -delete

echo "Creating DMG..."
hdiutil create \
  -volname "VoiceMeeting-0_0_1" \
  -srcfolder "$DMG_STAGING" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

echo "Signing DMG..."
clear_attrs "$DMG_PATH"
sign_container "$DMG_PATH"
codesign --verify --verbose=2 "$DMG_PATH"

if [ "${VOICE_MEETING_SKIP_NOTARIZE:-0}" = "1" ]; then
  echo "Skipping notarization: $DMG_PATH"
  exit 0
fi

echo "Submitting DMG for notarization..."
xcrun notarytool submit "$DMG_PATH" \
  --apple-id "$APPLE_ID_VALUE" \
  --password "$APPLE_PASSWORD_VALUE" \
  --team-id "$APPLE_TEAM_ID_VALUE" \
  --wait

echo "Stapling notarization ticket..."
xcrun stapler staple "$DMG_PATH"
xcrun stapler staple "$SOURCE_APP"
clear_attrs "$SOURCE_APP"

VERIFY_APP="$WORK_ROOT/verify/VoiceMeeting.app"
rm -rf "$WORK_ROOT/verify"
mkdir -p "$WORK_ROOT/verify"
ditto --norsrc --noextattr "$SOURCE_APP" "$VERIFY_APP"
clear_attrs "$VERIFY_APP"
codesign --verify --deep --strict --verbose=2 "$VERIFY_APP"
spctl -a -t open --context context:primary-signature -vv "$DMG_PATH"

echo "Done: $DMG_PATH"
