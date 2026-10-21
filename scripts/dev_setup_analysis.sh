#!/usr/bin/env bash
# 建立分析扩展包的开发环境 .dev/analysis-venv（PyTorch、pyannote、Qwen3-ForcedAligner、Demucs）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ "$(uname -s)" == "Darwin" && "$(uname -m)" != "arm64" ]]; then
  echo "这是 Intel 芯片的 Mac，不能在本机运行模型，不需要安装扩展包。请在 App 里使用在线 API。"
  exit 1
fi
VENV="$ROOT/.dev/analysis-venv"

# --managed-python：用 uv 自己下载管理的 Python，不依赖、也不改动本机已有的 Python
# --allow-existing：环境已存在时直接复用，重复执行本脚本可以更新依赖
uv venv --managed-python --allow-existing --python 3.13 "$VENV"
uv pip install --python "$VENV/bin/python" \
  -e "$ROOT/workers/worker-kit" \
  -e "$ROOT/workers/analysis"

echo "分析环境已就绪：$VENV"
echo "说话人区分需要先在 https://huggingface.co/pyannote/speaker-diarization-community-1 同意使用条款，"
echo "再在 App 的设置里填写 Hugging Face 访问令牌。"
