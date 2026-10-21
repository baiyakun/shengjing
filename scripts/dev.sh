#!/usr/bin/env bash
# 启动开发版 App（Tauri 开发模式，界面热更新）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ ! -x "$ROOT/.dev/base-venv/bin/python" ]]; then
  "$ROOT/scripts/dev_setup.sh"
fi
cd "$ROOT/app"
pnpm install
pnpm tauri dev
