#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../.." && pwd)
MODELSCOPE_BIN=${MODELSCOPE_BIN:-"$REPO_ROOT/.venv-vllm/bin/modelscope"}
MODEL_ROOT=${MODEL_ROOT:-"$REPO_ROOT/models"}

if [[ ! -x "$MODELSCOPE_BIN" ]]; then
    echo "找不到 ModelScope CLI：$MODELSCOPE_BIN" >&2
    echo "请先执行：uv pip install --python .venv-vllm/bin/python modelscope" >&2
    exit 2
fi

mkdir -p "$MODEL_ROOT"

echo "[1/2] 下载较小的向量模型（命令可重复执行并续传）"
"$MODELSCOPE_BIN" download \
    --model Qwen/Qwen3-Embedding-0.6B \
    --local_dir "$MODEL_ROOT/Qwen3-Embedding-0.6B"

echo "[2/2] 下载 Qwen3.5-9B FP16 权重（命令可重复执行并续传）"
"$MODELSCOPE_BIN" download \
    --model Qwen/Qwen3.5-9B \
    --local_dir "$MODEL_ROOT/Qwen3.5-9B"

echo "模型下载完成：$MODEL_ROOT"
