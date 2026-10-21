#!/usr/bin/env bash
# 给运行环境里的每个可执行文件和动态库签名（加固运行时 + 时间戳），公证必需。
# 用法：DEVELOPER_ID="Developer ID Application: 你的名字 (TEAMID)" sign_runtime.sh <目录>
set -euo pipefail
DIR="${1:?用法：sign_runtime.sh <目录>}"
IDENTITY="${DEVELOPER_ID:?请设置 DEVELOPER_ID}"
ENT="$(cd "$(dirname "$0")" && pwd)/entitlements/python.plist"

# 先签里层的动态库，再签可执行文件
find "$DIR" -type f \( -name "*.so" -o -name "*.dylib" \) -print0 |
  xargs -0 -n 20 codesign --force --timestamp --options runtime --sign "$IDENTITY"
find "$DIR" -type f -perm -u+x -print0 | while IFS= read -r -d '' f; do
  if file "$f" | grep -q "Mach-O"; then
    codesign --force --timestamp --options runtime --entitlements "$ENT" --sign "$IDENTITY" "$f"
  fi
done
echo "签名完成：$DIR"
