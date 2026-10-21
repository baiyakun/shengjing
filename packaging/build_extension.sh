#!/usr/bin/env bash
# 构建扩展包：analysis（PyTorch 分析模型）或 music（ACE-Step）。
# 输出 packaging/build/extensions/<名称>-<版本>.tar.gz 和对应的 sha256，
# 上传到你自己的下载服务器后，把地址和校验值写进扩展包清单。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="${1:?用法：build_extension.sh analysis|music}"
VERSION="${VERSION:-0.1.0}"
WORK="$ROOT/packaging/build/ext-$NAME"
OUT="$ROOT/packaging/build/extensions"

[[ "$(uname -s)-$(uname -m)" == "Darwin-arm64" ]] || { echo "需要 Apple Silicon Mac"; exit 1; }
rm -rf "$WORK" && mkdir -p "$WORK" "$OUT"

case "$NAME" in
  analysis)
    PY_VERSION=3.13
    uv python install "$PY_VERSION"
    cp -R "$(cd "$(dirname "$(uv python find "$PY_VERSION")")/.." && pwd)" "$WORK/analysis"
    uv pip install --python "$WORK/analysis/bin/python3" --break-system-packages \
      "$ROOT/workers/worker-kit" "$ROOT/workers/analysis"
    "$WORK/analysis/bin/python3" -c "import shengjing_analysis_worker, torch, pyannote.audio"
    ;;
  music)
    PY_VERSION=3.12   # ACE-Step 要求 3.11–3.12
    uv python install "$PY_VERSION"
    cp -R "$(cd "$(dirname "$(uv python find "$PY_VERSION")")/.." && pwd)" "$WORK/music"
    REF="${ACESTEP_REF:?请设置 ACESTEP_REF 为验证过的 ACE-Step 提交}"
    git clone https://github.com/ace-step/ACE-Step-1.5.git "$WORK/src"
    git -C "$WORK/src" checkout "$REF"
    uv pip install --python "$WORK/music/bin/python3" --break-system-packages "$WORK/src"
    "$WORK/music/bin/python3" -c "import acestep.api_server"
    ;;
  *) echo "未知扩展包：$NAME"; exit 1 ;;
esac

find "$WORK/$NAME" -name "__pycache__" -type d -prune -exec rm -rf {} +
"$ROOT/packaging/sign_runtime.sh" "$WORK/$NAME"
ARCHIVE="$OUT/$NAME-$VERSION.tar.gz"
tar -C "$WORK" -czf "$ARCHIVE" "$NAME"
shasum -a 256 "$ARCHIVE" | tee "$ARCHIVE.sha256"
