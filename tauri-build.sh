#!/usr/bin/env bash
# Build a release binary for the current platform.
# macOS:  src-tauri/target/release/bundle/macos/SyncMaster.app
# Linux:  src-tauri/target/release/bundle/appimage/*.AppImage
# Win:    src-tauri/target/release/bundle/msi/*.msi

set -euo pipefail
cd "$(dirname "$0")"

command -v tauri >/dev/null 2>&1 || cargo install tauri-cli --version "^2"

cd src-tauri
cargo tauri build "$@"
echo "✅ Done. Artifacts in src-tauri/target/release/bundle/"
