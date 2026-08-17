#!/usr/bin/env bash
# Build a release binary for the current platform.
# macOS:  src-tauri/target/release/bundle/macos/SyncMaster.app
#         src-tauri/target/release/bundle/dmg/SyncMaster_*.dmg
# Linux:  src-tauri/target/release/bundle/appimage/*.AppImage
# Win:    src-tauri/target/release/bundle/msi/*.msi

set -uo pipefail
cd "$(dirname "$0")"

command -v tauri >/dev/null 2>&1 || cargo install tauri-cli --version "^2"

cd src-tauri

# `cargo tauri build` 在 macOS 会尝试用 create-dmg (依赖 Finder/AppleScript)
# 生成 .dmg，在无 GUI / 沙箱环境下必然失败。这里不让它中断后续兜底流程。
cargo tauri build "$@" || echo "⚠️  tauri build 在 DMG 阶段报错（create-dmg 依赖），继续用手动 hdiutil 兜底。"

APP="target/release/bundle/macos/SyncMaster.app"
DMG_DIR="target/release/bundle/dmg"

if [ -d "$APP" ]; then
    mkdir -p "$DMG_DIR"
    # 复用 Tauri 已生成的 dmg 文件名（含版本/架构），不存在则用默认名
    EXISTING="$(ls "$DMG_DIR"/SyncMaster_*.dmg 2>/dev/null | head -1)"
    DMG_NAME="${EXISTING:-$DMG_DIR/SyncMaster_1.0.0_aarch64.dmg}"

    STAGING="$(mktemp -d)"
    cp -R "$APP" "$STAGING/"
    ln -s /Applications "$STAGING/Applications"
    hdiutil create -volname "SyncMaster" -srcfolder "$STAGING" -ov -format UDZO "$DMG_NAME"
    rm -rf "$STAGING"
    echo "✅ DMG 已生成: $DMG_NAME"
fi

echo "✅ Done. Artifacts in src-tauri/target/release/bundle/"
