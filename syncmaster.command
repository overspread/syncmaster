#!/bin/bash
# SyncMaster - Hermes 同步管理 Web 后台
# 双击运行，或从终端启动

cd "$(dirname "$0")"

# 确保 PYTHONPATH 包含 user site-packages（系统 Python 的 bug）
export PYTHONPATH="$HOME/Library/Python/3.9/lib/python/site-packages:$PYTHONPATH"

# 检查依赖
if ! python3 -c "import jinja2" 2>/dev/null; then
    echo "正在安装依赖 (jinja2)..."
    pip3 install -q jinja2 2>/dev/null || pip install -q jinja2 2>/dev/null
fi

# 启动服务器
echo "启动 SyncMaster..."
echo "打开浏览器访问 http://127.0.0.1:9800"
echo "按 Ctrl+C 停止"
echo ""

python3 syncmaster/server.py

# 如果服务器退出，保持窗口打开
echo ""
echo "服务器已停止"
read -p "按 Enter 关闭..."