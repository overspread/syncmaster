#!/bin/bash
# SyncMaster 一键构建 + 部署 + 启动
set -e
cd ~/projects/syncmaster

echo "🔧 正在关闭旧进程..."
pkill -x SyncMaster 2>/dev/null || true
sleep 1

echo "📦 正在构建..."
bash build.sh

echo "🚀 正在部署到桌面..."
rm -rf ~/Desktop/SyncMaster.app
cp -R build/SyncMaster.app ~/Desktop/SyncMaster.app

echo "✅ 正在启动..."
open ~/Desktop/SyncMaster.app

echo "完成！SyncMaster 已更新并启动。"
