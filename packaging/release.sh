#!/usr/bin/env bash
# 一键打包发布版：构建运行环境 → 签名 → 打包 App 和 dmg → 公证。
#
#   packaging/release.sh                Apple 芯片版本（可以在本机运行模型）
#   ARCH=x86_64 packaging/release.sh    Intel 版本（只能使用在线 API）
#
# 需要的环境变量：
#   DEVELOPER_ID            Developer ID Application 证书名
#   APPLE_SIGNING_IDENTITY  同上（Tauri 用它给 App 签名）
#   APPLE_ID / APPLE_PASSWORD / APPLE_TEAM_ID   公证用（APPLE_PASSWORD 是 App 专用密码）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ARCH="${ARCH:-arm64}"
case "$ARCH" in
  arm64) TARGET=aarch64-apple-darwin ;;
  x86_64) TARGET=x86_64-apple-darwin ;;
  *) echo "ARCH 只能是 arm64 或 x86_64"; exit 1 ;;
esac

ARCH="$ARCH" "$ROOT/packaging/build_runtime.sh"
"$ROOT/packaging/sign_runtime.sh" "$ROOT/packaging/build/runtime"

rustup target add "$TARGET"
cd "$ROOT/app"
pnpm install --frozen-lockfile
# Tauri 在设置了 APPLE_ID 等变量时会自动公证并装订（staple）
pnpm tauri build --config src-tauri/tauri.release.conf.json --target "$TARGET"
ls -lh "src-tauri/target/$TARGET/release/bundle/dmg/"
