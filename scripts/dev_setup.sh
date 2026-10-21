#!/usr/bin/env bash
# 建立开发用的基础环境 .dev/base-venv：主引擎 + 语音进程 + LLM 进程（mlx-lm）。
# Tauri 开发模式会用这个环境启动引擎（见 app/src-tauri/src/engine.rs）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.dev/base-venv"

# --managed-python：用 uv 自己下载管理的 Python，不依赖、也不改动本机已有的 Python
# --allow-existing：环境已存在时直接复用，重复执行本脚本可以更新依赖
uv venv --managed-python --allow-existing --python 3.13 "$VENV"
uv pip install --python "$VENV/bin/python" \
  -e "$ROOT/engine" \
  -e "$ROOT/workers/worker-kit" \
  -e "$ROOT/workers/speech"

if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
  # mlx-speech 已由语音进程的依赖带入；mlx-lm 提供本地 LLM 服务
  uv pip install --python "$VENV/bin/python" "mlx-lm>=0.32"
else
  echo "注意：当前不是 Apple Silicon Mac，mlx-speech 和 mlx-lm 没有安装，只能调试界面和引擎逻辑。"
fi

echo "基础环境已就绪：$VENV"
echo "下一步：cd app && pnpm install && pnpm tauri dev"
