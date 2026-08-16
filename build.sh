#!/bin/bash
# SyncMaster V6 构建脚本（多文件）
set -e
cd "$(dirname "$0")"
rm -rf build
mkdir -p build/SyncMaster.app/Contents/MacOS build/SyncMaster.app/Contents/Resources
cp SyncMaster.app/Contents/Info.plist build/SyncMaster.app/Contents/Info.plist 2>/dev/null || true
cp SyncMaster.app/Contents/Resources/icon.icns build/SyncMaster.app/Contents/Resources/ 2>/dev/null || true

swiftc -O \
    -framework Cocoa \
    -framework CryptoKit \
    -o build/SyncMaster \
    Models.swift \
    Database.swift \
    UIHelpers.swift \
    ManifestEngine.swift \
    SyncEngine.swift \
    FileBrowserPanel.swift \
    CategoryPanel.swift \
    ConflictResolverPanel.swift \
    EnvSyncEngine.swift \
    MainViewController.swift \
    main.swift

cp build/SyncMaster build/SyncMaster.app/Contents/MacOS/
echo "构建完成: build/SyncMaster.app"
