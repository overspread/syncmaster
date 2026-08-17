# SyncMaster 项目长期记忆

## 项目概述
SyncMaster 是 Hermes 配置同步工具，支持本地 ↔ 远程服务器（通过跳板机）的双向 rsync 同步。
原版用 Swift/Cocoa 写的 macOS 原生 App，现在转向 Tauri + Python Web 版（跨平台）。

## 技术架构
- **前端**：Jinja2 模板 + 原生 CSS/JS（无框架），暗色主题
- **后端**：Python http.server + SQLite（~/.syncmaster/db.sqlite）
- **桌面壳**：Tauri 2.x，Rust 启动 python3 server.py 子进程 → WebView 连 http://127.0.0.1:9800
- **同步引擎**：rsync over SSH（跳板机两跳），SSE 推送实时日志

## 关键路径
- 项目根：`/Users/overspread/projects/syncmaster/`
- Web 后端：`syncmaster/server.py`
- 模板：`syncmaster/templates/*.html`（8 个：base/index/settings/categories/history/backup/audit/monitor）
- 静态：`syncmaster/static/`（style.css, app.js）
- Tauri：`src-tauri/`（main.rs, Cargo.toml, tauri.conf.json, capabilities/）
- 图标：`src-tauri/icons/`（Python 生成的 PNG/ICNS/ICO 占位）
- 构建脚本：`tauri-dev.sh`（开发）、`tauri-build.sh`（打包）
- 数据库：`~/.syncmaster/db.sqlite`（与 Swift 版 schema 兼容）
- 备份目录：`~/.syncmaster/backups/`

## Swift 原版文件（参考用）
- `MainViewController.swift` — 主窗口、7 个面板切换
- `CategoryPanel.swift` — 分类管理面板
- `Database.swift` — SQLite 封装
- `Models.swift` — 数据模型
- `SyncEngine.swift` — 同步引擎
- `ManifestEngine.swift` — Manifest 哈希对比

## 服务器配置
- 跳板机：ubuntu@54.160.252.171（密钥 ~/Documents/2api.pem）
- 目标服务器：opc@155.248.172.187（密钥 /home/ubuntu/oci_opc_key.pem）
- 远程目录：/home/opc/.hermes
- 本地目录：~/.hermes
