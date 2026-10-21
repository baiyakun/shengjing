#!/usr/bin/env bash
# 启动开发版 App（Tauri 开发模式，界面热更新）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 每次都同步一遍开发环境：已经装好的会直接跳过，引擎新增依赖时也能自动装上
"$ROOT/scripts/dev_setup.sh"
cd "$ROOT/app"
pnpm install
pnpm tauri dev
