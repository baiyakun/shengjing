#!/usr/bin/env bash
# 安装音乐扩展包的开发环境：克隆 ACE-Step 1.5，用它自己的 uv 配置建立 Python 3.11/3.12 环境。
set -euo pipefail
# 所有环境都用 uv 管理的 Python，不依赖本机已有的 Python
export UV_MANAGED_PYTHON=1
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/.dev/ACE-Step-1.5"
# 固定到一个验证过的版本（第 0 阶段确定后填写具体提交）
REF="${ACESTEP_REF:-main}"

if [[ ! -d "$DEST/.git" ]]; then
  git clone https://github.com/ace-step/ACE-Step-1.5.git "$DEST"
fi
git -C "$DEST" fetch --depth 1 origin "$REF"
git -C "$DEST" checkout FETCH_HEAD
(cd "$DEST" && uv sync)

echo "音乐环境已就绪：$DEST/.venv"
echo "Tauri 开发模式会自动使用它；单独运行引擎时设置："
echo "  export SHENGJING_PY_MUSIC=$DEST/.venv/bin/python"
