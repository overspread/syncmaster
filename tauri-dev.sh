#!/usr/bin/env bash
# ── SyncMaster Tauri · one-click dev launcher ──────────────
# Installs tauri-cli if missing, then runs `cargo tauri dev`.
#
# Usage:
#   bash ./tauri-dev.sh

set -euo pipefail
cd "$(dirname "$0")"

# ── 1. System prereqs ──────────────────────────────────────
command -v cargo >/dev/null 2>&1 || {
  echo "❌ Rust + Cargo not found. Install from https://rustup.rs/ then re-run."
  exit 1
}
command -v python3 >/dev/null 2>&1 || {
  echo "❌ python3 not found on PATH. Install Python 3 first."
  exit 1
}
python3 -c "import jinja2" 2>/dev/null || {
  echo "📦 Installing missing Python dep: jinja2 ..."
  python3 -m pip install --user jinja2
}

# ── 2. Install tauri-cli (only if missing) ─────────────────
if ! command -v tauri >/dev/null 2>&1; then
  echo "📥 tauri-cli not found. Installing via cargo (may take 3-10 min)..."
  cargo install tauri-cli --version "^2"
fi

# ── 3. Run dev ─────────────────────────────────────────────
echo "🚀 Launching SyncMaster (Tauri dev mode)..."
cd src-tauri
cargo tauri dev "$@"
