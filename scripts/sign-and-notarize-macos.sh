#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SOURCE_APP="${1:-$ROOT/tauri/src-tauri/target/release/bundle/macos/VoiceMeeting.app}"
OUTPUT_DIR="$ROOT/tauri/src-tauri/target/release/bundle/dmg"
DMG_PATH="$OUTPUT_DIR/VoiceMeeting_0.0.2_aarch64.dmg"
WORK_ROOT="${TMPDIR:-/tmp}/VoiceMeeting-signing"
WORK_APP="$WORK_ROOT/VoiceMeeting.app"
APP_ZIP_PATH="$WORK_ROOT/VoiceMeeting.app.zip"
DMG_STAGING="$WORK_ROOT/dmg-root"
ENTITLEMENTS="$ROOT/tauri/src-tauri/entitlements.plist"
RESOURCE_SOURCE="$ROOT/tauri/src-tauri/resources/voice-meeting-server"

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

  for attempt in 1 2 3; do
    if output=$(codesign "${args[@]}" "$path" 2>&1); then
      return 0
    fi
    if [ "$attempt" -lt 3 ] && grep -qi 'timestamp' <<<"$output"; then
      echo "$output" >&2
      echo "Retrying codesign timestamp for $path..." >&2
      sleep 5
      continue
    fi
    echo "$output" >&2
    exit 1
  done
}

sign_container() {
  local path="$1"
  local output

  for attempt in 1 2 3; do
    if output=$(codesign --force --timestamp --sign "$IDENTITY" "$path" 2>&1); then
      return 0
    fi
    if [ "$attempt" -lt 3 ] && grep -qi 'timestamp' <<<"$output"; then
      echo "$output" >&2
      echo "Retrying codesign timestamp for $path..." >&2
      sleep 5
      continue
    fi
    echo "$output" >&2
    exit 1
  done
}

submit_notarization() {
  local path="$1"

  xcrun notarytool submit "$path" \
    --apple-id "$APPLE_ID_VALUE" \
    --password "$APPLE_PASSWORD_VALUE" \
    --team-id "$APPLE_TEAM_ID_VALUE" \
    --wait
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

repair_framework_symlinks() {
  local root="$1"

  while IFS= read -r -d '' framework_path; do
    local framework_name
    local versions_dir
    local version_dir
    local version_name
    local executable_name

    framework_name="$(basename "$framework_path" .framework)"
    versions_dir="$framework_path/Versions"
    [ -d "$versions_dir" ] || continue

    version_dir="$(find "$versions_dir" -mindepth 1 -maxdepth 1 -type d ! -name Current | sort | tail -n 1)"
    [ -n "$version_dir" ] || continue

    version_name="$(basename "$version_dir")"
    executable_name="$framework_name"
    [ -f "$version_dir/$executable_name" ] || continue

    rm -rf "$framework_path/_CodeSignature"
    find "$framework_path" -name '_CodeSignature' -type d -prune -exec rm -rf {} +

    rm -rf "$versions_dir/Current"
    ln -s "$version_name" "$versions_dir/Current"

    rm -rf "$framework_path/$executable_name"
    ln -s "Versions/Current/$executable_name" "$framework_path/$executable_name"

    if [ -d "$version_dir/Resources" ]; then
      rm -rf "$framework_path/Resources"
      ln -s "Versions/Current/Resources" "$framework_path/Resources"
    fi
  done < <(find "$root" -type d -name '*.framework' -print0)
}

repair_resource_symlinks() {
  local app_root="$1"
  local destination_root="$app_root/Contents/Resources/resources/voice-meeting-server"

  [ -d "$RESOURCE_SOURCE" ] || return 0
  [ -d "$destination_root" ] || return 0

  while IFS= read -r -d '' source_link; do
    local relative_path
    local link_target
    local destination_link

    relative_path="${source_link#"$RESOURCE_SOURCE"/}"
    link_target="$(readlink "$source_link")"
    destination_link="$destination_root/$relative_path"

    rm -rf "$destination_link"
    mkdir -p "$(dirname "$destination_link")"
    ln -s "$link_target" "$destination_link"
  done < <(find "$RESOURCE_SOURCE" -type l -print0)
}

echo "Preparing clean signing copy..."
rm -rf "$WORK_ROOT"
mkdir -p "$WORK_ROOT"
ditto --norsrc --noextattr "$SOURCE_APP" "$WORK_APP"

echo "Cleaning extended attributes..."
clear_attrs "$WORK_APP"
find "$WORK_APP" -name '._*' -delete

echo "Repairing resource symlinks..."
repair_resource_symlinks "$WORK_APP"
repair_framework_symlinks "$WORK_APP"
clear_attrs "$WORK_APP"

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

if [ "${VOICE_MEETING_SKIP_NOTARIZE:-0}" != "1" ]; then
  echo "Submitting app bundle for notarization..."
  rm -f "$APP_ZIP_PATH"
  ditto -c -k --keepParent "$WORK_APP" "$APP_ZIP_PATH"
  submit_notarization "$APP_ZIP_PATH"

  echo "Stapling app notarization ticket..."
  xcrun stapler staple "$WORK_APP"
  xcrun stapler validate "$WORK_APP"
fi

echo "Updating signed app bundle output..."
rm -rf "$SOURCE_APP"
ditto "$WORK_APP" "$SOURCE_APP"

mkdir -p "$OUTPUT_DIR"
rm -f "$DMG_PATH"
rm -rf "$DMG_STAGING"
mkdir -p "$DMG_STAGING"
ditto "$WORK_APP" "$DMG_STAGING/VoiceMeeting.app"
ln -s /Applications "$DMG_STAGING/Applications"
find "$DMG_STAGING" -name '._*' -delete

echo "Creating DMG..."
hdiutil create \
  -volname "VoiceMeeting-0_0_2" \
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
submit_notarization "$DMG_PATH"

echo "Stapling notarization ticket..."
xcrun stapler staple "$DMG_PATH"
xcrun stapler validate "$DMG_PATH"
xcrun stapler validate "$SOURCE_APP"

VERIFY_APP="$WORK_ROOT/verify/VoiceMeeting.app"
rm -rf "$WORK_ROOT/verify"
mkdir -p "$WORK_ROOT/verify"
ditto --norsrc --noextattr "$WORK_APP" "$VERIFY_APP"
clear_attrs "$VERIFY_APP"
codesign --verify --deep --strict --verbose=2 "$VERIFY_APP"
spctl -a -t open --context context:primary-signature -vv "$DMG_PATH"

echo "Done: $DMG_PATH"
