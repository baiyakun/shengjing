#!/usr/bin/env bash
# 一键打包发布版：构建运行环境 → 签名 → 打包 App 和 dmg → 公证。
#
# 需要的环境变量：
#   DEVELOPER_ID            Developer ID Application 证书名
#   APPLE_SIGNING_IDENTITY  同上（Tauri 用它给 App 签名）
#   APPLE_ID / APPLE_PASSWORD / APPLE_TEAM_ID   公证用（APPLE_PASSWORD 是 App 专用密码）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

"$ROOT/packaging/build_runtime.sh"
"$ROOT/packaging/sign_runtime.sh" "$ROOT/packaging/build/runtime"

cd "$ROOT/app"
pnpm install --frozen-lockfile
# Tauri 在设置了 APPLE_ID 等变量时会自动公证并装订（staple）
pnpm tauri build --config src-tauri/tauri.release.conf.json --target aarch64-apple-darwin
ls -lh src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/
