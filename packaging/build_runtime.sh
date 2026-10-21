#!/usr/bin/env bash
# 构建随 App 打包的基础运行环境：独立 Python 3.13 + 主引擎 + 语音进程 + mlx-lm。
# 输出到 packaging/build/runtime/base，由 tauri.release.conf.json 打进 App 的 Resources/runtime。
# 只能在 Apple Silicon Mac 上运行。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/packaging/build/runtime"
PY_VERSION="${PY_VERSION:-3.13}"

[[ "$(uname -s)-$(uname -m)" == "Darwin-arm64" ]] || { echo "需要 Apple Silicon Mac"; exit 1; }
rm -rf "$OUT/base"
mkdir -p "$OUT"

# 1. 复制一份独立的 Python（python-build-standalone，可整体搬移）
uv python install "$PY_VERSION"
SRC_PY="$(uv python find "$PY_VERSION")"
SRC_HOME="$(cd "$(dirname "$SRC_PY")/.." && pwd)"
cp -R "$SRC_HOME" "$OUT/base"
PY="$OUT/base/bin/python3"

# 2. 把依赖直接装进这份 Python（不用 venv，venv 里写死了绝对路径，不能搬移）
uv pip install --python "$PY" --break-system-packages \
  "$ROOT/engine" "$ROOT/workers/worker-kit" "$ROOT/workers/speech" "mlx-lm>=0.32"

# 3. 精简体积
find "$OUT/base" -name "__pycache__" -type d -prune -exec rm -rf {} +
find "$OUT/base" -path "*/site-packages/*/tests" -type d -prune -exec rm -rf {} +
rm -rf "$OUT/base/lib/python$PY_VERSION/test" "$OUT/base/lib/python$PY_VERSION/idlelib" \
       "$OUT/base/lib/python$PY_VERSION/tkinter" "$OUT/base/share"

# 4. ffmpeg：放一个 LGPL 构建（不要用 GPL 构建，否则整个 App 都要遵守 GPL）
if [[ -x "$ROOT/packaging/vendor/ffmpeg" ]]; then
  mkdir -p "$OUT/bin"
  cp "$ROOT/packaging/vendor/ffmpeg" "$OUT/bin/ffmpeg"
else
  echo "警告：packaging/vendor/ffmpeg 不存在，App 里不会带 ffmpeg"
fi

"$PY" -c "import shengjing_engine, shengjing_speech_worker, mlx_speech, mlx_lm; print('runtime ok')"
du -sh "$OUT/base"
