#!/usr/bin/env bash
# 运行所有不依赖 Mac 和模型权重的测试。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
(cd "$ROOT/engine" && uv run ruff check . && uv run pytest -q)
(cd "$ROOT/workers/worker-kit" && uv run ruff check . && uv run pytest -q)
(cd "$ROOT/workers/speech" && uv run ruff check . && uv run pytest -q)
(cd "$ROOT/workers/analysis" && PYTHONPATH=src:../worker-kit/src uv run --isolated --no-project \
  --with pytest --with fastapi --with uvicorn --with numpy python -m pytest -q tests)
(cd "$ROOT/app" && pnpm install --frozen-lockfile && pnpm build)
(cd "$ROOT/app/src-tauri" && cargo test)
