#!/usr/bin/env bash
# 构建随 App 打包的基础运行环境：独立 Python 3.13 + 主引擎（+ Apple 芯片版本的本地模型运行环境）。
# 输出到 packaging/build/runtime/base，由 tauri.release.conf.json 打进 App 的 Resources/runtime。
#
#   ARCH=arm64（默认）  Apple 芯片版本：主引擎 + 语音进程（mlx-speech）+ mlx-lm，可以在本机运行模型
#   ARCH=x86_64         Intel 版本：只有主引擎，只能使用在线 API
#
# Apple 芯片版本必须在 Apple 芯片的 Mac 上构建；Intel 版本在 Intel Mac 上构建，
# 也可以在 Apple 芯片的 Mac 上构建（需要已安装 Rosetta）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/packaging/build/runtime"
PY_VERSION="${PY_VERSION:-3.13}"
ARCH="${ARCH:-arm64}"

[[ "$(uname -s)" == "Darwin" ]] || { echo "需要在 Mac 上构建"; exit 1; }
case "$ARCH" in
  arm64)
    [[ "$(uname -m)" == "arm64" ]] || { echo "Apple 芯片版本需要在 Apple 芯片的 Mac 上构建"; exit 1; }
    UV_ARCH=aarch64
    PACKAGES=("$ROOT/engine" "$ROOT/workers/worker-kit" "$ROOT/workers/speech" "mlx-lm>=0.32")
    CHECK="import shengjing_engine, shengjing_speech_worker, mlx_speech, mlx_lm"
    ;;
  x86_64)
    UV_ARCH=x86_64
    PACKAGES=("$ROOT/engine")
    CHECK="import platform, shengjing_engine; assert platform.machine() == 'x86_64'"
    ;;
  *)
    echo "ARCH 只能是 arm64 或 x86_64"; exit 1 ;;
esac

rm -rf "$OUT"
mkdir -p "$OUT"

# 1. 复制一份对应架构的独立 Python（python-build-standalone，可整体搬移）
PY_REQUEST="cpython-$PY_VERSION-macos-$UV_ARCH-none"
uv python install "$PY_REQUEST"
SRC_PY="$(uv python find "$PY_REQUEST")"
SRC_HOME="$(cd "$(dirname "$SRC_PY")/.." && pwd)"
cp -R "$SRC_HOME" "$OUT/base"
PY="$OUT/base/bin/python3"

# 2. 把依赖直接装进这份 Python（不用 venv，venv 里写死了绝对路径，不能搬移）
uv pip install --python "$PY" --break-system-packages "${PACKAGES[@]}"

# 3. 精简体积
find "$OUT/base" -name "__pycache__" -type d -prune -exec rm -rf {} +
find "$OUT/base" -path "*/site-packages/*/tests" -type d -prune -exec rm -rf {} +
rm -rf "$OUT/base/lib/python$PY_VERSION/test" "$OUT/base/lib/python$PY_VERSION/idlelib" \
       "$OUT/base/lib/python$PY_VERSION/tkinter" "$OUT/base/share"

# 4. ffmpeg：放一个对应架构（或通用）的 LGPL 构建（不要用 GPL 构建，否则整个 App 都要遵守 GPL）
#    优先用 packaging/vendor/ffmpeg-<架构>，没有就用 packaging/vendor/ffmpeg
FFMPEG="$ROOT/packaging/vendor/ffmpeg-$ARCH"
[[ -x "$FFMPEG" ]] || FFMPEG="$ROOT/packaging/vendor/ffmpeg"
if [[ -x "$FFMPEG" ]]; then
  if ! lipo -archs "$FFMPEG" | tr ' ' '\n' | grep -qx "$ARCH"; then
    echo "错误：$FFMPEG 不包含 $ARCH 架构（$(lipo -archs "$FFMPEG")）"; exit 1
  fi
  mkdir -p "$OUT/bin"
  cp "$FFMPEG" "$OUT/bin/ffmpeg"
else
  echo "警告：packaging/vendor/ffmpeg-$ARCH 和 packaging/vendor/ffmpeg 都不存在，App 里不会带 ffmpeg"
fi

"$PY" -c "$CHECK; print('runtime ok ($ARCH)')"
du -sh "$OUT/base"
